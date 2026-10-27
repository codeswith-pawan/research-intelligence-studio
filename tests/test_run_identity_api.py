import json
from pathlib import Path
from fastapi.testclient import TestClient

import web_app


def test_find_run_dirs_with_explicit_metadata(tmp_path, monkeypatch):
    """Verify that _find_run_dirs reads additive run_metadata.json when present."""
    project_root = Path(tmp_path)
    run_dir = project_root / "run_upload_20261026_120000_11223344"
    (run_dir / "papers").mkdir(parents=True)
    (run_dir / "input").mkdir(parents=True)

    test_pdf = b"%PDF-1.4\nTest PDF content"
    (run_dir / "papers" / "attention.pdf").write_bytes(test_pdf)

    metadata = {
        "original_filename": "Attention_Is_All_You_Need_NeurIPS2017.pdf",
        "safe_filename": "attention.pdf",
        "file_size_bytes": len(test_pdf),
        "file_hash_sha256": "fakehash123",
        "uploaded_at": "2026-10-26T12:00:00Z",
        "paper_title_extracted": "Attention Is All You Need",
    }
    (run_dir / "input" / "run_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(project_root))
    runs = web_app._find_run_dirs()

    assert len(runs) == 1
    r = runs[0]
    assert r["name"] == "run_upload_20261026_120000_11223344"
    assert r["original_filename"] == "Attention_Is_All_You_Need_NeurIPS2017.pdf"
    assert r["paper_title"] == "Attention Is All You Need"
    assert r["run_number"] == 1
    assert r["total_runs_for_paper"] == 1
    assert r["run_label"] == "Run 1 of 1"
    assert "Attention Is All You Need" in r["formatted_summary"]


def test_find_run_dirs_legacy_fallback_and_deterministic_numbering(tmp_path, monkeypatch):
    """Verify backward compatibility and deterministic chronological numbering for legacy runs."""
    project_root = Path(tmp_path)

    # Legacy run 1 (older)
    run1 = project_root / "run_validation_20261025_100000_aaaa1111"
    (run1 / "papers").mkdir(parents=True)
    (run1 / "papers" / "smith_study.pdf").write_bytes(b"%PDF-1.4\nsmith")
    (run1 / "outputs" / "reports").mkdir(parents=True)
    (run1 / "outputs" / "reviews").mkdir(parents=True)
    (run1 / "outputs" / "reports" / "report.csv").write_text(
        "paper_filename,title,overall_score,recommendation,final_system_status\n"
        "smith_study,**Smith Study of Neural Models**,75.0,Needs Manual Review,Needs Manual Review\n",
        encoding="utf-8",
    )

    # Legacy run 2 (newer, same paper title)
    run2 = project_root / "run_validation_20261026_150000_bbbb2222"
    (run2 / "papers").mkdir(parents=True)
    (run2 / "papers" / "smith_study.pdf").write_bytes(b"%PDF-1.4\nsmith")
    (run2 / "outputs" / "reports").mkdir(parents=True)
    (run2 / "outputs" / "reviews").mkdir(parents=True)
    (run2 / "outputs" / "reports" / "report.csv").write_text(
        "paper_filename,title,overall_score,recommendation,final_system_status\n"
        "smith_study,Smith Study of Neural Models,85.0,Accept,Accept\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(project_root))
    runs = web_app._find_run_dirs()

    assert len(runs) == 2
    # Default order is newest first (run2 then run1)
    assert runs[0]["name"] == "run_validation_20261026_150000_bbbb2222"
    assert runs[1]["name"] == "run_validation_20261025_100000_aaaa1111"

    # Deterministic chronological run labels:
    # run1 is older -> Run 1 of 2
    # run2 is newer -> Run 2 of 2
    assert runs[1]["run_number"] == 1
    assert runs[1]["total_runs_for_paper"] == 2
    assert runs[1]["run_label"] == "Run 1 of 2"
    assert runs[1]["paper_title"] == "Smith Study of Neural Models"
    assert runs[1]["overall_score"] == 75.0

    assert runs[0]["run_number"] == 2
    assert runs[0]["total_runs_for_paper"] == 2
    assert runs[0]["run_label"] == "Run 2 of 2"
    assert runs[0]["paper_title"] == "Smith Study of Neural Models"
    assert runs[0]["overall_score"] == 85.0


def test_upload_persists_run_metadata(tmp_path, monkeypatch):
    """Verify that uploading creates input/run_metadata.json with original filename."""
    project_root = Path(tmp_path)
    config_dir = project_root / "config"
    prompts_dir = config_dir / "prompts"
    prompts_dir.mkdir(parents=True)
    (config_dir / "criteria.yaml").write_text("criteria: []\n", encoding="utf-8")
    (prompts_dir / "extractor_system.txt").write_text("prompt", encoding="utf-8")

    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(project_root))
    client = TestClient(web_app.app)
    pdf_content = b"%PDF-1.4\nCustom original paper manuscript"

    response = client.post(
        "/api/upload",
        files={"file": ("My_Research_Paper_2026.pdf", pdf_content, "application/pdf")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "uploaded"
    assert data["original_filename"] == "My_Research_Paper_2026.pdf"

    run_dir = project_root / data["run_dir"]
    meta_path = run_dir / "input" / "run_metadata.json"
    assert meta_path.is_file()

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["original_filename"] == "My_Research_Paper_2026.pdf"
    assert meta["file_size_bytes"] == len(pdf_content)
    assert meta["file_hash_sha256"] != ""
    assert "uploaded_at" in meta


def test_upload_force_new_allows_clean_rerun_on_duplicate(tmp_path, monkeypatch):
    """Verify that force_new=True bypasses duplicate reuse and creates a fresh run."""
    project_root = Path(tmp_path)
    config_dir = project_root / "config"
    prompts_dir = config_dir / "prompts"
    prompts_dir.mkdir(parents=True)
    (config_dir / "criteria.yaml").write_text("criteria: []\n", encoding="utf-8")
    (prompts_dir / "extractor_system.txt").write_text("prompt", encoding="utf-8")

    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(project_root))
    client = TestClient(web_app.app)
    pdf_content = b"%PDF-1.4\nExact Duplicate Content"

    # First upload
    first = client.post(
        "/api/upload",
        files={"file": ("paper_v1.pdf", pdf_content, "application/pdf")},
    )
    assert first.status_code == 200
    first_run = first.json()["run_dir"]

    # Second upload without force_new -> returns duplicate
    dup_res = client.post(
        "/api/upload",
        files={"file": ("paper_v2.pdf", pdf_content, "application/pdf")},
        data={"force_new": "false"},
    )
    assert dup_res.status_code == 200
    dup_data = dup_res.json()
    assert dup_data["status"] == "duplicate"
    assert dup_data["run_dir"] == first_run

    # Third upload WITH force_new=True -> creates brand new run directory!
    force_res = client.post(
        "/api/upload",
        files={"file": ("paper_v3.pdf", pdf_content, "application/pdf")},
        data={"force_new": "true"},
    )
    assert force_res.status_code == 200
    force_data = force_res.json()
    assert force_data["status"] == "uploaded"
    assert force_data["run_dir"] != first_run
    assert (project_root / force_data["run_dir"]).is_dir()
    assert (project_root / first_run).is_dir()


def test_run_metadata_api_endpoint(tmp_path, monkeypatch):
    """Verify /api/runs/{run_dir}/metadata returns 200 for known run and 404 for unknown."""
    project_root = Path(tmp_path)
    run_dir = project_root / "run_validation_20261026_000000_12345678"
    (run_dir / "papers").mkdir(parents=True)
    (run_dir / "papers" / "doc.pdf").write_bytes(b"%PDF-1.4\nSample")

    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(project_root))
    client = TestClient(web_app.app)

    # Known run
    res = client.get("/api/runs/run_validation_20261026_000000_12345678/metadata")
    assert res.status_code == 200
    meta = res.json()
    assert meta["name"] == "run_validation_20261026_000000_12345678"
    assert "paper_title" in meta
    assert "run_label" in meta

    # Unknown run
    bad_res = client.get("/api/runs/nonexistent_run_directory/metadata")
    assert bad_res.status_code == 404
