import json

from fastapi.testclient import TestClient

import web_app


def _setup_run(tmp_path, monkeypatch):
    run_dir = tmp_path / "test_run"
    papers_dir = run_dir / "papers"
    papers_dir.mkdir(parents=True)
    (papers_dir / "sample.pdf").write_bytes(b"%PDF-test")

    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(tmp_path))
    return run_dir, TestClient(web_app.app)


def test_quality_analysis_api_returns_saved_reports(tmp_path, monkeypatch):
    run_dir, client = _setup_run(tmp_path, monkeypatch)
    report_dir = run_dir / "outputs" / "quality_analysis"
    report_dir.mkdir(parents=True)

    reports = {
        "citation_reference": {
            "analysis_type": "rule_based_numbered_citation_reference_analysis",
            "status": "completed",
            "citations_without_reference": [4],
            "limitations": ["Numbered citations only."],
        },
        "document_structure": {
            "analysis_type": "rule_based_extracted_text_structure_analysis",
            "status": "completed",
            "figure_count": 2,
            "limitations": ["Extracted text only."],
        },
        "cross_section_consistency": {
            "analysis_type": "heuristic_cross_section_consistency",
            "status": "completed",
            "finding_count": 1,
            "findings": [{"status": "requires_human_review"}],
            "limitations": ["Heuristic prompts only."],
        },
    }

    for name, report in reports.items():
        (report_dir / f"sample.pdf.{name}.json").write_text(
            json.dumps(report), encoding="utf-8"
        )

    response = client.get(
        f"/api/quality-analysis-for-review/{run_dir.as_posix()}/sample_extractor_synthesizer_20261026_120000.md"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["paper_filename"] == "sample.pdf"
    for name, report in reports.items():
        assert payload["analyses"][name]["available"] is True
        for key, value in report.items():
            assert payload["analyses"][name][key] == value


def test_quality_analysis_api_handles_missing_reports(tmp_path, monkeypatch):
    run_dir, client = _setup_run(tmp_path, monkeypatch)

    response = client.get(
        f"/api/quality-analysis-for-review/{run_dir.as_posix()}/sample_extractor_synthesizer_20261026_120000.md"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert all(
        item["status"] == "not_generated"
        for item in payload["analyses"].values()
    )


def test_quality_analysis_api_rejects_invalid_review_filename(tmp_path, monkeypatch):
    run_dir, client = _setup_run(tmp_path, monkeypatch)

    response = client.get(
        f"/api/quality-analysis-for-review/{run_dir.as_posix()}/outside.txt"
    )
    assert response.status_code == 400

    traversal_response = client.get(
        f"/api/quality-analysis-for-review/{run_dir.as_posix()}/%2e%2e%2foutside.md"
    )
    assert traversal_response.status_code == 400


def test_quality_analysis_api_marks_corrupt_report_unreadable(tmp_path, monkeypatch):
    run_dir, client = _setup_run(tmp_path, monkeypatch)
    report_dir = run_dir / "outputs" / "quality_analysis"
    report_dir.mkdir(parents=True)
    (report_dir / "sample.pdf.citation_reference.json").write_text(
        "{invalid json", encoding="utf-8"
    )

    response = client.get(
        f"/api/quality-analysis-for-review/{run_dir.as_posix()}/sample_extractor_synthesizer_20261026_120000.md"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["analyses"]["citation_reference"]["available"] is False
    assert payload["analyses"]["citation_reference"]["status"] == "unreadable"


def test_quality_analysis_api_preserves_finding_severity(tmp_path, monkeypatch):
    run_dir, client = _setup_run(tmp_path, monkeypatch)
    report_dir = run_dir / "outputs" / "quality_analysis"
    report_dir.mkdir(parents=True)
    report = {
        "status": "completed",
        "findings": [{
            "type": "possible_numerical_inconsistency",
            "severity": "medium",
            "status": "requires_human_review",
        }],
    }
    (report_dir / "sample.pdf.cross_section_consistency.json").write_text(
        json.dumps(report), encoding="utf-8"
    )

    response = client.get(
        f"/api/quality-analysis-for-review/{run_dir.as_posix()}/sample_extractor_synthesizer_20261026_120000.md"
    )

    assert response.status_code == 200
    finding = response.json()["analyses"]["cross_section_consistency"]["findings"][0]
    assert finding["severity"] == "medium"
    assert finding["status"] == "requires_human_review"
