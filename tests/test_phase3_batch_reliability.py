import os
import json
import time
import shutil
import hashlib
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

import web_app


@pytest.fixture
def test_env(tmp_path, monkeypatch):
    """Setup isolated test environment with config and mock project root."""
    project_root = Path(tmp_path)
    config_dir = project_root / "config"
    prompts_dir = config_dir / "prompts"
    prompts_dir.mkdir(parents=True)

    (config_dir / "criteria.yaml").write_text("criteria: []\n", encoding="utf-8")
    (prompts_dir / "extractor_system.txt").write_text("test prompt", encoding="utf-8")
    (prompts_dir / "extractor_user.txt").write_text("test prompt", encoding="utf-8")
    (prompts_dir / "extractor_criterion.txt").write_text("test prompt", encoding="utf-8")
    (prompts_dir / "synthesizer_system.txt").write_text("test prompt", encoding="utf-8")
    (prompts_dir / "synthesizer_user.txt").write_text("test prompt", encoding="utf-8")

    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(project_root))
    monkeypatch.setattr(web_app, "BATCH_QUEUE_FILE", str(project_root / "batch_queue.json"))
    monkeypatch.setattr(web_app, "_batch_state", {"active": False, "cancelled": False})

    client = TestClient(web_app.app)
    return project_root, client


def test_multi_file_upload_stages_separate_items_with_positions(test_env):
    """Requirement 1 & 2: Uploading multiple PDFs in one request stages them into separate items with run IDs & positions."""
    project_root, client = test_env

    pdf1 = b"%PDF-1.4\nPaper 1 Unique Content Alpha"
    pdf2 = b"%PDF-1.4\nPaper 2 Unique Content Beta"
    pdf3 = b"%PDF-1.4\nPaper 3 Unique Content Gamma"

    files = [
        ("files", ("paper1.pdf", pdf1, "application/pdf")),
        ("files", ("paper2.pdf", pdf2, "application/pdf")),
        ("files", ("paper3.pdf", pdf3, "application/pdf")),
    ]

    response = client.post("/api/upload", files=files, data={"duplicate_policy": "skip"})
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "batch_staged"
    assert data["total_files"] == 3
    assert data["queued_count"] == 3
    assert data["skipped_count"] == 0
    assert len(data["items"]) == 3

    # Check separate positions and isolated run IDs
    positions = [item["position"] for item in data["items"]]
    assert positions == [1, 2, 3]

    run_dirs = [item["run_dir"] for item in data["items"]]
    assert len(set(run_dirs)) == 3  # All run dirs are distinct

    for item in data["items"]:
        assert item["status"] == "queued"
        assert item["progress_pct"] == 0
        assert item["error_message"] is None
        # Verify run directory was actually created on disk
        target_dir = project_root / item["run_dir"]
        assert target_dir.is_dir()
        assert (target_dir / "papers" / item["safe_filename"]).is_file()

    # Verify atomic persistence in batch_queue.json
    queue_path = project_root / "batch_queue.json"
    assert queue_path.is_file()
    saved_queue = json.loads(queue_path.read_text(encoding="utf-8"))
    assert len(saved_queue["items"]) == 3


def test_failure_isolation_between_queued_papers(test_env, monkeypatch):
    """Requirement 3: One paper's failure must not stop other queued papers from processing."""
    project_root, client = test_env

    pdf1 = b"%PDF-1.4\nManuscript One Failing Paper"
    pdf2 = b"%PDF-1.4\nManuscript Two Succeeding Paper"

    files = [
        ("files", ("fail_paper.pdf", pdf1, "application/pdf")),
        ("files", ("success_paper.pdf", pdf2, "application/pdf")),
    ]

    upload_res = client.post("/api/upload", files=files)
    assert upload_res.status_code == 200

    call_history = []

    def mock_run_pipeline(run_dir, run_id, mode, overrides):
        call_history.append(run_dir)
        if "fail_paper" in run_dir or any("fail_paper" in f for f in os.listdir(os.path.join(run_dir, "papers"))):
            raise RuntimeError("Simulated extraction parsing error in paper 1")
        # For the succeeding paper, mock report generation
        rep_dir = os.path.join(run_dir, "outputs", "reports")
        os.makedirs(rep_dir, exist_ok=True)
        csv_path = os.path.join(rep_dir, "consolidated_reviews_1.csv")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("overall_score,recommendation,final_system_status\n85.5,Accept,Accept (Provisional)\n")

    monkeypatch.setattr(web_app, "_run_pipeline", mock_run_pipeline)

    # Run queue synchronously
    web_app._run_batch_queue(mode="standard")

    # Verify both papers were attempted
    assert len(call_history) == 2

    # Verify persisted state reflects failure isolation
    queue_data = web_app._load_batch_queue()
    items = queue_data["items"]
    assert len(items) == 2

    item1 = items[0]
    assert item1["status"] == "failed"
    assert "Simulated extraction parsing error" in item1["error_message"]
    assert item1["stage"] == "Failed"

    item2 = items[1]
    assert item2["status"] == "completed"
    assert item2["progress_pct"] == 100
    assert item2["overall_score"] == 85.5
    assert item2["provisional_recommendation"] == "Accept"
    assert item2["error_message"] is None


def test_retry_failed_and_interrupted_items_without_rerunning_completed(test_env, monkeypatch):
    """Requirement 4: Retry re-queues only failed or interrupted items; completed and skipped are preserved."""
    project_root, client = test_env

    # Seed batch_queue.json with 4 items: completed, failed, interrupted, skipped
    queue_data = {
        "queue_version": 1,
        "duplicate_policy": "skip",
        "created_at": "2026-10-27T10:00:00Z",
        "updated_at": "2026-10-27T10:00:00Z",
        "items": [
            {
                "item_id": "item_1",
                "position": 1,
                "original_filename": "paper1.pdf",
                "safe_filename": "paper1.pdf",
                "paper_title": "Paper 1",
                "sha256": "hash1",
                "file_size_bytes": 100,
                "run_dir": "run_1",
                "status": "completed",
                "stage": "Completed",
                "progress_pct": 100,
                "overall_score": 90.0,
                "error_message": None,
            },
            {
                "item_id": "item_2",
                "position": 2,
                "original_filename": "paper2.pdf",
                "safe_filename": "paper2.pdf",
                "paper_title": "Paper 2",
                "sha256": "hash2",
                "file_size_bytes": 100,
                "run_dir": "run_2",
                "status": "failed",
                "stage": "Failed",
                "progress_pct": 20,
                "overall_score": None,
                "error_message": "LLM Timeout",
            },
            {
                "item_id": "item_3",
                "position": 3,
                "original_filename": "paper3.pdf",
                "safe_filename": "paper3.pdf",
                "paper_title": "Paper 3",
                "sha256": "hash3",
                "file_size_bytes": 100,
                "run_dir": "run_3",
                "status": "interrupted",
                "stage": "Interrupted by server restart",
                "progress_pct": 50,
                "overall_score": None,
                "error_message": "Process terminated while running; ready to retry",
            },
            {
                "item_id": "item_4",
                "position": 4,
                "original_filename": "paper4.pdf",
                "safe_filename": "paper4.pdf",
                "paper_title": "Paper 4",
                "sha256": "hash4",
                "file_size_bytes": 100,
                "run_dir": "run_1",
                "status": "duplicate_skipped",
                "stage": "Skipped (Duplicate)",
                "progress_pct": 100,
                "overall_score": 90.0,
                "error_message": "Duplicate of run_1",
            },
        ],
    }
    web_app._save_batch_queue(queue_data)

    # Mock runner thread start to inspect state without triggering background loop
    monkeypatch.setattr(web_app, "_run_batch_queue", lambda mode="standard": None)

    response = client.post("/api/batch/retry-failed")
    assert response.status_code == 200
    res = response.json()
    assert res["status"] == "retrying"
    assert res["retried_count"] == 2

    # Check updated queue
    updated = web_app._load_batch_queue()
    items = {it["item_id"]: it for it in updated["items"]}

    # Item 1 was completed, must still be completed!
    assert items["item_1"]["status"] == "completed"
    assert items["item_1"]["overall_score"] == 90.0

    # Item 2 was failed, must now be queued!
    assert items["item_2"]["status"] == "queued"
    assert items["item_2"]["error_message"] is None
    assert items["item_2"]["progress_pct"] == 0

    # Item 3 was interrupted, must now be queued!
    assert items["item_3"]["status"] == "queued"
    assert items["item_3"]["error_message"] is None
    assert items["item_3"]["progress_pct"] == 0

    # Item 4 was duplicate_skipped, must still be duplicate_skipped!
    assert items["item_4"]["status"] == "duplicate_skipped"


def test_restart_recovery_marks_running_items_interrupted(test_env):
    """Requirement 6: Unfinished running items are marked interrupted on startup."""
    project_root, client = test_env

    # Seed queue with a 'running' item simulating a crash/restart
    queue_data = {
        "queue_version": 1,
        "duplicate_policy": "skip",
        "created_at": "2026-10-27T10:00:00Z",
        "updated_at": "2026-10-27T10:00:00Z",
        "items": [
            {
                "item_id": "crashed_item",
                "position": 1,
                "original_filename": "interrupted.pdf",
                "safe_filename": "interrupted.pdf",
                "paper_title": "Interrupted Paper",
                "sha256": "crash_hash",
                "file_size_bytes": 200,
                "run_dir": "run_crashed",
                "status": "running",
                "stage": "Extracting criteria 4/8",
                "progress_pct": 50,
                "error_message": None,
            }
        ],
    }
    web_app._save_batch_queue(queue_data)

    # Trigger recovery function
    web_app._recover_batch_queue_on_startup()

    recovered = web_app._load_batch_queue()
    item = recovered["items"][0]
    assert item["status"] == "interrupted"
    assert item["stage"] == "Interrupted by server restart"
    assert "Process terminated while running; ready to retry" in item["error_message"]
    assert item["finished_at"] is not None


def test_sha256_duplicate_prevention_skip_vs_force_new(test_env):
    """Requirement 7: SHA-256 duplicate handling cleanly supports both skip and force_new."""
    project_root, client = test_env

    identical_content = b"%PDF-1.4\nDeterministic SHA256 Paper Content 123456789"

    # Upload first copy
    res1 = client.post(
        "/api/upload",
        files=[("files", ("first_copy.pdf", identical_content, "application/pdf"))],
        data={"duplicate_policy": "skip"},
    )
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["queued_count"] == 1
    run_dir1 = data1["items"][0]["run_dir"]

    # Upload duplicate with duplicate_policy='skip'
    res2 = client.post(
        "/api/upload",
        files=[("files", ("duplicate_copy.pdf", identical_content, "application/pdf"))],
        data={"duplicate_policy": "skip"},
    )
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["queued_count"] == 0
    assert data2["skipped_count"] == 1
    assert data2["items"][0]["status"] == "duplicate_skipped"
    assert data2["items"][0]["run_dir"] == run_dir1

    # Upload duplicate with duplicate_policy='force_new'
    res3 = client.post(
        "/api/upload",
        files=[("files", ("clean_rerun_copy.pdf", identical_content, "application/pdf"))],
        data={"duplicate_policy": "force_new"},
    )
    assert res3.status_code == 200
    data3 = res3.json()
    assert data3["queued_count"] == 1
    assert data3["skipped_count"] == 0
    assert data3["items"][0]["status"] == "queued"
    assert data3["items"][0]["run_dir"] != run_dir1  # Created a new run directory!


def test_run_isolation_separate_directories_and_metadata(test_env):
    """Requirement 8: Each paper preserves complete isolation of inputs, files, metadata, and reports."""
    project_root, client = test_env

    content_a = b"%PDF-1.4\nPaper A Alpha Specific Hypothesis"
    content_b = b"%PDF-1.4\nPaper B Beta Specific Hypothesis"

    res = client.post(
        "/api/upload",
        files=[
            ("files", ("paper_a.pdf", content_a, "application/pdf")),
            ("files", ("paper_b.pdf", content_b, "application/pdf")),
        ],
    )
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 2

    dir_a = project_root / items[0]["run_dir"]
    dir_b = project_root / items[1]["run_dir"]

    assert dir_a != dir_b
    assert dir_a.is_dir()
    assert dir_b.is_dir()

    # Verify paper file isolation
    assert (dir_a / "papers" / "paper_a.pdf").read_bytes() == content_a
    assert not (dir_a / "papers" / "paper_b.pdf").exists()

    assert (dir_b / "papers" / "paper_b.pdf").read_bytes() == content_b
    assert not (dir_b / "papers" / "paper_a.pdf").exists()

    # Verify metadata isolation
    meta_a = json.loads((dir_a / "input" / "run_metadata.json").read_text(encoding="utf-8"))
    meta_b = json.loads((dir_b / "input" / "run_metadata.json").read_text(encoding="utf-8"))

    assert meta_a["original_filename"] == "paper_a.pdf"
    assert meta_a["file_hash_sha256"] == hashlib.sha256(content_a).hexdigest()

    assert meta_b["original_filename"] == "paper_b.pdf"
    assert meta_b["file_hash_sha256"] == hashlib.sha256(content_b).hexdigest()


def test_batch_api_endpoints_and_single_item_retry(test_env, monkeypatch):
    """Requirement 4 & API: Test GET /api/batch-queue, retry single item, clear queue, and stop batch."""
    project_root, client = test_env

    # 1. Test GET /api/batch-queue
    res = client.get("/api/batch-queue")
    assert res.status_code == 200
    data = res.json()
    assert "counts" in data
    assert "items" in data

    # 2. Add a failed item and retry single item
    queue_data = {
        "queue_version": 1,
        "duplicate_policy": "skip",
        "created_at": "2026-10-27T10:00:00Z",
        "updated_at": "2026-10-27T10:00:00Z",
        "items": [
            {
                "item_id": "failed_item_99",
                "position": 1,
                "original_filename": "single_retry.pdf",
                "safe_filename": "single_retry.pdf",
                "paper_title": "Single Retry Paper",
                "sha256": "hash99",
                "file_size_bytes": 100,
                "run_dir": "run_99",
                "status": "failed",
                "stage": "Failed",
                "progress_pct": 10,
                "error_message": "Single item test failure",
            }
        ],
    }
    web_app._save_batch_queue(queue_data)

    monkeypatch.setattr(web_app, "_run_batch_queue", lambda mode="standard": None)

    retry_res = client.post("/api/batch/items/failed_item_99/retry")
    assert retry_res.status_code == 200
    assert retry_res.json()["status"] == "retrying"

    updated = web_app._load_batch_queue()
    assert updated["items"][0]["status"] == "queued"
    assert updated["items"][0]["error_message"] is None

    # 3. Test POST /api/batch/clear
    updated["items"][0]["status"] = "completed"
    web_app._save_batch_queue(updated)

    clear_res = client.post("/api/batch/clear")
    assert clear_res.status_code == 200
    assert clear_res.json()["status"] == "cleared"
    assert clear_res.json()["remaining_count"] == 0
