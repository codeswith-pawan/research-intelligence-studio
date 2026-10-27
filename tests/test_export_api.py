"""
Tests for export and audit endpoints in web_app.py.
"""

import shutil
from pathlib import Path
import pytest
from starlette.testclient import TestClient
import web_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    fixture_dir = Path(__file__).parent / "fixtures" / "run_validation_20261026_232939_dda2d977"
    target_dir = tmp_path / "run_validation_20261026_232939_dda2d977"
    if fixture_dir.is_dir():
        shutil.copytree(str(fixture_dir), str(target_dir))
    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(tmp_path))
    return TestClient(web_app.app)


def test_download_csv_report_success(client):
    res = client.get("/api/reports/csv/run_validation_20261026_232939_dda2d977")
    assert res.status_code == 200
    assert "text/csv" in res.headers["content-type"]
    assert "attachment;" in res.headers.get("content-disposition", "")
    assert "paper_filename" in res.text
    assert "overall_score" in res.text


def test_download_csv_report_missing_dir(client):
    res = client.get("/api/reports/csv/non_existent_run_dir_12345")
    assert res.status_code == 404


def test_export_audit_json_success(client):
    res = client.get(
        "/api/export/audit/run_validation_20261026_232939_dda2d977/"
        "attention_is_all_you_need_neurips2017_ollama_llama3.2:3b_ollama_llama3.2:3b_20261026_233222.md"
    )
    assert res.status_code == 200
    data = res.json()
    assert data["system"] == "Research Intelligence Studio"
    assert data["export_type"] == "full_research_audit"
    assert "governance_invariants" in data
    assert data["governance_invariants"]["diagnostic_score"] == 80.0
    assert data["governance_invariants"]["provisional_recommendation"] == "Accept with Revisions"
    assert data["governance_invariants"]["final_system_status"] == "NEEDS MANUAL REVIEW"
    assert "evidence_grounding" in data
    assert "active_configuration" in data
    assert "raw_markdown" in data


def test_export_audit_json_not_found(client):
    res = client.get(
        "/api/export/audit/run_validation_20261026_232939_dda2d977/non_existent_paper.md"
    )
    assert res.status_code == 404


def test_export_audit_json_invalid_filename(client):
    res = client.get(
        "/api/export/audit/run_validation_20261026_232939_dda2d977/invalid_file.txt"
    )
    assert res.status_code == 400
