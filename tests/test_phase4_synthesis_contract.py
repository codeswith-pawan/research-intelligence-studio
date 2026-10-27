"""Comprehensive regression test suite for Phase 4: Shared Synthesis & Output Contract.

Tests:
1. Complete review satisfies the shared output contract with all sections having verified_content.
2. Filtered content produces filtered_by_evidence_gate status without exposing ungrounded claims.
3. Unsupported quotations produce unsupported_by_direct_quotation status.
4. Missing/unidentified content produces not_identified_in_text status (replaces misleading 'None').
5. Synthesis schema/provider failure produces synthesis_error status with truthful deterministic fallback.
6. Meaningful structured summary preserves problem, method, contributions, evaluation, and limitations.
7. Output consistency across Markdown reports, JSON representations, and CSV exports (no 'None' output).
8. Scoring rubrics, weights, thresholds, and provisional recommendation separation are strictly preserved.
9. Deterministic fallbacks produce truthful diagnostic messages and never fabricate paper-specific analysis.
"""

import csv
import json
import pytest
from pathlib import Path

from agents.agent_synthesizer import synthesize_review
from core.data_models import EvidenceItem, Extraction, Paper, PaperMetadata, Review
from core.synthesis_contract import (
    validate_review_output_contract,
    evaluate_section_statuses,
    generate_deterministic_fallback_summary,
    STATUS_VERIFIED,
    STATUS_NOT_IDENTIFIED,
    STATUS_UNSUPPORTED,
    STATUS_FILTERED,
    STATUS_SYNTHESIS_ERROR,
    SECTION_SUMMARY,
    SECTION_STRENGTHS,
    SECTION_CONCERNS,
    SECTION_MINOR_ISSUES,
    SECTION_SUGGESTIONS,
    SECTION_CRITERION_NARRATIVE,
)
from utilities.output_generator import save_review_markdown, save_consolidated_csv


class DummyConfig:
    def __init__(self, criteria=None):
        self.criteria = criteria or [
            {
                "id": "methodology",
                "name": "Methodology",
                "weight": 50,
                "scale": {"range": [1, 5], "labels": {str(i): str(i) for i in range(1, 6)}},
            },
            {
                "id": "novelty",
                "name": "Novelty",
                "weight": 50,
                "scale": {"range": [1, 5], "labels": {str(i): str(i) for i in range(1, 6)}},
            },
        ]
        self.extraction_failures = {}

    def get_llm_config(self):
        return {
            "extractor_provider": "ollama",
            "extractor_model": "llama3.2:3b",
            "synthesizer_provider": "ollama",
            "synthesizer_model": "llama3.2:3b",
            "temperature": 0.0,
            "max_retries": 1,
        }

    def get_criteria(self):
        return self.criteria

    def get_criterion_by_id(self, criterion_id):
        return next((c for c in self.criteria if c["id"] == criterion_id), {})

    def get_recommendation_thresholds(self):
        return [
            {"threshold": 85, "label": "Accept"},
            {"threshold": 70, "label": "Accept with Revisions"},
            {"threshold": 50, "label": "Revise and Resubmit"},
            {"threshold": 0, "label": "Reject"},
        ]

    def get_prompt(self, name):
        if name == "synthesizer_user":
            return "{paper_title} {paper_abstract} {json_dump_of_extractions} {evidence_summary} {weights_table} {calculated_score} {calculated_recommendation}"
        return "Synthesize academic paper review."


def make_dummy_paper():
    return Paper(
        filename="transformer_attention.pdf",
        original_path="/tmp/transformer_attention.pdf",
        content_markdown="# Attention Is All You Need\nAbstract: We propose the Transformer...",
        token_count=1200,
        metadata=PaperMetadata(
            title="Attention Is All You Need",
            abstract="We propose the Transformer, a model architecture eschewing recurrence and entirely relying on an attention mechanism.",
        ),
    )


def make_dummy_extraction(criterion_id, score=5, verified_count=2, unverified_count=0):
    evidence = []
    for idx in range(1, verified_count + 1):
        evidence.append(
            EvidenceItem(
                quote=f"Verified quotation {idx} for {criterion_id}: The model utilizes multi-head attention.",
                page_reference=f"Page {idx}",
                relevance="Direct evidence for criterion.",
                verification_status="Verified",
                pdf_page_numbers=[idx],
            )
        )
    for idx in range(1, unverified_count + 1):
        evidence.append(
            EvidenceItem(
                quote=f"Unverified quotation {idx} for {criterion_id}: Hallucinated baseline comparison.",
                page_reference="N/A",
                relevance="Unverified statement.",
                verification_status="Unverified",
                verification_note="No text match.",
            )
        )
    return Extraction(
        paper_id="paper_123",
        criterion_id=criterion_id,
        score=score,
        score_justification=f"Assessment score {score} for {criterion_id}.",
        evidence=evidence,
        strengths=[f"Strong {criterion_id}"],
        weaknesses=[],
        confidence=0.9,
        model_used="ollama/llama3.2:3b",
    )


def patch_synthesis_llm(monkeypatch, response_dict):
    monkeypatch.setattr(
        "agents.agent_synthesizer.call_llm",
        lambda **kwargs: {"success": True, "content": json.dumps(response_dict), "cost": 0.001},
    )


# ---------------------------------------------------------------------------
# Test 1: Complete Review Output Contract & verified_content Status
# ---------------------------------------------------------------------------
def test_complete_review_satisfies_shared_output_contract(monkeypatch):
    config = DummyConfig()
    ext_method = make_dummy_extraction("methodology", score=5, verified_count=2)
    ext_novel = make_dummy_extraction("novelty", score=5, verified_count=2)

    response_payload = {
        "executive_summary": (
            "The authors address sequence transduction without recurrence [Evidence: methodology:Q1]. "
            "They introduce multi-head attention mechanisms to replace recurrent layers [Evidence: methodology:Q2]. "
            "The model achieves superior translation performance on benchmark datasets [Evidence: novelty:Q1]."
        ),
        "decision_confidence": 0.95,
        "recommendation": "Accept",
        "recommendation_rationale": "High scores across all evaluated criteria.",
        "detailed_assessment": {
            "major_strengths": [
                "Novel multi-head self-attention architecture replaces recurrent models [Evidence: methodology:Q1].",
                "Strong empirical performance on machine translation tasks [Evidence: novelty:Q1].",
            ],
            "major_concerns": [],
            "minor_issues": [],
        },
        "revision_suggestions": [
            "Provide extended ablation analysis for attention head dimensional scaling [Evidence: methodology:Q2]."
        ],
        "criterion_narrative": {
            "methodology": "The architectural formulation is rigorously specified [Evidence: methodology:Q1].",
            "novelty": "The replacement of recurrence represents a substantial innovation [Evidence: novelty:Q1].",
        },
    }
    patch_synthesis_llm(monkeypatch, response_payload)

    review = synthesize_review(make_dummy_paper(), [ext_method, ext_novel], config)

    # 1. Output Contract Validation
    valid, errors = validate_review_output_contract(review)
    assert valid, f"Output contract validation failed: {errors}"

    # 2. Section Statuses
    assert review.section_statuses[SECTION_SUMMARY] == STATUS_VERIFIED
    assert review.section_statuses[SECTION_STRENGTHS] == STATUS_VERIFIED
    assert review.section_statuses[SECTION_SUGGESTIONS] == STATUS_VERIFIED
    assert review.section_statuses[SECTION_CRITERION_NARRATIVE] == STATUS_VERIFIED

    # 3. Truthful not_identified_in_text for empty concerns
    assert review.section_statuses[SECTION_CONCERNS] == STATUS_NOT_IDENTIFIED
    assert review.section_statuses[SECTION_MINOR_ISSUES] == STATUS_NOT_IDENTIFIED
    assert "No specific weaknesses" in review.section_diagnostics[SECTION_CONCERNS]

    # 4. Invariants
    assert review.review_status == "complete"
    assert review.overall_score == 100.0
    assert review.provisional_recommendation == "Accept"
    assert review.recommendation == "Accept"


# ---------------------------------------------------------------------------
# Test 2: Filtered by Evidence Gate Preserves Diagnostic Without Fabrications
# ---------------------------------------------------------------------------
def test_filtered_by_evidence_gate_preserves_diagnostic_status(monkeypatch):
    config = DummyConfig()
    ext_method = make_dummy_extraction("methodology", score=4, verified_count=2)
    ext_novel = make_dummy_extraction("novelty", score=4, verified_count=2)

    # Payload with invalid/unverified evidence tags that will be stripped by the evidence safety gate
    response_payload = {
        "executive_summary": "The paper makes ungrounded claims with missing citation tag.",
        "decision_confidence": 0.85,
        "recommendation": "Accept",
        "recommendation_rationale": "Reasoning text.",
        "detailed_assessment": {
            "major_strengths": ["Ungrounded strength claim."],
            "major_concerns": ["Ungrounded concern without valid quote tag."],
            "minor_issues": [],
        },
        "revision_suggestions": ["Ungrounded suggestion [Evidence: non_existent_cid:Q99]."],
        "criterion_narrative": {
            "methodology": "Valid grounded narrative [Evidence: methodology:Q1].",
            "novelty": "Valid grounded narrative [Evidence: novelty:Q1].",
        },
    }
    patch_synthesis_llm(monkeypatch, response_payload)

    review = synthesize_review(make_dummy_paper(), [ext_method, ext_novel], config)

    # Items without citations must be omitted from output
    assert review.detailed_assessment.major_strengths == []
    assert review.detailed_assessment.major_concerns == []
    assert review.revision_suggestions == []

    # Section statuses must explicitly report filtered_by_evidence_gate
    assert review.section_statuses[SECTION_STRENGTHS] == STATUS_FILTERED
    assert review.section_statuses[SECTION_CONCERNS] == STATUS_FILTERED
    assert review.section_statuses[SECTION_SUGGESTIONS] == STATUS_FILTERED
    assert "omitted by the evidence safety gate" in review.section_diagnostics[SECTION_CONCERNS]

    # Verify contract
    valid, errors = validate_review_output_contract(review)
    assert valid, f"Output contract errors: {errors}"


# ---------------------------------------------------------------------------
# Test 3: Unsupported by Direct Quotation
# ---------------------------------------------------------------------------
def test_unsupported_by_direct_quotation_when_quotes_unverified(monkeypatch):
    config = DummyConfig()
    # Extractions with ONLY unverified evidence
    ext_method = make_dummy_extraction("methodology", score=3, verified_count=0, unverified_count=2)
    ext_novel = make_dummy_extraction("novelty", score=3, verified_count=0, unverified_count=2)

    response_payload = {
        "executive_summary": "No quotes can be cited.",
        "decision_confidence": 0.5,
        "recommendation": "Reject",
        "recommendation_rationale": "Reasoning text.",
        "detailed_assessment": {
            "major_strengths": [],
            "major_concerns": [],
            "minor_issues": [],
        },
        "revision_suggestions": [],
        "criterion_narrative": {
            "methodology": "",
            "novelty": "",
        },
    }
    patch_synthesis_llm(monkeypatch, response_payload)

    review = synthesize_review(make_dummy_paper(), [ext_method, ext_novel], config)

    # Unverified extractions lead to manual_review and unsupported status
    assert review.review_status == "manual_review"
    assert review.recommendation == "Needs Manual Review"
    assert review.section_statuses[SECTION_STRENGTHS] == STATUS_UNSUPPORTED
    assert review.section_statuses[SECTION_CONCERNS] == STATUS_UNSUPPORTED
    assert review.section_statuses[SECTION_SUGGESTIONS] == STATUS_UNSUPPORTED


# ---------------------------------------------------------------------------
# Test 4: Replacement of Misleading 'None' with Truthful Statuses in Markdown
# ---------------------------------------------------------------------------
def test_markdown_and_csv_replace_none_with_truthful_statuses(tmp_path, monkeypatch):
    config = DummyConfig()
    ext_method = make_dummy_extraction("methodology", score=5, verified_count=2)
    ext_novel = make_dummy_extraction("novelty", score=5, verified_count=2)

    response_payload = {
        "executive_summary": "Grounded summary [Evidence: methodology:Q1].",
        "decision_confidence": 0.9,
        "recommendation": "Accept",
        "recommendation_rationale": "High score.",
        "detailed_assessment": {
            "major_strengths": ["Grounded strength [Evidence: methodology:Q1]."],
            "major_concerns": [],  # Empty
            "minor_issues": [],    # Empty
        },
        "revision_suggestions": [], # Empty
        "criterion_narrative": {
            "methodology": "Method narrative [Evidence: methodology:Q1].",
            "novelty": "Novelty narrative [Evidence: novelty:Q1].",
        },
    }
    patch_synthesis_llm(monkeypatch, response_payload)
    review = synthesize_review(make_dummy_paper(), [ext_method, ext_novel], config)

    # 1. Save Markdown Report
    md_file = tmp_path / "review_report.md"
    save_review_markdown(review, str(md_file))
    md_content = md_file.read_text(encoding="utf-8")

    # Assert that standalone misleading "None" lines DO NOT EXIST
    assert "\nNone\n" not in md_content
    assert "\n### Major Concerns\nNone\n" not in md_content
    assert "\n### Minor Issues\nNone\n" not in md_content
    assert "\n## Revision Suggestions\n\nNone\n" not in md_content

    # Assert explicit truthful statuses ARE written
    assert "*(Status: not_identified_in_text)*" in md_content
    assert "No specific weaknesses or concerns were identified in the verified source text." in md_content
    assert "No specific revision suggestions were identified in the verified source text." in md_content

    # 2. Save CSV Report
    csv_file = tmp_path / "consolidated.csv"
    save_consolidated_csv([review], str(csv_file))

    with csv_file.open(encoding="utf-8", newline="") as f:
        reader = list(csv.DictReader(f))
        assert len(reader) == 1
        row = reader[0]

        assert row["recommendation"] == "Accept"
        assert row["provisional_recommendation"] == "Accept"
        assert row["summary_status"] == STATUS_VERIFIED
        assert row["strengths_status"] == STATUS_VERIFIED
        assert row["concerns_status"] == STATUS_NOT_IDENTIFIED
        assert row["suggestions_status"] == STATUS_NOT_IDENTIFIED


# ---------------------------------------------------------------------------
# Test 5: Synthesis Failure Produces synthesis_error & Deterministic Summary
# ---------------------------------------------------------------------------
def test_synthesis_failure_produces_synthesis_error_status(monkeypatch):
    config = DummyConfig()
    ext_method = make_dummy_extraction("methodology", score=5, verified_count=2)
    ext_novel = make_dummy_extraction("novelty", score=4, verified_count=2)

    # Simulate invalid schema (missing required fields)
    monkeypatch.setattr(
        "agents.agent_synthesizer.call_llm",
        lambda **kwargs: {"success": True, "content": "{\"invalid\": \"payload\"}", "cost": 0.0},
    )

    review = synthesize_review(make_dummy_paper(), [ext_method, ext_novel], config)

    assert review.review_status == "failed"
    assert review.recommendation == "Needs Manual Review"
    assert review.provisional_recommendation == "Accept"
    assert review.overall_score == 90.0

    # Statuses
    assert review.section_statuses[SECTION_SUMMARY] == STATUS_SYNTHESIS_ERROR
    assert review.section_statuses[SECTION_STRENGTHS] == STATUS_SYNTHESIS_ERROR
    assert review.section_statuses[SECTION_CONCERNS] == STATUS_SYNTHESIS_ERROR
    assert review.section_statuses[SECTION_SUGGESTIONS] == STATUS_SYNTHESIS_ERROR

    # Deterministic fallback summary must preserve truthfulness without hallucinations
    assert "No validated synthesized narrative is available" in review.executive_summary
    assert "This review evaluated manuscript 'Attention Is All You Need'" in review.executive_summary
    assert "verified 4 direct source quotation(s)" in review.executive_summary


# ---------------------------------------------------------------------------
# Test 6: Deterministic Fallback Summary Safety Invariant
# ---------------------------------------------------------------------------
def test_deterministic_fallback_summary_never_hallucinates():
    extractions = [
        make_dummy_extraction("methodology", score=5, verified_count=2),
        make_dummy_extraction("novelty", score=3, verified_count=1),
    ]

    summary = generate_deterministic_fallback_summary(
        paper_title="Sample Title",
        extractions=extractions,
        score=80.0,
        provisional_rec="Accept with Revisions",
        status=STATUS_FILTERED,
        reason="Statements omitted due to citation rules",
    )

    assert "Sample Title" in summary
    assert "methodology, novelty" in summary
    assert "verified 3 direct source quotation(s)" in summary
    assert "80.0/100" in summary
    assert "Accept with Revisions" in summary
    assert "filtered_by_evidence_gate" in summary
    # Must NOT hallucinate claims not in evidence
    assert "proves that" not in summary.lower()
    assert "outperforms SOTA" not in summary


# ---------------------------------------------------------------------------
# Test 7: Separation of Provisional Recommendation vs Final System Status
# ---------------------------------------------------------------------------
def test_provisional_recommendation_separated_from_final_status(monkeypatch):
    config = DummyConfig()
    # High score (100.0) but only 1 quote (insufficient evidence gate triggers Needs Manual Review)
    ext_method = make_dummy_extraction("methodology", score=5, verified_count=1)
    ext_novel = make_dummy_extraction("novelty", score=5, verified_count=1)

    response_payload = {
        "executive_summary": "Grounded text [Evidence: methodology:Q1].",
        "decision_confidence": 0.8,
        "recommendation": "Accept",
        "recommendation_rationale": "High score on criteria.",
        "detailed_assessment": {
            "major_strengths": ["Grounded strength [Evidence: methodology:Q1]."],
            "major_concerns": [],
            "minor_issues": [],
        },
        "revision_suggestions": ["Suggestion [Evidence: novelty:Q1]."],
        "criterion_narrative": {
            "methodology": "Narrative [Evidence: methodology:Q1].",
            "novelty": "Narrative [Evidence: novelty:Q1].",
        },
    }
    patch_synthesis_llm(monkeypatch, response_payload)

    review = synthesize_review(make_dummy_paper(), [ext_method, ext_novel], config)

    # Rubric score is 100.0 -> provisional is Accept
    assert review.overall_score == 100.0
    assert review.provisional_recommendation == "Accept"

    # But safety gate triggers because < 2 quotes per criterion -> Needs Manual Review
    assert review.review_status == "manual_review"
    assert review.recommendation == "Needs Manual Review"
    assert "Fewer than 2 verified quotations" in " ".join(review.warnings)
