from pathlib import Path

from fastapi.testclient import TestClient

import web_app


def test_upload_pdf_creates_isolated_run_and_preserves_file(tmp_path, monkeypatch):
    project_root = Path(tmp_path)
    config_dir = project_root / "config"
    prompts_dir = config_dir / "prompts"
    prompts_dir.mkdir(parents=True)

    (config_dir / "criteria.yaml").write_text("criteria: []\n", encoding="utf-8")
    (prompts_dir / "extractor_system.txt").write_text("test prompt", encoding="utf-8")

    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(project_root))
    client = TestClient(web_app.app)
    pdf_content = b"%PDF-1.4\nTest PDF content"

    response = client.post(
        "/api/upload",
        files={"file": ("sample.pdf", pdf_content, "application/pdf")},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["filename"] == "sample.pdf"
    assert result["status"] == "uploaded"

    run_dir = project_root / result["run_dir"]
    assert run_dir.is_dir()
    assert (run_dir / "papers" / "sample.pdf").read_bytes() == pdf_content
    assert (run_dir / "input" / "criteria.yaml").read_text(encoding="utf-8") == "criteria: []\n"
    assert (run_dir / "input" / "prompts" / "extractor_system.txt").read_text(encoding="utf-8") == "test prompt"


def test_upload_inherits_selected_run_criteria_and_prompts(tmp_path, monkeypatch):
    project_root = Path(tmp_path)
    global_prompts = project_root / "config" / "prompts"
    global_prompts.mkdir(parents=True)
    (project_root / "config" / "criteria.yaml").write_text(
        "criteria:\n  - id: global_default\n", encoding="utf-8"
    )
    (global_prompts / "extractor_system.txt").write_text(
        "global extractor prompt", encoding="utf-8"
    )
    (global_prompts / "synthesizer_system.txt").write_text(
        "global synthesizer fallback", encoding="utf-8"
    )

    base_input = project_root / "target_run" / "input"
    base_prompts = base_input / "prompts"
    base_prompts.mkdir(parents=True)
    (base_input / "criteria.yaml").write_text(
        "criteria:\n  - id: target_specific\n", encoding="utf-8"
    )
    (base_input / ".env").write_text(
        "PROVIDER_EXTRACTION=ollama\nEXTRACTOR_MODEL=llama3.2:3b\n",
        encoding="utf-8",
    )
    (base_prompts / "extractor_system.txt").write_text(
        "target extractor prompt", encoding="utf-8"
    )

    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(project_root))
    client = TestClient(web_app.app)
    response = client.post(
        "/api/upload",
        data={"base_run_dir": "target_run"},
        files={"file": ("sample.pdf", b"%PDF-1.4\nTest PDF content", "application/pdf")},
    )

    assert response.status_code == 200
    run_dir = project_root / response.json()["run_dir"]
    assert (run_dir / "input" / "criteria.yaml").read_text(encoding="utf-8") == (
        "criteria:\n  - id: target_specific\n"
    )
    assert (run_dir / "input" / ".env").read_text(encoding="utf-8") == (
        "PROVIDER_EXTRACTION=ollama\nEXTRACTOR_MODEL=llama3.2:3b\n"
    )
    assert (
        run_dir / "input" / "prompts" / "extractor_system.txt"
    ).read_text(encoding="utf-8") == "target extractor prompt"
    # Missing run-specific prompt files retain the existing global fallback.
    assert (
        run_dir / "input" / "prompts" / "synthesizer_system.txt"
    ).read_text(encoding="utf-8") == "global synthesizer fallback"


def test_upload_rejects_unsupported_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(tmp_path))
    client = TestClient(web_app.app)

    response = client.post(
        "/api/upload",
        files={"file": ("sample.txt", b"not a paper", "text/plain")},
    )

    assert response.status_code == 400
    assert list(tmp_path.iterdir()) == []


def test_upload_rejects_invalid_pdf_signature(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(tmp_path))
    client = TestClient(web_app.app)

    response = client.post(
        "/api/upload",
        files={"file": ("sample.pdf", b"not a PDF", "application/pdf")},
    )

    assert response.status_code == 400
    assert list(tmp_path.iterdir()) == []


def test_upload_cleans_partial_run_when_criteria_is_missing(tmp_path, monkeypatch):
    project_root = Path(tmp_path)
    prompts_dir = project_root / "config" / "prompts"
    prompts_dir.mkdir(parents=True)

    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(project_root))
    client = TestClient(web_app.app)

    response = client.post(
        "/api/upload",
        files={"file": ("sample.pdf", b"%PDF-1.4\nTest PDF", "application/pdf")},
    )

    assert response.status_code == 500
    assert list(project_root.glob("run_upload_*")) == []



def test_duplicate_upload_reuses_existing_run(tmp_path, monkeypatch):
    project_root = Path(tmp_path)
    config_dir = project_root / "config"
    prompts_dir = config_dir / "prompts"
    prompts_dir.mkdir(parents=True)
    (config_dir / "criteria.yaml").write_text("criteria: []\\n", encoding="utf-8")

    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(project_root))
    client = TestClient(web_app.app)
    pdf_content = b"%PDF-1.4\\nSame PDF content"

    first = client.post(
        "/api/upload",
        files={"file": ("first.pdf", pdf_content, "application/pdf")},
    )
    assert first.status_code == 200
    first_result = first.json()

    second = client.post(
        "/api/upload",
        files={"file": ("renamed.pdf", pdf_content, "application/pdf")},
    )
    assert second.status_code == 200
    second_result = second.json()

    assert second_result["status"] == "duplicate"
    assert second_result["run_dir"] == first_result["run_dir"]
    assert len(list(project_root.glob("run_upload_*"))) == 1
