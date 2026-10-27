import json
from pathlib import Path

from fastapi.testclient import TestClient

import web_app


def test_evidence_api_returns_saved_records_without_modifying_them(tmp_path, monkeypatch):
    run_dir = tmp_path / "test_run"
    evidence_dir = run_dir / "outputs" / "evidence"
    evidence_dir.mkdir(parents=True)

    original_records = [
        {
            "paper_filename": "sample.pdf",
            "criterion_id": "test",
            "quote": "A test evidence quote.",
            "verification_status": "Unverified",
        }
    ]
    evidence_path = evidence_dir / "sample_evidence.json"
    original_text = json.dumps(original_records, indent=2)
    evidence_path.write_text(original_text, encoding="utf-8")

    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(tmp_path))
    client = TestClient(web_app.app)

    response = client.get(f"/api/evidence/{run_dir.as_posix()}/sample.pdf")

    assert response.status_code == 200
    assert response.json()["evidence"] == original_records
    assert evidence_path.read_text(encoding="utf-8") == original_text


def test_evidence_api_returns_empty_list_when_file_is_missing(tmp_path, monkeypatch):
    run_dir = tmp_path / "test_run"
    (run_dir / "outputs" / "evidence").mkdir(parents=True)

    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(tmp_path))
    client = TestClient(web_app.app)

    response = client.get(f"/api/evidence/{run_dir.as_posix()}/missing.pdf")

    assert response.status_code == 200
    assert response.json() == {
        "paper_filename": "missing.pdf",
        "evidence": [],
    }
