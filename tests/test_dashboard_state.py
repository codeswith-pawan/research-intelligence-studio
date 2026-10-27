from types import SimpleNamespace

from fastapi.testclient import TestClient

from core.progress import RunCompleted
import web_app
from web_app import _classify_run_status


def test_run_status_distinguishes_complete_warning_partial_and_failed_states():
    complete = SimpleNamespace(review_status="complete")
    incomplete = SimpleNamespace(review_status="incomplete")

    assert _classify_run_status([complete], 1) == "completed"
    assert _classify_run_status([incomplete], 1) == "completed_with_warnings"
    assert _classify_run_status([complete], 2) == "partial"
    assert _classify_run_status([], 1) == "failed"
    assert _classify_run_status([complete], 1, cancelled=True) == "cancelled"


def test_run_completed_event_carries_expected_counts_and_status():
    event = RunCompleted(total_papers=1, papers_total=3, papers_failed=2, status="partial")
    payload = event.to_dict()

    assert payload["total_papers"] == 1
    assert payload["papers_total"] == 3
    assert payload["papers_failed"] == 2
    assert payload["status"] == "partial"


def test_dashboard_parses_bold_review_metadata_and_preserves_report_warnings(tmp_path, monkeypatch):
    run_dir = tmp_path / "run_validation"
    reviews_dir = run_dir / "outputs" / "reviews"
    reviews_dir.mkdir(parents=True)
    filename = "22-1250_ollama_llama3.2:3b_ollama_llama3.2:3b_20261026_193420.md"
    report = reviews_dir / filename
    report.write_text(
        "\n".join(
            [
                "# Review: Target Paper",
                "**Review Status:** **MANUAL_REVIEW**",
                "**Criteria:** 8/8 successful; 0 failed",
                "**Verified Evidence:** 11",
                "**Unverified Evidence:** 2",
                "**Final System Recommendation:** **NEEDS MANUAL REVIEW**",
                "**Provisional Recommendation:** Accept with Revisions",
                "**Overall Score:** **80.0 / 100**",
                "**Heuristic Reliability Estimate:** 45.8% (not statistically calibrated)",
                "### Warnings",
                "- Two quotations were not source verified.",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(tmp_path))

    with TestClient(web_app.app) as client:
        response = client.get("/api/all-reviews/run_validation")
        assert response.status_code == 200
        reviews = response.json()["reviews"]
        assert len(reviews) == 1
        metadata = reviews[0]
        assert metadata["review_status"] == "manual_review"
        assert metadata["successful_criteria"] == 8
        assert metadata["total_criteria"] == 8
        assert metadata["verified_evidence_count"] == 11
        assert metadata["unverified_evidence_count"] == 2
        assert metadata["recommendation"] == "NEEDS MANUAL REVIEW"
        assert metadata["provisional_recommendation"] == "Accept with Revisions"
        assert metadata["overall_score"] == 80.0

        report_response = client.get(f"/api/review/run_validation/{filename}")
        assert report_response.status_code == 200
        assert "### Warnings" in report_response.text
