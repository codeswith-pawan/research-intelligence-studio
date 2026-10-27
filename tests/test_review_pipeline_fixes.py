import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List

import pymupdf
import pytest
from starlette.testclient import TestClient

from agents.agent_extractor import extract_criterion_evidence
from agents.agent_synthesizer import synthesize_review
from core.data_models import Paper, PaperMetadata, Extraction, EvidenceItem
from core.config_loader import Config
from utilities.output_generator import save_review_markdown, save_consolidated_csv
from web_app import app, _find_run_dirs, _parse_review_metadata_from_text


class MockConfig:
    domain = "development_economics"

    def __init__(self, criteria=None):
        self.extraction_failures = {}
        self._criteria = criteria or [
            {
                "id": "theoretical_contribution",
                "name": "Theoretical Contribution",
                "description": "Evaluates the paper's advancement of theoretical understanding.",
                "weight": 50,
                "scale": {"type": "numeric", "range": [1, 5], "labels": {}},
            },
            {
                "id": "empirical_rigor",
                "name": "Empirical Rigor",
                "description": "Assesses empirical methods and data quality.",
                "weight": 50,
                "scale": {"type": "numeric", "range": [1, 5], "labels": {}},
            },
        ]

    def get_criteria(self) -> List[Dict[str, Any]]:
        return self._criteria

    def get_criterion_by_id(self, cid: str) -> Dict[str, Any]:
        for c in self._criteria:
            if c.get("id") == cid:
                return c
        return None

    def get_recommendation_thresholds(self) -> List[Dict[str, Any]]:
        return [
            {"threshold": 85, "label": "Accept"},
            {"threshold": 70, "label": "Accept with Revisions"},
            {"threshold": 50, "label": "Revise and Resubmit"},
            {"threshold": 0, "label": "Reject"},
        ]

    def get_llm_config(self) -> Dict[str, Any]:
        return {
            "extractor_provider": "ollama",
            "extractor_model": "llama3.2:3b",
            "synthesizer_provider": "ollama",
            "synthesizer_model": "llama3.2:3b",
            "temperature": 0.2,
            "max_retries": 1,
        }

    def get_prompt(self, name: str) -> str:
        prompt_dir = Path(__file__).resolve().parents[1] / "config/prompts"
        path = prompt_dir / f"{name}.txt"
        if path.is_file():
            return path.read_text(encoding="utf-8")
        return ""

    def get_system_config(self) -> Dict[str, Any]:
        return {"max_content_tokens": 10000}


def create_fixture_paper(tmp_path: Path, filename="sample_paper.pdf", text=None) -> Paper:
    pdf_path = tmp_path / filename
    source_text = text or (
        "We propose a new simple network architecture, the Transformer, based solely on attention mechanisms. "
        "The dominant sequence transduction models are based on complex recurrent or convolutional neural networks. "
        "Experiments on two machine translation tasks show these models to be superior in quality."
    )
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), source_text)
        doc.save(str(pdf_path))

    return Paper(
        id="fixture-paper-001",
        filename=filename,
        original_path=str(pdf_path),
        content_markdown=source_text,
        token_count=len(source_text.split()),
        metadata=PaperMetadata(
            title="Sample Transformer Manuscript",
            abstract="Abstract of sample paper.",
        ),
    )


# 1. Valid extraction response produces criterion records
def test_valid_extraction_response_produces_criterion_records(tmp_path, monkeypatch):
    paper = create_fixture_paper(tmp_path)
    config = MockConfig()
    criterion = config.get_criteria()[0]

    valid_payload = json.dumps({
        "score": 4,
        "score_justification": "Strong theoretical foundation based on attention mechanisms.",
        "evidence": [{
            "quote": "We propose a new simple network architecture, the Transformer, based solely on attention mechanisms.",
            "page_reference": "PDF Page 1",
            "relevance": "Direct statement of the architecture novelty.",
        }],
        "strengths": ["Clear architectural novelty"],
        "weaknesses": ["Relies heavily on specialized hardware"],
        "confidence": 0.88,
    })

    monkeypatch.setattr(
        "agents.agent_extractor.call_llm",
        lambda **kwargs: {"success": True, "content": valid_payload, "cost": 0.0},
    )

    result = extract_criterion_evidence(paper, criterion, config)

    assert result is not None
    assert result.criterion_id == "theoretical_contribution"
    assert result.score == 4
    assert result.confidence == 0.88
    assert len(result.evidence) == 1
    assert result.evidence[0].verification_status == "Verified"
    assert result.evidence[0].pdf_page_numbers == [1]
    assert "Transformer" in result.evidence[0].quote


# 2. Malformed JSON produces an informative, criterion-specific error
def test_malformed_json_produces_informative_criterion_specific_error(tmp_path, monkeypatch):
    paper = create_fixture_paper(tmp_path)
    config = MockConfig()
    criterion = config.get_criteria()[0]

    monkeypatch.setattr(
        "agents.agent_extractor.call_llm",
        lambda **kwargs: {"success": True, "content": "<<<NOT VALID JSON>>><broken payload}", "cost": 0.0},
    )

    result = extract_criterion_evidence(paper, criterion, config)

    assert result is None
    assert "theoretical_contribution" in config.extraction_failures
    error_msg = config.extraction_failures["theoretical_contribution"]
    assert "theoretical_contribution" in error_msg
    assert "JSON parse error" in error_msg or "Invalid response format" in error_msg


# 3. Missing or wrongly typed fields are rejected safely
def test_missing_or_wrongly_typed_fields_are_rejected_safely(tmp_path, monkeypatch):
    paper = create_fixture_paper(tmp_path)
    config = MockConfig()
    criterion = config.get_criteria()[0]

    # Subtest 3A: Missing field (score_justification missing)
    missing_field_payload = json.dumps({
        "score": 4,
        "evidence": [],
        "strengths": ["Novel approach"],
        "weaknesses": [],
        "confidence": 0.9,
    })
    monkeypatch.setattr(
        "agents.agent_extractor.call_llm",
        lambda **kwargs: {"success": True, "content": missing_field_payload, "cost": 0.0},
    )
    res_missing = extract_criterion_evidence(paper, criterion, config)
    assert res_missing is None
    assert "score_justification" in config.extraction_failures["theoretical_contribution"]

    # Subtest 3B: Wrong type (score as string instead of int)
    wrong_type_payload = json.dumps({
        "score": "four",
        "score_justification": "Valid justification text.",
        "evidence": [],
        "strengths": ["Novel approach"],
        "weaknesses": [],
        "confidence": 0.9,
    })
    monkeypatch.setattr(
        "agents.agent_extractor.call_llm",
        lambda **kwargs: {"success": True, "content": wrong_type_payload, "cost": 0.0},
    )
    res_type = extract_criterion_evidence(paper, criterion, config)
    assert res_type is None
    assert "score" in config.extraction_failures["theoretical_contribution"]


# 4. Valid quotations are retained and matched against source text; unmatched quotations are rejected
def test_valid_quotations_retained_and_unmatched_quotations_rejected(tmp_path, monkeypatch):
    paper = create_fixture_paper(tmp_path)
    config = MockConfig()
    criterion = config.get_criteria()[0]

    valid_quote = "We propose a new simple network architecture, the Transformer, based solely on attention mechanisms."
    unmatched_quote = "This paper presents completely fabricated experimental data that never appeared in the text."

    mixed_payload = json.dumps({
        "score": 4,
        "score_justification": "Evidence provided.",
        "evidence": [
            {"quote": valid_quote, "page_reference": "PDF Page 1", "relevance": "Valid support"},
            {"quote": unmatched_quote, "page_reference": "PDF Page 1", "relevance": "Fake quote"},
        ],
        "strengths": ["Novel architecture"],
        "weaknesses": [],
        "confidence": 0.85,
    })

    monkeypatch.setattr(
        "agents.agent_extractor.call_llm",
        lambda **kwargs: {"success": True, "content": mixed_payload, "cost": 0.0},
    )

    result = extract_criterion_evidence(paper, criterion, config)
    assert result is not None
    assert len(result.evidence) == 2

    # Verified item
    assert result.evidence[0].quote == valid_quote
    assert result.evidence[0].verification_status == "Verified"
    assert result.evidence[0].pdf_page_numbers == [1]
    assert result.evidence[0].verification_note is None

    # Unverified / rejected item
    assert result.evidence[1].quote == unmatched_quote
    assert result.evidence[1].verification_status == "Unverified"
    assert result.evidence[1].page_reference == "N/A"
    assert result.evidence[1].pdf_page_numbers == []
    assert result.evidence[1].verification_note is not None


# 4b. DOCX: Exact-text match becomes Verified with DOCX page reference; unmatched remains Unverified
def test_docx_quotations_verified_and_unmatched_rejected(tmp_path, monkeypatch):
    docx_path = tmp_path / "reserch.docx"
    docx_path.write_text("Binary placeholder")
    content = (
        "Smart Kitchen Analytics for Hostels: A Machine Learning Approach to Food-Waste Prediction.\n"
        "Decision Tree and Polynomial SVM achieved superior empirical convergence."
    )
    paper = Paper(
        id="docx-fixture-001",
        filename="reserch.docx",
        original_path=str(docx_path),
        content_markdown=content,
        token_count=len(content.split()),
        metadata=PaperMetadata(title="Smart Kitchen Analytics"),
    )
    config = MockConfig()
    criterion = config.get_criteria()[0]

    valid_quote = "Decision Tree and Polynomial SVM achieved superior empirical convergence."
    unmatched_quote = "Fabricated statement not in document."

    payload = json.dumps({
        "score": 4,
        "score_justification": "Evidence provided.",
        "evidence": [
            {"quote": valid_quote, "page_reference": "N/A", "relevance": "Valid match"},
            {"quote": unmatched_quote, "page_reference": "N/A", "relevance": "Unmatched quote"},
        ],
        "strengths": ["Clear algorithms"],
        "weaknesses": [],
        "confidence": 0.85,
    })

    monkeypatch.setattr(
        "agents.agent_extractor.call_llm",
        lambda **kwargs: {"success": True, "content": payload, "cost": 0.0},
    )

    result = extract_criterion_evidence(paper, criterion, config)
    assert result is not None
    assert len(result.evidence) == 2

    # Verified item (DOCX)
    assert result.evidence[0].quote == valid_quote
    assert result.evidence[0].verification_status == "Verified"
    assert result.evidence[0].page_reference == "N/A (DOCX page numbering unavailable)"
    assert result.evidence[0].pdf_page_numbers == []
    assert result.evidence[0].verification_note is None

    # Unverified / rejected item (DOCX)
    assert result.evidence[1].quote == unmatched_quote
    assert result.evidence[1].verification_status == "Unverified"
    assert result.evidence[1].page_reference == "N/A"
    assert result.evidence[1].pdf_page_numbers == []
    assert result.evidence[1].verification_note == "No conservative exact-text match was found in extracted document text."


# 5. Synthesis receives only validated criterion results
def test_synthesis_receives_only_validated_criterion_results(tmp_path, monkeypatch):
    paper = create_fixture_paper(tmp_path)
    config = MockConfig()

    synthesis_called_with = {}

    def fake_synth_llm(prompt, **kwargs):
        synthesis_called_with["prompt"] = prompt
        return {
            "success": True,
            "content": json.dumps({
                "executive_summary": "The paper architecture is validated [Evidence: theoretical_contribution:Q1].",
                "decision_confidence": 0.8,
                "recommendation": "Accept",
                "recommendation_rationale": "High quality methodology.",
                "detailed_assessment": {
                    "major_strengths": ["Novel self-attention mechanism [Evidence: theoretical_contribution:Q1]."],
                    "major_concerns": [],
                    "minor_issues": [],
                },
                "revision_suggestions": [],
                "criterion_narrative": {
                    "theoretical_contribution": "Clear framework [Evidence: theoretical_contribution:Q1].",
                },
            }),
            "cost": 0.0,
        }

    monkeypatch.setattr("agents.agent_synthesizer.call_llm", fake_synth_llm)

    verified_item = EvidenceItem(
        quote="We propose a new simple network architecture, the Transformer, based solely on attention mechanisms.",
        page_reference="PDF Page 1",
        relevance="Architecture statement",
        verification_status="Verified",
        pdf_page_numbers=[1],
    )
    unverified_item = EvidenceItem(
        quote="Hallucinated quotation never found in source.",
        page_reference="N/A",
        relevance="Irrelevant",
        verification_status="Unverified",
        pdf_page_numbers=[],
        verification_note="Quote not found.",
    )

    valid_extraction = Extraction(
        paper_id=paper.id,
        criterion_id="theoretical_contribution",
        score=5,
        score_justification="Outstanding contribution.",
        evidence=[verified_item, unverified_item],
        strengths=["Original architecture"],
        weaknesses=[],
        confidence=0.9,
        model_used="ollama/llama3.2:3b",
        cost=0.0,
    )

    review = synthesize_review(paper, [valid_extraction], config)

    assert review is not None
    # Verified evidence passed to synthesis
    assert "theoretical_contribution:Q1" in synthesis_called_with["prompt"]
    # Unverified quote text is excluded from verified evidence summary in synthesis
    assert "Hallucinated quotation" not in synthesis_called_with["prompt"]

    # When extractions is empty (all failed):
    failed_review = synthesize_review(paper, [], config)
    assert failed_review.review_status == "failed"
    assert failed_review.overall_score is None
    assert failed_review.successful_criteria == 0
    assert failed_review.failed_criteria == 2
    assert "Extraction failed for all criteria" in failed_review.recommendation_rationale


# 6. One run's results never appear in another run
def test_one_run_results_never_appear_in_another_run(tmp_path, monkeypatch):
    run_a_dir = tmp_path / "run_upload_20261027_010000_11111111"
    run_b_dir = tmp_path / "run_upload_20261027_020000_22222222"

    for rdir, fname, title, score in [
        (run_a_dir, "paper_alpha.pdf", "Paper Alpha Title", 85.0),
        (run_b_dir, "paper_beta.pdf", "Paper Beta Title", 62.0),
    ]:
        (rdir / "papers").mkdir(parents=True)
        (rdir / "input").mkdir(parents=True)
        (rdir / "outputs" / "reviews").mkdir(parents=True)
        (rdir / "outputs" / "reports").mkdir(parents=True)

        (rdir / "papers" / fname).write_text("Dummy content")
        (rdir / "input" / "run_metadata.json").write_text(json.dumps({
            "original_filename": fname,
            "paper_title_extracted": title,
            "uploaded_at": "2026-10-27T01:00:00Z",
        }))
        (rdir / "outputs" / "reports" / "consolidated_reviews_20261027.csv").write_text(
            f"paper_filename,title,overall_score,recommendation,review_status\n{fname},{title},{score},Accept,complete\n"
        )

    monkeypatch.setattr("web_app.PROJECT_ROOT", str(tmp_path))
    runs = _find_run_dirs()

    run_a_meta = next(r for r in runs if r["name"] == run_a_dir.name)
    run_b_meta = next(r for r in runs if r["name"] == run_b_dir.name)

    assert run_a_meta["original_filename"] == "paper_alpha.pdf"
    assert run_a_meta["paper_title"] == "Paper Alpha Title"
    assert run_a_meta["overall_score"] == 85.0

    assert run_b_meta["original_filename"] == "paper_beta.pdf"
    assert run_b_meta["paper_title"] == "Paper Beta Title"
    assert run_b_meta["overall_score"] == 62.0

    # Ensure no leakage
    assert run_a_meta["original_filename"] != run_b_meta["original_filename"]
    assert run_a_meta["overall_score"] != run_b_meta["overall_score"]


# 7. A failed run is never shown as a successful review
def test_failed_run_is_never_shown_as_successful_review(tmp_path, monkeypatch):
    run_failed_dir = tmp_path / "run_upload_20261027_030000_33333333"
    (run_failed_dir / "papers").mkdir(parents=True)
    (run_failed_dir / "input").mkdir(parents=True)
    (run_failed_dir / "outputs" / "reviews").mkdir(parents=True)
    (run_failed_dir / "outputs" / "reports").mkdir(parents=True)

    (run_failed_dir / "papers" / "failed_paper.pdf").write_text("Dummy content")
    (run_failed_dir / "input" / "run_metadata.json").write_text(json.dumps({
        "original_filename": "failed_paper.pdf",
        "paper_title_extracted": "Failed Paper Title",
        "uploaded_at": "2026-10-27T03:00:00Z",
    }))

    # Progress indicating failure
    (run_failed_dir / "progress.json").write_text(json.dumps({
        "papers": {
            "failed_paper.pdf": {
                "review": {
                    "review_status": "failed",
                    "overall_score": None,
                    "recommendation": "Needs Manual Review",
                    "successful_criteria": 0,
                    "failed_criteria": 8,
                }
            }
        }
    }))

    (run_failed_dir / "outputs" / "reports" / "consolidated_reviews_20261027.csv").write_text(
        "paper_filename,title,overall_score,recommendation,review_status\nfailed_paper.pdf,Failed Paper Title,,Needs Manual Review,failed\n"
    )

    review_md_content = """# Review: Failed Paper Title

**Review Status:** **FAILED**
**Criteria:** 0/8 successful; 8 failed
**Overall Score:** None (Analysis Failed)
**Recommendation:** **NEEDS MANUAL REVIEW**
"""
    (run_failed_dir / "outputs" / "reviews" / "failed_paper_review.md").write_text(review_md_content)

    monkeypatch.setattr("web_app.PROJECT_ROOT", str(tmp_path))
    runs = _find_run_dirs()
    run_meta = next(r for r in runs if r["name"] == run_failed_dir.name)

    assert run_meta["status"] == "failed"
    assert run_meta["status_desc"] == "Failed"
    assert run_meta["overall_score"] is None
    # No "Score 0/100" presented in secondary text
    assert "Score" not in run_meta["formatted_secondary"]

    # Test metadata parsed from markdown directly
    parsed_meta = _parse_review_metadata_from_text(review_md_content, "failed_paper_review.md")
    assert parsed_meta["review_status"] == "failed"
    assert parsed_meta["overall_score"] is None


# 8. Local model failure yields a failure state with no fabricated results
@pytest.mark.parametrize("error_scenario,llm_mock_return,expected_error_substr", [
    ("unreachable", {"success": False, "error": "Connection refused to http://localhost:11434"}, "Model unreachable"),
    ("timeout", {"success": False, "error": "Read timed out after 300s"}, "Model unreachable"),
    ("empty_response", {"success": True, "content": "    "}, "empty response"),
])
def test_local_model_failure_yields_failure_state_with_no_fabricated_results(
    tmp_path, monkeypatch, error_scenario, llm_mock_return, expected_error_substr
):
    paper = create_fixture_paper(tmp_path)
    config = MockConfig()
    criterion = config.get_criteria()[0]

    monkeypatch.setattr(
        "agents.agent_extractor.call_llm",
        lambda **kwargs: llm_mock_return,
    )

    result = extract_criterion_evidence(paper, criterion, config)
    assert result is None
    assert criterion["id"] in config.extraction_failures
    recorded_error = config.extraction_failures[criterion["id"]]
    assert expected_error_substr.lower() in recorded_error.lower()

    # Now verify synthesis on failed extractions
    review = synthesize_review(paper, [], config)
    assert review.review_status == "failed"
    assert review.overall_score is None
    assert review.successful_criteria == 0
    assert review.failed_criteria == 2
    assert review.verified_evidence_count == 0
    assert review.unverified_evidence_count == 0
    assert len(review.detailed_assessment.major_strengths) == 0
    assert len(review.detailed_assessment.major_concerns) == 0
    assert len(review.revision_suggestions) == 0
    assert "No validated criterion extraction" in review.executive_summary
