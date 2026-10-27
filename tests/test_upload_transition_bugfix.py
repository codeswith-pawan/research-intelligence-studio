import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

import web_app


def _setup_test_env(tmp_path):
    project_root = Path(tmp_path)
    config_dir = project_root / "config"
    prompts_dir = config_dir / "prompts"
    prompts_dir.mkdir(parents=True)
    (config_dir / "criteria.yaml").write_text("criteria: []\n", encoding="utf-8")
    for p in ("extractor_system.txt", "extractor_user.txt", "extractor_criterion.txt", "synthesizer_system.txt", "synthesizer_user.txt"):
        (prompts_dir / p).write_text(f"prompt {p}", encoding="utf-8")
    return project_root


def test_upload_persists_identity_and_original_filename(tmp_path, monkeypatch):
    """1. Upload response identifies the new run and its original filename is persisted additively."""
    project_root = _setup_test_env(tmp_path)
    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(project_root))
    client = TestClient(web_app.app)

    pdf_content = b"%PDF-1.4\nTest upload content 1"
    response = client.post(
        "/api/upload",
        files={"file": ("attend_all_of_yu.pdf", pdf_content, "application/pdf")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "uploaded"
    assert data["original_filename"] == "attend_all_of_yu.pdf"
    assert data["filename"] == "attend_all_of_yu.pdf"

    run_dir = project_root / data["run_dir"]
    assert run_dir.is_dir()
    meta_path = run_dir / "input" / "run_metadata.json"
    assert meta_path.is_file()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["original_filename"] == "attend_all_of_yu.pdf"
    assert meta["safe_filename"] == "attend_all_of_yu.pdf"
    assert meta["file_hash_sha256"] is not None
    assert meta["paper_title_extracted"] is None


def test_display_name_never_derives_garbled_title_from_slug(tmp_path, monkeypatch):
    """2. Display name is derived by the documented rule and is never a garbled slug-derived paper title."""
    project_root = _setup_test_env(tmp_path)
    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(project_root))

    run_dir = project_root / "run_upload_20261027_022355_8f7decd7"
    (run_dir / "papers").mkdir(parents=True)
    (run_dir / "input").mkdir(parents=True)
    (run_dir / "papers" / "attend_all_of_yu.pdf").write_bytes(b"%PDF-1.4\ncontent")
    meta = {
        "original_filename": "attend_all_of_yu.pdf",
        "safe_filename": "attend_all_of_yu.pdf",
        "file_size_bytes": 1234,
        "file_hash_sha256": "fakehash123",
        "uploaded_at": "2026-10-27T02:23:55Z",
        "paper_title_extracted": None,
    }
    (run_dir / "input" / "run_metadata.json").write_text(json.dumps(meta), encoding="utf-8")

    runs = web_app._find_run_dirs()
    assert len(runs) == 1
    r = runs[0]
    # Primary display name must be original filename, paper_title must NOT be fabricated
    assert r["original_filename"] == "attend_all_of_yu.pdf"
    assert r["paper_title"] is None
    assert "Attend All Of Yu" not in r["display_name"]
    assert r["display_name"] == "attend_all_of_yu.pdf · Run 1 of 1"


def test_duplicate_detection_sha256_and_force_new(tmp_path, monkeypatch):
    """3. Re-uploading identical content returns duplicate information without overwriting; force_new creates separate run."""
    project_root = _setup_test_env(tmp_path)
    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(project_root))
    client = TestClient(web_app.app)

    pdf_content = b"%PDF-1.4\nExact Duplicate Content"
    first = client.post(
        "/api/upload",
        files={"file": ("paper.pdf", pdf_content, "application/pdf")},
    )
    assert first.status_code == 200
    first_dir = first.json()["run_dir"]

    # Re-upload without force_new -> returns duplicate
    dup = client.post(
        "/api/upload",
        files={"file": ("different_name.pdf", pdf_content, "application/pdf")},
    )
    assert dup.status_code == 200
    dup_data = dup.json()
    assert dup_data["status"] == "duplicate"
    assert dup_data["run_dir"] == first_dir

    # Re-upload with force_new -> creates new separate run
    forced = client.post(
        "/api/upload",
        data={"force_new": "true"},
        files={"file": ("paper_v2.pdf", pdf_content, "application/pdf")},
    )
    assert forced.status_code == 200
    forced_data = forced.json()
    assert forced_data["status"] == "uploaded"
    assert forced_data["run_dir"] != first_dir
    assert (project_root / forced_data["run_dir"]).is_dir()

    # Both runs exist on disk
    runs = web_app._find_run_dirs()
    assert len(runs) == 2


def test_unanalyzed_run_ready_state_and_no_fabricated_scores(tmp_path, monkeypatch):
    """4. A new, unanalyzed run reports a 'ready' status and no fabricated results or scores."""
    project_root = _setup_test_env(tmp_path)
    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(project_root))

    run_dir = project_root / "run_upload_20261027_000000_11112222"
    (run_dir / "papers").mkdir(parents=True)
    (run_dir / "input").mkdir(parents=True)
    (run_dir / "papers" / "new_paper.pdf").write_bytes(b"%PDF-1.4\nnew")

    runs = web_app._find_run_dirs()
    assert len(runs) == 1
    r = runs[0]
    assert r["status"] == "ready"
    assert r["status_desc"] == "Ready for Analysis"
    assert r["has_output"] is False
    assert r["overall_score"] is None
    assert r["final_system_status"] is None


def test_start_analysis_targets_intended_run_only(tmp_path, monkeypatch):
    """5. Start Analysis targets the intended run only."""
    project_root = _setup_test_env(tmp_path)
    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(project_root))
    client = TestClient(web_app.app)

    run_dir = project_root / "run_upload_target"
    (run_dir / "papers").mkdir(parents=True)
    (run_dir / "input").mkdir(parents=True)
    (run_dir / "papers" / "paper.pdf").write_bytes(b"%PDF-1.4\ntarget")

    with patch("web_app.threading.Thread") as mock_thread:
        mock_instance = MagicMock()
        mock_thread.return_value = mock_instance
        response = client.post("/api/start", json={"run_dir": "run_upload_target", "mode": "standard"})
        assert response.status_code == 200
        data = response.json()
        assert "run_id" in data
        assert "run_upload_target" in data["run_id"]
        assert mock_thread.called
        target_arg = mock_thread.call_args[1]["args"][0]
        assert "run_upload_target" in target_arg


def test_run_isolation_b_does_not_contain_a(tmp_path, monkeypatch):
    """6. Run B's metadata and results cannot include Run A's data."""
    project_root = _setup_test_env(tmp_path)
    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(project_root))
    client = TestClient(web_app.app)

    # Run A
    run_a = project_root / "run_a"
    (run_a / "papers").mkdir(parents=True)
    (run_a / "papers" / "paper_a.pdf").write_bytes(b"%PDF-1.4\npaperA")
    (run_a / "outputs" / "reports").mkdir(parents=True)
    (run_a / "outputs" / "reviews").mkdir(parents=True)
    (run_a / "outputs" / "reports" / "report.csv").write_text(
        "paper_filename,title,overall_score,recommendation\npaper_a,Title A,90.0,Accept\n",
        encoding="utf-8",
    )

    # Run B (unanalyzed)
    run_b = project_root / "run_b"
    (run_b / "papers").mkdir(parents=True)
    (run_b / "papers" / "paper_b.pdf").write_bytes(b"%PDF-1.4\npaperB")

    meta_a = client.get("/api/runs/run_a/metadata").json()
    meta_b = client.get("/api/runs/run_b/metadata").json()

    assert meta_a["original_filename"] == "paper_a.pdf"
    assert meta_a["paper_title"] == "Title A"
    assert meta_a["overall_score"] == 90.0

    assert meta_b["original_filename"] == "paper_b.pdf"
    assert meta_b["paper_title"] is None
    assert meta_b["overall_score"] is None

    revs_b = client.get("/api/all-reviews/run_b").json()
    assert revs_b["reviews"] == []


def test_invalid_or_missing_run_id_returns_404(tmp_path, monkeypatch):
    """7. Invalid or missing run ID returns a clear 404 error without substituting another run."""
    project_root = _setup_test_env(tmp_path)
    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(project_root))
    client = TestClient(web_app.app)

    # Valid run exists
    run_valid = project_root / "run_valid"
    (run_valid / "papers").mkdir(parents=True)
    (run_valid / "papers" / "valid.pdf").write_bytes(b"%PDF-1.4\nvalid")

    res = client.get("/api/runs/totally_invalid_run_name_xyz/metadata")
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


def test_completed_historical_fixture_reopens_with_saved_report(tmp_path, monkeypatch):
    """8. A completed historical fixture reopens with its saved report intact."""
    project_root = _setup_test_env(tmp_path)
    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(project_root))
    client = TestClient(web_app.app)

    run_hist = project_root / "run_historical"
    (run_hist / "papers").mkdir(parents=True)
    (run_hist / "papers" / "hist.pdf").write_bytes(b"%PDF-1.4\nhist")
    (run_hist / "outputs" / "reports").mkdir(parents=True)
    (run_hist / "outputs" / "reviews").mkdir(parents=True)
    (run_hist / "outputs" / "reports" / "report.csv").write_text(
        "paper_filename,title,overall_score,recommendation,final_system_status\nhist,Historical Paper Title,77.0,Needs Manual Review,Needs Manual Review\n",
        encoding="utf-8",
    )
    (run_hist / "outputs" / "reviews" / "review_hist.md").write_text("# Review: Historical Paper Title\nGreat paper", encoding="utf-8")

    meta = client.get("/api/runs/run_historical/metadata").json()
    assert meta["has_output"] is True
    assert meta["paper_title"] == "Historical Paper Title"
    assert meta["overall_score"] == 77.0
    assert meta["final_system_status"] == "Needs Manual Review"

    revs = client.get("/api/all-reviews/run_historical").json()
    assert len(revs["reviews"]) == 1
    assert revs["reviews"][0]["filename"] == "review_hist.md"


def test_legacy_runs_without_metadata_fallback_gracefully(tmp_path, monkeypatch):
    """9. Old runs without the new metadata field still load (backward compatibility)."""
    project_root = _setup_test_env(tmp_path)
    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(project_root))

    run_legacy = project_root / "run_legacy_20261020_000000_12345678"
    (run_legacy / "papers").mkdir(parents=True)
    (run_legacy / "papers" / "old_study.pdf").write_bytes(b"%PDF-1.4\nlegacy")

    runs = web_app._find_run_dirs()
    assert len(runs) == 1
    r = runs[0]
    assert r["name"] == "run_legacy_20261020_000000_12345678"
    assert r["original_filename"] == "old_study.pdf"
    assert r["status"] == "ready"
    assert r["display_name"] == "old_study.pdf · Run 1 of 1"


def test_api_response_contract_backward_compatibility(tmp_path, monkeypatch):
    """10. Existing API response shapes are unchanged except for additive optional fields."""
    project_root = _setup_test_env(tmp_path)
    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(project_root))
    client = TestClient(web_app.app)

    run_dir = project_root / "run_contract"
    (run_dir / "papers").mkdir(parents=True)
    (run_dir / "papers" / "test.pdf").write_bytes(b"%PDF-1.4\ncontract")

    res = client.get("/api/runs")
    assert res.status_code == 200
    runs = res.json()["runs"]
    assert len(runs) == 1
    r = runs[0]

    # Required contract keys
    expected_keys = [
        "name", "path", "paper_count", "has_output", "original_filename",
        "safe_filename", "paper_title", "upload_timestamp", "upload_date_str",
        "status", "status_desc", "run_number", "total_runs_for_paper",
        "run_label", "display_name", "formatted_summary", "formatted_secondary"
    ]
    for key in expected_keys:
        assert key in r, f"Missing expected contract key: {key}"
