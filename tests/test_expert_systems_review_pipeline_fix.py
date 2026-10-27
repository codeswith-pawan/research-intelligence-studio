"""Regression test suite for the Expert Systems With Applications pipeline fix.

Validates:
1. Soft-hyphen (\u00ad) and typographical quote normalization in PDF quote verification.
2. Synthesis resilience when a criterion with verified evidence has an empty or omitted narrative.
3. Retention of diagnostic score and provisional recommendation without converting to rejection.
4. Strict rejection of genuine hallucinations/paraphrases.
"""

import json
from dataclasses import dataclass
from typing import List

import pytest

from agents.agent_extractor import _find_source_substring
from agents.agent_synthesizer import synthesize_review
from core.data_models import Paper, PaperMetadata, Extraction, EvidenceItem
from core.config_loader import Config


class FakeConfig:
    def __init__(self, criteria):
        self._criteria = criteria

    def get_criteria(self):
        return self._criteria

    def get_criterion_by_id(self, criterion_id):
        for crit in self._criteria:
            if crit.get("id") == criterion_id:
                return crit
        return None

    def get_prompt(self, name):
        if name == "synthesizer_system":
            return "You are an academic synthesis system."
        return "Review this paper: {paper_title}\n{json_dump_of_extractions}\n{evidence_summary}\n{weights_table}\n{calculated_score}\n{calculated_recommendation}"

    def get_llm_config(self):
        return {
            "extractor_provider": "ollama",
            "extractor_model": "llama3.2:3b",
            "synthesizer_provider": "ollama",
            "synthesizer_model": "llama3.2:3b",
            "temperature": 0.0,
            "max_retries": 1,
        }

    def get_recommendation_thresholds(self):
        return [
            {"threshold": 80.0, "label": "Accept"},
            {"threshold": 65.0, "label": "Minor Revision"},
            {"threshold": 50.0, "label": "Revise and Resubmit"},
            {"threshold": 0.0, "label": "Reject"},
        ]


def _make_criterion(cid: str, weight: float) -> dict:
    return {
        "id": cid,
        "name": cid.replace("_", " ").title(),
        "weight": weight,
        "description": f"Assessment of {cid}",
        "sub_questions": [f"Does the paper satisfy {cid}?"],
        "scale": {"range": [1, 5], "labels": {str(i): str(i) for i in range(1, 6)}},
    }


def _make_evidence(cid: str, q_idx: int, verified: bool = True) -> EvidenceItem:
    return EvidenceItem(
        quote=f"Source quote {q_idx} for {cid}.",
        page_reference=f"PDF Page {q_idx}",
        relevance=f"Relevance for {cid}",
        verification_status="Verified" if verified else "Unverified",
        pdf_page_numbers=[q_idx] if verified else [],
    )


def _make_paper(title="Expert Systems With Applications"):
    return Paper(
        id="test-expert-systems-id",
        filename="Expert_System_with_applications.pdf",
        original_path="/tmp/Expert_System_with_applications.pdf",
        content_markdown="# Expert Systems With Applications\nFull text of the paper...",
        token_count=100,
        metadata=PaperMetadata(title=title, abstract="Abstract text..."),
    )


# ---------------------------------------------------------------------------
# Task 3: Quote Verification Normalization Tests
# ---------------------------------------------------------------------------

def test_soft_hyphen_line_break_normalization():
    """Verify that soft-hyphens (\\u00ad\\n) at line breaks in PDFs match clean quotes."""
    source_pdf_text = "We focus on empirical studies because they go beyond conceptual un\xad\nderstanding and provide empirical evidence."
    clean_quote = "We focus on empirical studies because they go beyond conceptual understanding and provide empirical evidence."

    matched = _find_source_substring(source_pdf_text, clean_quote)
    assert matched is not None
    assert "conceptual un\xad\nderstanding" in matched


def test_typographical_smart_quote_normalization():
    """Verify that curly smart quotes (\u201c...\u201d) in PDF match straight ASCII quotes."""
    source_pdf_text = 'The term \u201cperspectives\u201d emerges within this cluster, indicating multiple angles.'
    ascii_quote = 'The term "perspectives" emerges within this cluster, indicating multiple angles.'

    matched = _find_source_substring(source_pdf_text, ascii_quote)
    assert matched is not None
    assert "\u201cperspectives\u201d" in matched


def test_quote_verification_preserves_strictness_on_paraphrases():
    """Verify that semantic paraphrases or altered words are still strictly rejected."""
    source_pdf_text = "The primary goal of AI is to create intelligent systems capable of intelligent behaviors."
    altered_quote = "The ultimate goal of AI is to design intelligent systems capable of intelligent behaviors."

    matched = _find_source_substring(source_pdf_text, altered_quote)
    assert matched is None


# ---------------------------------------------------------------------------
# Task 2 & 4: Synthesis Resilience and Score Preservation
# ---------------------------------------------------------------------------

def test_reproduce_data_quality_empty_narrative_handled_gracefully(monkeypatch):
    """Reproduce the exact Expert Systems failure where data_quality narrative was empty.

    Verifies:
    1. Synthesis does not crash with ValueError.
    2. Review status succeeds without turning into a rejected paper.
    3. Diagnostic score (51.2) is preserved.
    4. Provisional recommendation ('Revise and Resubmit') is preserved.
    5. Criterion narrative for data_quality receives a non-empty fallback citing the evidence.
    6. Detailed assessment strengths/concerns are preserved.
    """
    config = FakeConfig([
        _make_criterion("empirical_rigor", 18.0),
        _make_criterion("data_quality", 10.0),
        _make_criterion("theoretical_contribution", 12.0),
    ])

    extractions = [
        Extraction(
            paper_id="test-expert-systems-id",
            criterion_id="empirical_rigor",
            score=3,
            score_justification="Empirical studies focus",
            evidence=[_make_evidence("empirical_rigor", 1, verified=True), _make_evidence("empirical_rigor", 2, verified=True)],
            strengths=["Focuses on empirical studies [Evidence: empirical_rigor:Q1]."],
            weaknesses=[],
            confidence=0.8,
            cost=0.001,
            model_used="ollama/llama3.2:3b",
        ),
        Extraction(
            paper_id="test-expert-systems-id",
            criterion_id="data_quality",
            score=3,
            score_justification="Mixed methods potential",
            evidence=[_make_evidence("data_quality", 1, verified=True), _make_evidence("data_quality", 2, verified=True)],
            strengths=["Identifies opportunities for high quality research [Evidence: data_quality:Q1]."],
            weaknesses=[],
            confidence=0.8,
            cost=0.001,
            model_used="ollama/llama3.2:3b",
        ),
        Extraction(
            paper_id="test-expert-systems-id",
            criterion_id="theoretical_contribution",
            score=3,
            score_justification="Conceptual review",
            evidence=[_make_evidence("theoretical_contribution", 1, verified=True), _make_evidence("theoretical_contribution", 2, verified=True)],
            strengths=["Covers conceptual structure [Evidence: theoretical_contribution:Q1]."],
            weaknesses=[],
            confidence=0.8,
            cost=0.001,
            model_used="ollama/llama3.2:3b",
        ),
    ]

    # Model payload replicating the exact failing response where data_quality was empty string
    failing_response = {
        "executive_summary": "Systematic review of AI in education [Evidence: empirical_rigor:Q1].",
        "decision_confidence": 0.8,
        "recommendation": "Revise and Resubmit",
        "recommendation_rationale": "Moderate score across empirical and data dimensions.",
        "detailed_assessment": {
            "major_strengths": ["Comprehensive bibliometric analysis [Evidence: empirical_rigor:Q1]."],
            "major_concerns": ["Limited econometric depth [Evidence: empirical_rigor:Q1]."],
            "minor_issues": [],
        },
        "revision_suggestions": ["Integrate formal theoretical models [Evidence: theoretical_contribution:Q1]."],
        "criterion_narrative": {
            "empirical_rigor": "Empirical studies are emphasized [Evidence: empirical_rigor:Q1].",
            "data_quality": "",  # The exact empty string that previously raised ValueError!
            "theoretical_contribution": "Theoretical frameworks reviewed [Evidence: theoretical_contribution:Q1].",
        },
    }

    monkeypatch.setattr(
        "agents.agent_synthesizer.call_llm",
        lambda *args, **kwargs: {"content": json.dumps(failing_response), "cost": 0.005, "success": True},
    )

    review = synthesize_review(_make_paper(), extractions, config)

    # 1. Pipeline did NOT crash
    assert review is not None
    assert review.review_status == "complete"

    # 2. Score preserved
    # score: 3/5 on all criteria = 60.0/100
    assert review.overall_score == pytest.approx(60.0)
    assert review.provisional_recommendation == "Revise and Resubmit"

    # 3. data_quality explanation is non-empty and cites verified evidence
    assert "data_quality" in review.criterion_narrative
    dq_narrative = review.criterion_narrative["data_quality"]
    assert len(dq_narrative.strip()) > 0
    assert "[Evidence: data_quality:Q1]" in dq_narrative

    # 4. Detailed assessment preserved and NOT replaced by synthesis_error
    assert len(review.detailed_assessment.major_strengths) == 1
    assert "Comprehensive bibliometric analysis" in review.detailed_assessment.major_strengths[0]
    assert len(review.detailed_assessment.major_concerns) == 1


def test_fatal_synthesis_error_preserves_score_and_sets_needs_manual_review(monkeypatch):
    """When synthesis completely fails (e.g. unparseable JSON), verify diagnostic score

    is preserved, provisional recommendation is kept, and recommendation becomes
    Needs Manual Review (NOT Reject).
    """
    config = FakeConfig([
        _make_criterion("empirical_rigor", 50.0),
        _make_criterion("data_quality", 50.0),
    ])

    extractions = [
        Extraction(
            paper_id="test-id",
            criterion_id="empirical_rigor",
            score=4,
            score_justification="Solid",
            evidence=[_make_evidence("empirical_rigor", 1, verified=True), _make_evidence("empirical_rigor", 2, verified=True)],
            strengths=[],
            weaknesses=[],
            confidence=0.85,
            model_used="ollama/llama3.2:3b",
        ),
        Extraction(
            paper_id="test-id",
            criterion_id="data_quality",
            score=3,
            score_justification="Fair",
            evidence=[_make_evidence("data_quality", 1, verified=True), _make_evidence("data_quality", 2, verified=True)],
            strengths=[],
            weaknesses=[],
            confidence=0.85,
            model_used="ollama/llama3.2:3b",
        ),
    ]

    # Completely broken JSON from provider
    monkeypatch.setattr(
        "agents.agent_synthesizer.call_llm",
        lambda *args, **kwargs: {"content": "INVALID JSON <<< >>>", "cost": 0.0, "success": True},
    )

    review = synthesize_review(_make_paper(), extractions, config)

    # Review status records failure, but does NOT turn into a paper rejection
    assert review.review_status == "failed"
    assert review.recommendation == "Needs Manual Review"
    # Weighted score: (4/5 * 50) + (3/5 * 50) = 40 + 30 = 70.0
    assert review.overall_score == 70.0
    assert review.provisional_recommendation == "Minor Revision"
    assert "diagnostic weighted score is 70.0/100" in review.recommendation_rationale
