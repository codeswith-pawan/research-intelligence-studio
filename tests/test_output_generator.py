import csv
from datetime import datetime
from pathlib import Path

from core.data_models import (
    DetailedAssessment,
    EvidenceItem,
    GroundedReview,
    LiteratureContext,
    WeightedBreakdown,
)
from utilities.output_generator import (
    save_consolidated_csv,
    save_review_markdown,
)


def make_grounded_review(novelty_adjusted_score=None, base_score=72.0):
    return GroundedReview(
        paper_id="paper-test",
        paper_title="Test Research Paper",
        paper_filename="test_paper.pdf",
        overall_score=72.0,
        recommendation="Accept with Revisions",
        recommendation_rationale="The paper needs targeted improvements.",
        executive_summary="A test review summary.",
        detailed_assessment=DetailedAssessment(
            major_strengths=["Clear research question"],
            major_concerns=["Limited validation"],
            minor_issues=[],
        ),
        criterion_narrative={"methodology": "Methodology assessment."},
        revision_suggestions=["Add an external validation experiment."],
        decision_confidence=0.85,
        weighted_breakdown={
            "methodology": WeightedBreakdown(
                score=4,
                weight=60,
                weighted_score=48.0,
            )
        },
        extractor_model_used="test-extractor",
        synthesizer_model_used="test-synthesizer",
        total_cost=0.12,
        synthesis_timestamp=datetime(2026, 10, 26, 10, 30),
        literature_context=LiteratureContext(),
        base_score=base_score,
        novelty_adjusted_score=novelty_adjusted_score,
        criterion_evidence={
            "methodology": [
                EvidenceItem(
                    quote="The verified source passage.",
                    page_reference="PDF Page 4",
                    verification_status="Verified",
                    pdf_page_numbers=[4],
                )
            ]
        },
    )


def test_grounded_markdown_preserves_score_and_recommendation(tmp_path):
    review = make_grounded_review(novelty_adjusted_score=0.0)
    output_path = tmp_path / "review.md"

    save_review_markdown(
        review,
        str(output_path),
        include_literature_context=True,
    )

    report = output_path.read_text(encoding="utf-8")
    assert "**Recommendation:** **ACCEPT WITH REVISIONS**" in report
    assert "**Overall Score:** **72.0 / 100**" in report
    assert "Base Score: 72.0" in report
    assert "**Novelty-Adjusted Score: 0.0**" in report
    assert "Novelty Adjustment: -72.0" in report


def test_markdown_evidence_ids_match_synthesis_citation_format(tmp_path):
    review = make_grounded_review()
    review.executive_summary = "A source-grounded summary. [Evidence: methodology:Q1]."
    output_path = tmp_path / "review.md"

    save_review_markdown(
        review,
        str(output_path),
        include_literature_context=True,
    )

    report = output_path.read_text(encoding="utf-8")
    assert "[Evidence: methodology:Q1]" in report
    assert "**Evidence ID:** `methodology:Q1`" in report
    assert "**Verification:** VERIFIED" in report
    assert "PDF Page 4" in report


def test_grounded_csv_preserves_score_and_recommendation(tmp_path):
    review = make_grounded_review(novelty_adjusted_score=0.0)
    output_path = tmp_path / "reviews.csv"

    save_consolidated_csv(
        [review],
        str(output_path),
        include_literature_metrics=True,
    )

    with output_path.open(encoding="utf-8", newline="") as file:
        row = next(csv.DictReader(file))

    assert float(row["overall_score"]) == 72.0
    assert row["recommendation"] == "Accept with Revisions"
    assert float(row["novelty_adjusted_score"]) == 0.0
    assert float(row["novelty_adjustment"]) == -72.0


def test_grounded_csv_omits_novelty_metrics_when_disabled(tmp_path):
    review = make_grounded_review(novelty_adjusted_score=0.0)
    output_path = tmp_path / "reviews.csv"

    save_consolidated_csv(
        [review],
        str(output_path),
        include_literature_metrics=False,
    )

    with output_path.open(encoding="utf-8", newline="") as file:
        row = next(csv.DictReader(file))

    assert float(row["overall_score"]) == 72.0
    assert row["recommendation"] == "Accept with Revisions"
    assert "novelty_adjusted_score" not in row
    assert "novelty_adjustment" not in row
