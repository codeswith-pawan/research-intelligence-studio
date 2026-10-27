"""Test suite for PDF report export, score consistency, and synthesis safety."""

import os
import json
from pathlib import Path
import pytest
import pymupdf
from fastapi.testclient import TestClient

from core.data_models import Review, Paper, PaperMetadata, WeightedBreakdown, DetailedAssessment, EvidenceItem
from utilities.output_generator import save_review_pdf, generate_review_pdf_html
from agents.agent_synthesizer import _rescue_misplaced_fields, synthesize_review
import web_app
from web_app import app


def _make_sample_review(paper_title="Test Evaluation Paper", score=58.5, status="manual_review"):
    return Review(
        paper_id="test-paper-pdf-1234",
        paper_title=paper_title,
        paper_filename="sample_paper.pdf",
        extractor_model_used="ollama/llama3.2:3b",
        synthesizer_model_used="ollama/llama3.2:3b",
        overall_score=score,
        weighted_breakdown={
            "empirical_rigor": WeightedBreakdown(score=3, weight=18.0, weighted_score=10.8),
            "data_quality": WeightedBreakdown(score=3, weight=10.0, weighted_score=6.0),
        },
        recommendation="Needs Manual Review" if status != "complete" else "Accept",
        provisional_recommendation="Revise and Resubmit",
        recommendation_rationale=f"Diagnostic weighted score is {score:.1f}/100 requiring human editorial review.",
        executive_summary="The paper explores empirical methods [Evidence: empirical_rigor:Q1].",
        detailed_assessment=DetailedAssessment(
            major_strengths=["Clear experimental evaluation [Evidence: empirical_rigor:Q1]."],
            major_concerns=["Limited econometric depth [Evidence: empirical_rigor:Q1]."],
            minor_issues=[],
        ),
        criterion_narrative={
            "empirical_rigor": "Empirical studies focus [Evidence: empirical_rigor:Q1].",
            "data_quality": "Data sources evaluated [Evidence: data_quality:Q1].",
        },
        revision_suggestions=["Incorporate formal power analysis."],
        decision_confidence=0.75,
        review_status=status,
        total_criteria=2,
        successful_criteria=2,
        failed_criteria=0,
        verified_evidence_count=2,
        unverified_evidence_count=1,
        criterion_evidence={
            "empirical_rigor": [
                EvidenceItem(
                    quote="Direct verified excerpt from page 9 of the manuscript.",
                    page_reference="PDF Page 9",
                    relevance="Demonstrates empirical focus",
                    verification_status="Verified",
                    pdf_page_numbers=[9],
                ),
            ],
            "data_quality": [
                EvidenceItem(
                    quote="Direct unverified text that had no source match.",
                    page_reference="N/A",
                    relevance="Illustrates unverified extraction",
                    verification_status="Unverified",
                    pdf_page_numbers=[],
                    verification_note="No conservative exact-text match was found in the source PDF.",
                ),
            ],
        },
        warnings=[
            "1 quotation(s) did not match source text and are excluded from verified evidence.",
            "Exact quotation matching verifies text presence in the source manuscript but does not establish semantic relevance to the evaluated criteria; human review is required to confirm rubric alignment.",
        ],
    )


# ---------------------------------------------------------------------------
# Section B: PDF Generation Tests
# ---------------------------------------------------------------------------

def test_save_review_pdf_creates_valid_readable_document(tmp_path):
    """Verify that save_review_pdf produces a readable, valid multi-page PDF with all metadata."""
    review = _make_sample_review()
    out_pdf = str(tmp_path / "test_report.pdf")

    saved_path = save_review_pdf(review, out_pdf)
    assert os.path.exists(saved_path)

    doc = pymupdf.open(saved_path)
    assert len(doc) >= 1

    full_text = "".join(page.get_text() for page in doc)
    import unicodedata
    normalized_text = unicodedata.normalize("NFKD", full_text)
    # Check paper metadata
    assert "Test Evaluation Paper" in normalized_text
    assert "test-paper-pdf-1234" in normalized_text
    # Check score and recommendations
    assert "58.5 / 100" in normalized_text
    assert "Revise and Resubmit" in normalized_text
    assert "Needs Manual Review" in normalized_text
    # Check verification distinction
    assert "2 Verified" in normalized_text
    assert "1 Unverified" in normalized_text
    assert "VERIFIED" in normalized_text
    assert "UNVERIFIED" in normalized_text
    assert "PDF Page 9" in normalized_text
    assert "Direct verified excerpt" in normalized_text
    # Check warnings
    assert "did not match source text" in normalized_text
    assert "human review is required" in normalized_text
    doc.close()


def test_generate_review_pdf_html_includes_all_contract_fields():
    """Verify that generated HTML contains all necessary CSS and semantic structures."""
    review = _make_sample_review(score=63.2, status="failed")
    html_out = generate_review_pdf_html(review)

    assert "63.2 / 100" in html_out
    assert "Diagnostic Score" in html_out
    assert "PROCESSING FAILURE" in html_out
    assert "data_quality:Q1" in html_out
    assert "empirical_rigor:Q1" in html_out


# ---------------------------------------------------------------------------
# Section A & B: API Endpoint & Download Tests
# ---------------------------------------------------------------------------

def test_export_pdf_endpoint_downloads_existing_or_generated_pdf(tmp_path, monkeypatch):
    """Test /api/export/pdf endpoint returns application/pdf with Content-Disposition header."""
    run_name = "run_test_export_pdf"
    run_dir = tmp_path / run_name
    reviews_dir = run_dir / "outputs" / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    sample_review = _make_sample_review(paper_title="Expert Systems With Applications")
    pdf_file = reviews_dir / "Expert_System_with_applications_review.pdf"
    save_review_pdf(sample_review, str(pdf_file))
    assert pdf_file.is_file()

    # Configure progress.json for dynamic generation fallback test
    progress_file = run_dir / "progress.json"
    progress_data = {
        "papers": {
            "sample_paper.pdf": {
                "review": sample_review.model_dump(mode="json"),
                "status": "completed",
            }
        }
    }
    with open(progress_file, "w", encoding="utf-8") as f:
        json.dump(progress_data, f)

    # Safely isolate PROJECT_ROOT to the temporary test directory
    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(tmp_path))

    client = TestClient(app)

    # 1. Test downloading an existing PDF file
    res = client.get(f"/api/export/pdf/{run_name}/Expert_System_with_applications_review.pdf")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert "attachment" in res.headers["content-disposition"]
    assert "Expert_System_with_applications_review.pdf" in res.headers["content-disposition"]
    assert len(res.content) > 1000

    # Verify content is a valid PDF
    doc = pymupdf.open(stream=res.content, filetype="pdf")
    assert len(doc) >= 1
    assert "Expert Systems With Applications" in "".join(page.get_text() for page in doc)
    doc.close()

    # 2. Test dynamic generation when the PDF does not exist on disk but progress.json does
    pdf_file.unlink()
    assert not pdf_file.is_file()

    res_gen = client.get(f"/api/export/pdf/{run_name}/Expert_System_with_applications_review.pdf")
    assert res_gen.status_code == 200
    assert res_gen.headers["content-type"] == "application/pdf"
    assert "attachment" in res_gen.headers["content-disposition"]
    assert len(res_gen.content) > 1000
    assert pdf_file.is_file()

    doc_gen = pymupdf.open(stream=res_gen.content, filetype="pdf")
    assert len(doc_gen) >= 1
    assert "Expert Systems With Applications" in "".join(page.get_text() for page in doc_gen)
    doc_gen.close()


# ---------------------------------------------------------------------------
# Section A: Misplaced Field Rescue & Synthesis Resilience
# ---------------------------------------------------------------------------

def test_rescue_misplaced_revision_suggestions_inside_detailed_assessment():
    """Verify that small models nesting revision_suggestions in detailed_assessment are rescued."""
    raw = {
        "executive_summary": "Summary text",
        "detailed_assessment": {
            "major_strengths": ["Strength 1"],
            "major_concerns": ["Concern 1"],
            "minor_issues": [],
            "revision_suggestions": ["Suggestion 1", "Suggestion 2"],
        },
        "criterion_narrative": {
            "theory": "Theory note",
        },
    }

    rescued = _rescue_misplaced_fields(raw)
    assert "revision_suggestions" in rescued
    assert rescued["revision_suggestions"] == ["Suggestion 1", "Suggestion 2"]
    assert "revision_suggestions" not in rescued["detailed_assessment"]
