"""Comprehensive integration tests for Step 3: Lifecycle, Cache Mutation & Restart Recovery Verification.

All tests operate strictly on isolated tmp_path fixtures with monkeypatched paths.
No production files, historical runs, original PDFs, or user data are touched.
"""

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Dict, Any

import pytest
from starlette.testclient import TestClient

import web_app
from core.data_models import Paper, PaperMetadata
from core.paper_overview import (
    PaperOverviewData,
    PaperOverviewSection,
    compute_file_sha256,
    load_paper_overview_cache,
    save_paper_overview_cache,
)


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Setup completely isolated workspace environment in tmp_path."""
    project_root = Path(tmp_path)
    config_dir = project_root / "config"
    prompts_dir = config_dir / "prompts"
    prompts_dir.mkdir(parents=True)

    (config_dir / "criteria.yaml").write_text("criteria: []\n", encoding="utf-8")
    (prompts_dir / "extractor_system.txt").write_text("system prompt", encoding="utf-8")
    (prompts_dir / "extractor_user.txt").write_text("user prompt", encoding="utf-8")
    (prompts_dir / "extractor_criterion.txt").write_text("criterion prompt", encoding="utf-8")
    (prompts_dir / "synthesizer_system.txt").write_text("synthesizer system", encoding="utf-8")
    (prompts_dir / "synthesizer_user.txt").write_text("synthesizer user", encoding="utf-8")
    (prompts_dir / "paper_overview_system.txt").write_text("overview system", encoding="utf-8")
    (prompts_dir / "paper_overview_user.txt").write_text("overview user", encoding="utf-8")

    batch_queue_path = project_root / "batch_queue.json"

    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(project_root))
    monkeypatch.setattr(web_app, "BATCH_QUEUE_FILE", str(batch_queue_path))
    monkeypatch.setattr(web_app, "_batch_state", {"active": False, "cancelled": False})
    monkeypatch.setattr(web_app, "_active_runs", {})
    monkeypatch.setattr(web_app, "_overview_jobs", {})

    client = TestClient(web_app.app)
    return project_root, client


# ===========================================================================
# 1. BATCH QUEUE PERSISTENCE & RESTART RECOVERY
# ===========================================================================

def test_batch_queue_atomic_persistence(isolated_env):
    """Verify batch queue state is saved atomically with schema integrity."""
    project_root, client = isolated_env
    queue_file = project_root / "batch_queue.json"

    test_queue = {
        "queue_version": "1.0",
        "duplicate_policy": "skip",
        "created_at": "2026-10-27T10:00:00Z",
        "updated_at": "2026-10-27T10:00:00Z",
        "items": [
            {
                "item_id": "item-001",
                "run_dir": "run_test_001",
                "original_filename": "paper1.pdf",
                "sha256": "abc123hash",
                "status": "queued",
                "queue_position": 1,
                "error_message": None,
            }
        ],
    }

    web_app._save_batch_queue(test_queue)
    assert queue_file.is_file()

    loaded = web_app._load_batch_queue()
    assert loaded["queue_version"] == "1.0"
    assert len(loaded["items"]) == 1
    assert loaded["items"][0]["item_id"] == "item-001"
    assert loaded["items"][0]["status"] == "queued"


def test_restart_recovery_transitions_running_to_interrupted(isolated_env):
    """Verify application restart recovery marks running items interrupted, preserving completed & failed."""
    project_root, client = isolated_env

    staged_queue = {
        "queue_version": "1.0",
        "duplicate_policy": "skip",
        "created_at": "2026-10-27T10:00:00Z",
        "updated_at": "2026-10-27T10:00:00Z",
        "items": [
            {
                "item_id": "item-done",
                "run_dir": "run_test_done",
                "original_filename": "paper_done.pdf",
                "sha256": "hash_done",
                "status": "completed",
                "queue_position": 1,
                "error_message": None,
            },
            {
                "item_id": "item-running",
                "run_dir": "run_test_running",
                "original_filename": "paper_running.pdf",
                "sha256": "hash_running",
                "status": "running",
                "queue_position": 2,
                "error_message": None,
            },
            {
                "item_id": "item-failed",
                "run_dir": "run_test_failed",
                "original_filename": "paper_failed.pdf",
                "sha256": "hash_failed",
                "status": "failed",
                "queue_position": 3,
                "error_message": "Prior explicit error",
            },
            {
                "item_id": "item-queued",
                "run_dir": "run_test_queued",
                "original_filename": "paper_queued.pdf",
                "sha256": "hash_queued",
                "status": "queued",
                "queue_position": 4,
                "error_message": None,
            },
        ],
    }
    web_app._save_batch_queue(staged_queue)

    # Simulate startup recovery execution
    web_app._recover_batch_queue_on_startup()

    recovered = web_app._load_batch_queue()
    items_by_id = {it["item_id"]: it for it in recovered["items"]}

    # Completed must remain completed
    assert items_by_id["item-done"]["status"] == "completed"
    assert items_by_id["item-done"]["error_message"] is None

    # Running must be marked interrupted with recovery notice
    assert items_by_id["item-running"]["status"] == "interrupted"
    assert "Interrupted by server restart" in items_by_id["item-running"]["stage"]
    assert "terminated while running" in items_by_id["item-running"]["error_message"]

    # Failed must remain failed with original message
    assert items_by_id["item-failed"]["status"] == "failed"
    assert items_by_id["item-failed"]["error_message"] == "Prior explicit error"

    # Queued must remain queued
    assert items_by_id["item-queued"]["status"] == "queued"
    assert items_by_id["item-queued"]["error_message"] is None


def test_batch_retry_operates_only_on_interrupted_and_failed(isolated_env, monkeypatch):
    """Verify batch retry transitions only failed and interrupted items to queued, leaving completed untouched."""
    project_root, client = isolated_env

    staged_queue = {
        "queue_version": "1.0",
        "duplicate_policy": "skip",
        "created_at": "2026-10-27T10:00:00Z",
        "updated_at": "2026-10-27T10:00:00Z",
        "items": [
            {
                "item_id": "item-done",
                "run_dir": "run_test_done",
                "original_filename": "paper_done.pdf",
                "sha256": "hash_done",
                "status": "completed",
                "queue_position": 1,
                "error_message": None,
            },
            {
                "item_id": "item-interrupted",
                "run_dir": "run_test_interrupted",
                "original_filename": "paper_interrupted.pdf",
                "sha256": "hash_interrupted",
                "status": "interrupted",
                "queue_position": 2,
                "error_message": "Interrupted",
            },
            {
                "item_id": "item-failed",
                "run_dir": "run_test_failed",
                "original_filename": "paper_failed.pdf",
                "sha256": "hash_failed",
                "status": "failed",
                "queue_position": 3,
                "error_message": "Failed extraction",
            },
        ],
    }
    web_app._save_batch_queue(staged_queue)

    # Intercept _run_batch_queue to prevent actual async background processing in this test
    monkeypatch.setattr(web_app, "_run_batch_queue", lambda mode: None)

    resp = client.post("/api/batch/retry-failed")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "retrying"
    assert data["retried_count"] == 2

    # Check queue states
    loaded = web_app._load_batch_queue()
    items_by_id = {it["item_id"]: it for it in loaded["items"]}
    assert items_by_id["item-done"]["status"] == "completed"
    assert items_by_id["item-interrupted"]["status"] == "queued"
    assert items_by_id["item-interrupted"]["error_message"] is None
    assert items_by_id["item-failed"]["status"] == "queued"
    assert items_by_id["item-failed"]["error_message"] is None


def test_batch_single_item_retry(isolated_env, monkeypatch):
    """Verify retrying a single item by item ID touches only that item."""
    project_root, client = isolated_env

    staged_queue = {
        "queue_version": "1.0",
        "duplicate_policy": "skip",
        "created_at": "2026-10-27T10:00:00Z",
        "updated_at": "2026-10-27T10:00:00Z",
        "items": [
            {
                "item_id": "item-fail-1",
                "run_dir": "run_test_1",
                "original_filename": "paper1.pdf",
                "sha256": "hash_1",
                "status": "failed",
                "queue_position": 1,
                "error_message": "Timeout",
            },
            {
                "item_id": "item-fail-2",
                "run_dir": "run_test_2",
                "original_filename": "paper2.pdf",
                "sha256": "hash_2",
                "status": "failed",
                "queue_position": 2,
                "error_message": "Parser crash",
            },
        ],
    }
    web_app._save_batch_queue(staged_queue)
    monkeypatch.setattr(web_app, "_run_batch_queue", lambda mode: None)

    resp = client.post("/api/batch/items/item-fail-2/retry")
    assert resp.status_code == 200

    loaded = web_app._load_batch_queue()
    items_by_id = {it["item_id"]: it for it in loaded["items"]}
    # item 1 should remain failed with error_message intact
    assert items_by_id["item-fail-1"]["status"] == "failed"
    assert items_by_id["item-fail-1"]["error_message"] == "Timeout"
    # item 2 should be requeued with error_message cleared
    assert items_by_id["item-fail-2"]["status"] == "queued"
    assert items_by_id["item-fail-2"]["error_message"] is None


def test_batch_duplicate_policy_skip_vs_force_new(isolated_env):
    """Verify duplicate policy correctly handles existing sha256 hashes."""
    project_root, client = isolated_env

    pdf_bytes = b"%PDF-1.4\nDuplicate test manuscript content"
    expected_hash = hashlib.sha256(pdf_bytes).hexdigest()

    # Pre-populate queue with existing completed item
    staged_queue = {
        "queue_version": "1.0",
        "duplicate_policy": "skip",
        "created_at": "2026-10-27T10:00:00Z",
        "updated_at": "2026-10-27T10:00:00Z",
        "items": [
            {
                "item_id": "existing-001",
                "run_dir": "run_existing_001",
                "original_filename": "orig.pdf",
                "sha256": expected_hash,
                "status": "completed",
                "position": 1,
                "error_message": None,
            }
        ],
    }
    web_app._save_batch_queue(staged_queue)

    # 1. Upload duplicate with policy=skip
    resp_skip = client.post(
        "/api/upload",
        data={"is_batch": "true", "duplicate_policy": "skip"},
        files=[("files", ("duplicate.pdf", pdf_bytes, "application/pdf"))],
    )
    assert resp_skip.status_code == 200
    data_skip = resp_skip.json()
    assert data_skip["status"] == "batch_staged"
    assert data_skip["skipped_count"] == 1
    assert data_skip["queued_count"] == 0

    # 2. Upload duplicate with policy=force_new
    resp_force = client.post(
        "/api/upload",
        data={"is_batch": "true", "duplicate_policy": "force_new"},
        files=[("files", ("duplicate_new.pdf", pdf_bytes, "application/pdf"))],
    )
    assert resp_force.status_code == 200
    data_force = resp_force.json()
    assert data_force["status"] == "batch_staged"
    assert data_force["skipped_count"] == 0
    assert data_force["queued_count"] == 1


# ===========================================================================
# 2. CACHE INVALIDATION ON SOURCE MANUSCRIPT MUTATION
# ===========================================================================

def test_cache_invalidation_when_source_pdf_bytes_mutate(tmp_path):
    """Verify that mutating 1 byte of the source manuscript immediately invalidates overview cache (status: stale)."""
    run_dir = tmp_path / "run_test_cache_invalidation"
    papers_dir = run_dir / "papers"
    outputs_dir = run_dir / "outputs"
    papers_dir.mkdir(parents=True)
    outputs_dir.mkdir(parents=True)

    original_bytes = b"%PDF-1.4\nOriginal Academic Manuscript Text for Cache Verification"
    pdf_file = papers_dir / "paper.pdf"
    pdf_file.write_bytes(original_bytes)

    orig_sha256 = compute_file_sha256(str(pdf_file))

    # Construct valid overview cache
    dummy_section = PaperOverviewSection(
        title="Test Section",
        status="verified_content",
        content="Ground-truth content verified from text.",
    )
    overview_data = PaperOverviewData(
        run_dir=str(run_dir.name),
        paper_filename="paper.pdf",
        file_hash_sha256=orig_sha256,
        generated_at="2026-10-27T10:00:00Z",
        generator_model="test-model",
        metadata={"title": "Test Paper"},
        sections={
            "research_problem": dummy_section,
            "research_objectives": dummy_section,
            "methodology": dummy_section,
            "main_contributions": dummy_section,
            "experimental_setup": dummy_section,
            "key_results": dummy_section,
            "limitations": dummy_section,
            "future_work": dummy_section,
        },
    )
    save_paper_overview_cache(str(run_dir), overview_data)

    # 1. Valid cache matches disk bytes
    loaded, status = load_paper_overview_cache(str(run_dir), expected_sha256=orig_sha256)
    assert status == "valid"
    assert loaded is not None

    # 2. Mutate exactly 1 byte in the temporary PDF fixture
    mutated_bytes = b"%PDF-1.4\nOriginal Academic Manuscript Text for Cache Xerification"  # 'V' -> 'X'
    pdf_file.write_bytes(mutated_bytes)
    mutated_sha256 = compute_file_sha256(str(pdf_file))
    assert mutated_sha256 != orig_sha256

    # Cache loader must detect mismatch and return stale
    loaded_stale, status_stale = load_paper_overview_cache(str(run_dir), expected_sha256=mutated_sha256)
    assert status_stale == "stale"
    assert loaded_stale is None

    # 3. Reverting the mutated byte restores valid cache status without regeneration
    pdf_file.write_bytes(original_bytes)
    restored_sha256 = compute_file_sha256(str(pdf_file))
    assert restored_sha256 == orig_sha256

    loaded_restored, status_restored = load_paper_overview_cache(str(run_dir), expected_sha256=restored_sha256)
    assert status_restored == "valid"
    assert loaded_restored is not None


def test_api_overview_endpoint_returns_stale_when_file_bytes_change(isolated_env):
    """Verify GET /api/overview/{run_dir} returns status: stale when source PDF bytes differ from cache."""
    project_root, client = isolated_env
    run_dir = project_root / "run_upload_20261027_test_stale"
    papers_dir = run_dir / "papers"
    outputs_dir = run_dir / "outputs"
    papers_dir.mkdir(parents=True)
    outputs_dir.mkdir(parents=True)

    original_bytes = b"%PDF-1.4\nOriginal Manuscript"
    pdf_file = papers_dir / "paper.pdf"
    pdf_file.write_bytes(original_bytes)
    orig_sha256 = compute_file_sha256(str(pdf_file))

    dummy_section = PaperOverviewSection(
        title="Test Section",
        status="verified_content",
        content="Valid overview content.",
    )
    overview_data = PaperOverviewData(
        run_dir=run_dir.name,
        paper_filename="paper.pdf",
        file_hash_sha256=orig_sha256,
        generated_at="2026-10-27T10:00:00Z",
        generator_model="test-model",
        metadata={"title": "Test Paper"},
        sections={
            "research_problem": dummy_section,
            "research_objectives": dummy_section,
            "methodology": dummy_section,
            "main_contributions": dummy_section,
            "experimental_setup": dummy_section,
            "key_results": dummy_section,
            "limitations": dummy_section,
            "future_work": dummy_section,
        },
    )
    save_paper_overview_cache(str(run_dir), overview_data)

    # Baseline: cached and valid
    resp_init = client.get(f"/api/overview/{run_dir.name}")
    assert resp_init.status_code == 200
    assert resp_init.json()["status"] == "cached"

    # Mutate manuscript bytes
    pdf_file.write_bytes(b"%PDF-1.4\nMutated Manuscript Content")

    # Query GET endpoint: must report stale and refuse to return stale overview
    resp_stale = client.get(f"/api/overview/{run_dir.name}")
    assert resp_stale.status_code == 200
    data_stale = resp_stale.json()
    assert data_stale["status"] == "stale"
    assert "Manuscript on disk has changed" in data_stale["message"]


# ===========================================================================
# 3. SOFT-DELETE & RESTORE BEHAVIOR WITH EVIDENCE PRESERVATION
# ===========================================================================

def test_soft_delete_and_restore_preserves_all_artifacts(isolated_env):
    """Verify soft-delete marks run soft_deleted.json, preserves all artifacts in place, and restores cleanly."""
    project_root, client = isolated_env
    run_dir = project_root / "run_upload_20261027_test_lifecycle"
    papers_dir = run_dir / "papers"
    evidence_dir = run_dir / "outputs" / "evidence"
    reviews_dir = run_dir / "outputs" / "reviews"
    reports_dir = run_dir / "outputs" / "reports"
    for d in (papers_dir, evidence_dir, reviews_dir, reports_dir):
        d.mkdir(parents=True)

    # Create artifacts
    (papers_dir / "test_paper.pdf").write_bytes(b"%PDF-1.4\nAcademic paper content")
    evidence_content = [{"criterion_id": "data_quality", "quote": "Valid quote", "verification_status": "Verified"}]
    (evidence_dir / "test_paper_evidence.json").write_text(json.dumps(evidence_content), encoding="utf-8")
    (reviews_dir / "review_test.md").write_text("# Review: Test Paper\nOverall Score: 85.0", encoding="utf-8")
    (reports_dir / "consolidated_report.csv").write_text("paper,score\ntest_paper,85.0", encoding="utf-8")

    # Verify run is visible initially
    resp_initial = client.get("/api/runs")
    assert resp_initial.status_code == 200
    active_runs_init = [r["name"] for r in resp_initial.json()["runs"]]
    assert run_dir.name in active_runs_init

    # 1. Soft-delete the run
    resp_del = client.post(f"/api/runs/{run_dir.name}/delete")
    assert resp_del.status_code == 200
    del_data = resp_del.json()
    assert del_data["status"] == "soft_deleted"

    # Soft deletion marker must exist on disk
    marker_file = run_dir / "soft_deleted.json"
    assert marker_file.is_file()
    marker_json = json.loads(marker_file.read_text(encoding="utf-8"))
    assert marker_json["is_deleted"] is True

    # All artifacts must be completely intact inside run_dir
    assert (run_dir / "papers" / "test_paper.pdf").is_file()
    assert (run_dir / "outputs" / "evidence" / "test_paper_evidence.json").is_file()
    assert (run_dir / "outputs" / "reviews" / "review_test.md").is_file()
    assert (run_dir / "outputs" / "reports" / "consolidated_report.csv").is_file()

    # Excluded from active /api/runs
    resp_runs = client.get("/api/runs")
    assert resp_runs.status_code == 200
    active_run_names = [r["name"] for r in resp_runs.json()["runs"]]
    assert run_dir.name not in active_run_names

    # Present in /api/deleted-runs
    resp_deleted = client.get("/api/deleted-runs")
    assert resp_deleted.status_code == 200
    deleted_run_names = [r["name"] for r in resp_deleted.json()["deleted_runs"]]
    assert run_dir.name in deleted_run_names

    # Direct access to deleted run blocked with 404
    resp_direct = client.get(f"/api/overview/{run_dir.name}")
    assert resp_direct.status_code == 404

    # 2. Restore the run
    resp_restore = client.post(f"/api/runs/{run_dir.name}/restore")
    assert resp_restore.status_code == 200
    rest_data = resp_restore.json()
    assert rest_data["status"] == "restored"

    # soft_deleted.json marker must be deleted
    assert not marker_file.exists()

    # Active run list includes it again
    resp_runs_restored = client.get("/api/runs")
    assert resp_runs_restored.status_code == 200
    active_restored = [r["name"] for r in resp_runs_restored.json()["runs"]]
    assert run_dir.name in active_restored

    # All artifacts intact after restoration
    assert (run_dir / "outputs" / "evidence" / "test_paper_evidence.json").is_file()
    restored_evidence = json.loads((run_dir / "outputs" / "evidence" / "test_paper_evidence.json").read_text())
    assert restored_evidence[0]["verification_status"] == "Verified"
    assert (run_dir / "outputs" / "reviews" / "review_test.md").read_text().startswith("# Review: Test Paper")


def test_soft_delete_rejects_active_running_run(isolated_env):
    """Verify attempting to soft-delete an in-flight active run is blocked with HTTP 409."""
    project_root, client = isolated_env
    run_dir = project_root / "run_upload_20261027_active"
    papers_dir = run_dir / "papers"
    papers_dir.mkdir(parents=True)
    (papers_dir / "paper.pdf").write_bytes(b"%PDF-1.4\nActive Content")

    # Mark run active in memory
    web_app._active_runs[run_dir.name] = {"stage": "Running"}

    resp = client.post(f"/api/runs/{run_dir.name}/delete")
    assert resp.status_code == 409
    assert "currently in progress" in resp.json()["detail"]
    assert not (run_dir / "soft_deleted.json").exists()


def test_path_traversal_protection_on_lifecycle_endpoints(isolated_env):
    """Verify path traversal attempts are rejected across all lifecycle endpoints."""
    project_root, client = isolated_env

    traversal_targets = [
        "../etc/passwd",
        "../../batch_queue.json",
        "..%2f..%2fconfig",
        "/etc/shadow",
    ]

    for target in traversal_targets:
        # Delete endpoint
        resp_del = client.post(f"/api/runs/{target}/delete")
        assert resp_del.status_code in (400, 404, 422)

        # Restore endpoint
        resp_res = client.post(f"/api/runs/{target}/restore")
        assert resp_res.status_code in (400, 404, 422)

        # Overview GET endpoint
        resp_ov = client.get(f"/api/overview/{target}")
        assert resp_ov.status_code in (400, 403, 404, 422)


# ===========================================================================
# 4. CONCURRENCY DEDUPLICATION & EDGE CASES
# ===========================================================================

def test_overview_generation_deduplicates_concurrent_calls(isolated_env):
    """Verify that concurrent POST generate requests for an already running overview job are safely deduplicated."""
    project_root, client = isolated_env
    run_dir = project_root / "run_upload_20261027_concurrent"
    papers_dir = run_dir / "papers"
    papers_dir.mkdir(parents=True)
    (papers_dir / "paper.pdf").write_bytes(b"%PDF-1.4\nTest Content")

    # Simulate in-flight overview generation job
    web_app._overview_jobs[run_dir.name] = {
        "status": "running",
        "stage": "Analyzing manuscript across 8 dimensions",
        "progress": 45,
        "started_at": "2026-10-27T10:00:00Z",
        "finished_at": None,
        "error": None,
        "overview": None,
    }

    resp = client.post(f"/api/overview/{run_dir.name}/generate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"
    assert data["stage"] == "Analyzing manuscript across 8 dimensions"
    assert "already running" in data["message"]
