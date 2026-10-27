"""
Phase 2 Comprehensive Regression Tests:
Safe Soft-Deletion, Isolation, and Trash Recovery.
"""

import json
import os
import shutil
from pathlib import Path
import pytest
from starlette.testclient import TestClient
import web_app


@pytest.fixture
def test_env(tmp_path, monkeypatch):
    """Fixture that creates an isolated project root with mock runs."""
    fixture_dir = Path(__file__).parent / "fixtures" / "run_validation_20261026_232939_dda2d977"
    run_a = tmp_path / "run_validation_20261026_232939_dda2d977"
    run_b = tmp_path / "run_upload_20261027_010000_12345678"

    if fixture_dir.is_dir():
        shutil.copytree(str(fixture_dir), str(run_a))
        shutil.copytree(str(fixture_dir), str(run_b))

    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(tmp_path))
    client = TestClient(web_app.app)
    return client, tmp_path, run_a.name, run_b.name


def test_soft_delete_and_recoverable(test_env):
    client, tmp_path, run_a, run_b = test_env

    # Verify both runs visible initially
    res_initial = client.get("/api/runs")
    assert res_initial.status_code == 200
    runs = [r["name"] for r in res_initial.json()["runs"]]
    assert run_a in runs
    assert run_b in runs

    # Soft-delete run_a
    del_res = client.post(f"/api/runs/{run_a}/delete")
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "soft_deleted"

    # Verify files on disk preserved (Invariant 7)
    run_a_dir = tmp_path / run_a
    assert (run_a_dir / "soft_deleted.json").is_file()
    assert (run_a_dir / "papers").is_dir()
    assert (run_a_dir / "outputs").is_dir()

    # Verify run_metadata.json marked is_deleted
    meta_path = run_a_dir / "input" / "run_metadata.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta.get("is_deleted") is True


def test_soft_deleted_excluded_from_archive_and_csv(test_env):
    client, tmp_path, run_a, run_b = test_env

    # Soft-delete run_a
    client.post(f"/api/runs/{run_a}/delete")

    # 1. Excluded from /api/runs (Requirement 3)
    res_runs = client.get("/api/runs")
    active_runs = [r["name"] for r in res_runs.json()["runs"]]
    assert run_a not in active_runs
    assert run_b in active_runs

    # 2. Excluded from consolidated CSV export (Requirement 3)
    res_csv = client.get("/api/reports/csv")
    assert res_csv.status_code == 200
    assert run_a not in res_csv.text
    assert run_b in res_csv.text

    # 3. Visible in Deleted Reviews list (Requirement 4)
    res_trash = client.get("/api/deleted-runs")
    assert res_trash.status_code == 200
    trash_runs = [r["name"] for r in res_trash.json()["deleted_runs"]]
    assert run_a in trash_runs
    assert run_b not in trash_runs


def test_restore_run_success(test_env):
    client, tmp_path, run_a, run_b = test_env

    # Soft-delete then restore run_a
    client.post(f"/api/runs/{run_a}/delete")
    restore_res = client.post(f"/api/runs/{run_a}/restore")
    assert restore_res.status_code == 200
    assert restore_res.json()["status"] == "restored"

    # Verify soft_deleted.json removed
    run_a_dir = tmp_path / run_a
    assert not (run_a_dir / "soft_deleted.json").exists()

    # Verify run_a reappears in /api/runs and /api/reports/csv
    res_runs = client.get("/api/runs")
    active_runs = [r["name"] for r in res_runs.json()["runs"]]
    assert run_a in active_runs

    res_csv = client.get("/api/reports/csv")
    assert run_a in res_csv.text

    # Verify run_a removed from /api/deleted-runs
    res_trash = client.get("/api/deleted-runs")
    trash_runs = [r["name"] for r in res_trash.json()["deleted_runs"]]
    assert run_a not in trash_runs


def test_path_traversal_rejection(test_env):
    client, tmp_path, run_a, _ = test_env

    # Traversal attempts must fail (Requirement 6)
    traversal_paths = [
        "../../etc",
        "..%2f..%2fetc",
        "../something",
        "/etc/passwd",
        "run_validation_20261026_232939_dda2d977/../..",
    ]
    for p in traversal_paths:
        res_del = client.post(f"/api/runs/{p}/delete")
        assert res_del.status_code in (400, 403, 404)
        res_rst = client.post(f"/api/runs/{p}/restore")
        assert res_rst.status_code in (400, 403, 404)


def test_unknown_run_404(test_env):
    client, tmp_path, _, _ = test_env

    res_del = client.post("/api/runs/non_existent_run_dir_99999/delete")
    assert res_del.status_code == 404

    res_rst = client.post("/api/runs/non_existent_run_dir_99999/restore")
    assert res_rst.status_code == 404


def test_repeated_delete_and_restore(test_env):
    client, tmp_path, run_a, _ = test_env

    # 1. Restore non-deleted run fails
    res1 = client.post(f"/api/runs/{run_a}/restore")
    assert res1.status_code == 400
    assert "not soft-deleted" in res1.json()["detail"]

    # 2. First delete succeeds
    res2 = client.post(f"/api/runs/{run_a}/delete")
    assert res2.status_code == 200

    # 3. Repeated delete fails
    res3 = client.post(f"/api/runs/{run_a}/delete")
    assert res3.status_code == 400
    assert "already soft-deleted" in res3.json()["detail"]


def test_active_run_deletion_blocked(test_env, monkeypatch):
    client, tmp_path, run_a, _ = test_env

    # Simulate active run in progress (Requirement 9)
    monkeypatch.setattr(web_app, "_active_runs", {run_a: {"stage": "Extraction", "paper": "sample.pdf"}})

    res = client.post(f"/api/runs/{run_a}/delete")
    assert res.status_code == 409
    assert "currently in progress" in res.json()["detail"]

    # Verify run was not marked deleted
    assert not (tmp_path / run_a / "soft_deleted.json").exists()


def test_persistence_across_restart(test_env, monkeypatch):
    client, tmp_path, run_a, _ = test_env

    # Soft-delete run_a
    client.post(f"/api/runs/{run_a}/delete")

    # Simulate fresh discovery/restart without relying on in-memory state
    runs_fresh = web_app._find_run_dirs(include_deleted=False)
    active_names = [r["name"] for r in runs_fresh]
    assert run_a not in active_names

    deleted_fresh = web_app._find_deleted_run_dirs()
    deleted_names = [r["name"] for r in deleted_fresh]
    assert run_a in deleted_names


def test_direct_access_to_deleted_run_blocked(test_env):
    client, tmp_path, run_a, _ = test_env

    # Soft delete run_a
    client.post(f"/api/runs/{run_a}/delete")

    # Direct resolution should fail with 404 explaining soft-deleted status
    with pytest.raises(Exception) as excinfo:
        web_app._resolve_run_path(run_a)
    assert "soft-deleted" in str(excinfo.value)
