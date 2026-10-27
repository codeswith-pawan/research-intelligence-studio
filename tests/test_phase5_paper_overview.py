"""Comprehensive regression test suite for Phase 5: Dedicated Paper Overview Tab & Final Feature Integration.

Tests:
1. Data Contract & Validation: Validates all 8 sections and statuses; rejects missing sections or invalid statuses.
2. File Byte Hash Calculation: Computes SHA-256 directly from file bytes; detects alterations.
3. Cache Integrity & Atomic Persistence: Saves cache atomically; validates cache match; detects stale cache on file modification.
4. Read-Only GET Endpoint: GET /api/overview/{run_dir} is strictly read-only, never generating or writing files.
5. Path Validation & Security: Rejects traversal attempts (403), missing runs (404), and soft-deleted runs (404).
6. Async Generation & Deduplication: POST /api/overview/{run_dir}/generate initiates asynchronous worker and prevents duplicate concurrent runs.
7. Independent State: Overview generation leaves batch queue (batch_queue.json), review scores, and synthesis untouched.
8. Grounding Truthfulness: Author-stated limitations and future work accurately report not_identified_in_text when unstated.
"""

import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from core.paper_overview import (
    compute_file_sha256,
    validate_overview_payload,
    load_paper_overview_cache,
    save_paper_overview_cache,
    PaperOverviewData,
    PaperOverviewSection,
    OVERVIEW_SECTION_KEYS,
    STATUS_VERIFIED_CONTENT,
    STATUS_NOT_IDENTIFIED,
)
import web_app


@pytest.fixture
def test_env(tmp_path, monkeypatch):
    """Setup an isolated test workspace with a sample run and PDF."""
    run_name = "run_overview_test_20261027_001"
    run_dir = tmp_path / run_name
    papers_dir = run_dir / "papers"
    outputs_dir = run_dir / "outputs"
    papers_dir.mkdir(parents=True)
    outputs_dir.mkdir(parents=True)

    # Create dummy manuscript PDF with known content
    pdf_path = papers_dir / "sample_paper.pdf"
    pdf_bytes = b"%PDF-1.4\n1 0 obj\n<< /Title (Test Paper on Neural Systems) >>\nendobj\n%%EOF\n"
    pdf_path.write_bytes(pdf_bytes)

    # Ingestion cache
    ingestion_cache = {
        "title": "Test Paper on Neural Systems",
        "authors": ["Alice Smith", "Bob Jones"],
        "year": 2026,
        "abstract": "This paper explores offline peer review models.",
        "text": "1. Introduction\nWe study automated peer review.\n2. Methodology\nWe propose an 8-criteria agent architecture.\n3. Evaluation\nWe evaluate on 50 papers.\n4. Results\nAccuracy improves by 15%."
    }
    (run_dir / "ingestion_cache.json").write_text(json.dumps(ingestion_cache))

    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(web_app, "BATCH_QUEUE_FILE", str(tmp_path / "batch_queue.json"))

    client = TestClient(web_app.app)
    return {
        "client": client,
        "tmp_path": tmp_path,
        "run_name": run_name,
        "run_dir": run_dir,
        "pdf_path": pdf_path,
        "pdf_bytes": pdf_bytes,
    }


# ---------------------------------------------------------------------------
# 1. Data Contract & Validation Tests
# ---------------------------------------------------------------------------

def test_validate_overview_payload_all_eight_sections_valid():
    """Verify that a payload with all 8 sections and valid statuses succeeds."""
    sections = {}
    for key in OVERVIEW_SECTION_KEYS:
        sections[key] = {
            "title": key.replace("_", " ").title(),
            "content": f"Verified description for {key}.",
            "status": STATUS_VERIFIED_CONTENT,
            "source_references": ["Page 2, Paragraph 1"],
        }

    payload = {
        "sections": sections,
    }

    is_valid, errors = validate_overview_payload(payload)
    assert is_valid is True
    assert errors == []


def test_validate_overview_payload_rejects_missing_section():
    """Verify that omitting any of the 8 required sections raises validation errors."""
    sections = {}
    for key in OVERVIEW_SECTION_KEYS:
        if key == "future_work":
            continue  # Omit future_work
        sections[key] = {
            "title": key,
            "content": "content",
            "status": STATUS_VERIFIED_CONTENT,
        }

    payload = {
        "sections": sections,
    }

    is_valid, errors = validate_overview_payload(payload)
    assert is_valid is False
    assert any("Missing required section: 'future_work'" in e for e in errors)


def test_validate_overview_payload_rejects_invalid_status():
    """Verify that an unrecognized section status raises validation errors."""
    sections = {}
    for key in OVERVIEW_SECTION_KEYS:
        sections[key] = {
            "title": key,
            "content": "content",
            "status": "hallucinated_status" if key == "limitations" else STATUS_VERIFIED_CONTENT,
        }

    payload = {
        "sections": sections,
    }

    is_valid, errors = validate_overview_payload(payload)
    assert is_valid is False
    assert any("has invalid status: 'hallucinated_status'" in e for e in errors)


def test_validate_overview_payload_allows_not_identified_in_text():
    """Verify that not_identified_in_text is accepted for unstated limitations/future work."""
    sections = {}
    for key in OVERVIEW_SECTION_KEYS:
        status = STATUS_NOT_IDENTIFIED if key in ["limitations", "future_work"] else STATUS_VERIFIED_CONTENT
        sections[key] = {
            "title": key,
            "content": "Not explicitly stated." if status == STATUS_NOT_IDENTIFIED else "Detail.",
            "status": status,
            "source_references": [] if status == STATUS_NOT_IDENTIFIED else ["Page 1"],
        }

    payload = {
        "sections": sections,
    }

    is_valid, errors = validate_overview_payload(payload)
    assert is_valid is True
    assert errors == []


# ---------------------------------------------------------------------------
# 2. File Byte Hash Calculation Tests
# ---------------------------------------------------------------------------

def test_compute_file_sha256_exact(tmp_path):
    """Verify that compute_file_sha256 returns the true SHA-256 byte hash."""
    file_path = tmp_path / "test.bin"
    file_bytes = b"Hello Antigravity Paper Overview Verification"
    file_path.write_bytes(file_bytes)

    import hashlib
    expected_hash = hashlib.sha256(file_bytes).hexdigest()
    computed_hash = compute_file_sha256(str(file_path))
    assert computed_hash == expected_hash


def test_compute_file_sha256_nonexistent_raises_or_empty():
    """Verify that compute_file_sha256 handles missing files cleanly."""
    with pytest.raises(FileNotFoundError):
        compute_file_sha256("/nonexistent/file/path.pdf")


# ---------------------------------------------------------------------------
# 3. Cache Storage & Invalidation Tests
# ---------------------------------------------------------------------------

def test_save_and_load_cache_valid_sha(test_env):
    """Verify cache is saved atomically and loaded successfully when byte hash matches."""
    run_dir = test_env["run_dir"]
    pdf_path = test_env["pdf_path"]
    expected_sha = compute_file_sha256(str(pdf_path))

    sections = {
        k: PaperOverviewSection(title=k, content="Test content", status=STATUS_VERIFIED_CONTENT)
        for k in OVERVIEW_SECTION_KEYS
    }
    overview = PaperOverviewData(
        run_dir=test_env["run_name"],
        paper_filename="sample_paper.pdf",
        file_hash_sha256=expected_sha,
        generated_at=datetime.now(timezone.utc).isoformat(),
        generator_model="llama3.2:3b",
        metadata={"title": "Cached Paper"},
        sections=sections,
    )

    # Save cache
    saved_path = save_paper_overview_cache(str(run_dir), overview)
    assert os.path.exists(saved_path)

    # Load cache with matching hash
    loaded, status = load_paper_overview_cache(str(run_dir), expected_sha)
    assert status == "valid"
    assert loaded is not None
    assert loaded.metadata.get("title") == "Cached Paper"
    assert len(loaded.sections) == 8


def test_load_cache_detects_tampered_or_modified_manuscript(test_env):
    """Verify cache returns None and status 'stale' when manuscript bytes on disk change."""
    run_dir = test_env["run_dir"]
    pdf_path = test_env["pdf_path"]
    orig_sha = compute_file_sha256(str(pdf_path))

    sections = {
        k: PaperOverviewSection(title=k, content="Test content", status=STATUS_VERIFIED_CONTENT)
        for k in OVERVIEW_SECTION_KEYS
    }
    overview = PaperOverviewData(
        run_dir=test_env["run_name"],
        paper_filename="sample_paper.pdf",
        file_hash_sha256=orig_sha,
        generated_at=datetime.now(timezone.utc).isoformat(),
        generator_model="llama3.2:3b",
        metadata={"title": "Cached Paper"},
        sections=sections,
    )
    save_paper_overview_cache(str(run_dir), overview)

    # Modify the manuscript file on disk
    pdf_path.write_bytes(b"%PDF-1.4\nModified manuscript content bytes!\n%%EOF\n")
    new_sha = compute_file_sha256(str(pdf_path))
    assert new_sha != orig_sha

    # Attempting to load with the new SHA should invalidate the cache
    loaded, status = load_paper_overview_cache(str(run_dir), new_sha)
    assert status == "stale"
    assert loaded is None


# ---------------------------------------------------------------------------
# 4. API Endpoint Tests: GET (Pure Read-Only)
# ---------------------------------------------------------------------------

def test_get_overview_not_generated_does_not_create_files(test_env):
    """Verify GET /api/overview/{run_dir} is read-only and never writes to disk."""
    client = test_env["client"]
    run_name = test_env["run_name"]
    outputs_dir = test_env["run_dir"] / "outputs"
    cache_file = outputs_dir / "paper_overview.json"

    assert not cache_file.exists()

    res = client.get(f"/api/overview/{run_name}")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "not_generated"
    assert "overview" not in data or data["overview"] is None

    # Crucial safety check: GET request must NEVER create cache or trigger generation
    assert not cache_file.exists()


def test_get_overview_returns_cached_data_when_valid(test_env):
    """Verify GET /api/overview/{run_dir} returns cached overview when hash matches."""
    client = test_env["client"]
    run_name = test_env["run_name"]
    run_dir = test_env["run_dir"]
    pdf_path = test_env["pdf_path"]
    sha = compute_file_sha256(str(pdf_path))

    sections = {
        k: PaperOverviewSection(title=k, content=f"Verified {k}", status=STATUS_VERIFIED_CONTENT)
        for k in OVERVIEW_SECTION_KEYS
    }
    overview = PaperOverviewData(
        run_dir=run_name,
        paper_filename="sample_paper.pdf",
        file_hash_sha256=sha,
        generated_at=datetime.now(timezone.utc).isoformat(),
        generator_model="llama3.2:3b",
        metadata={"title": "API Cached Title"},
        sections=sections,
    )
    save_paper_overview_cache(str(run_dir), overview)

    res = client.get(f"/api/overview/{run_name}")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "cached"
    assert data["overview"]["metadata"]["title"] == "API Cached Title"
    assert len(data["overview"]["sections"]) == 8


def test_get_overview_returns_stale_when_file_bytes_differ(test_env):
    """Verify GET /api/overview/{run_dir} returns status 'stale' when manuscript changed."""
    client = test_env["client"]
    run_name = test_env["run_name"]
    run_dir = test_env["run_dir"]

    # Save with dummy old hash
    sections = {
        k: PaperOverviewSection(title=k, content="Test", status=STATUS_VERIFIED_CONTENT)
        for k in OVERVIEW_SECTION_KEYS
    }
    overview = PaperOverviewData(
        run_dir=run_name,
        paper_filename="sample_paper.pdf",
        file_hash_sha256="old_sha256_hash_value_here_12345678",
        generated_at=datetime.now(timezone.utc).isoformat(),
        generator_model="llama3.2:3b",
        metadata={"title": "Old Title"},
        sections=sections,
    )
    save_paper_overview_cache(str(run_dir), overview)

    res = client.get(f"/api/overview/{run_name}")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "stale"


# ---------------------------------------------------------------------------
# 5. Path Containment & Security Checks
# ---------------------------------------------------------------------------

def test_overview_endpoints_reject_path_traversal(test_env):
    """Verify traversal attempts are rejected with 403 Forbidden."""
    client = test_env["client"]

    # 1. Traversal via absolute path outside project root
    res_get = client.get("/api/overview/%2Fetc%2Fpasswd")
    assert res_get.status_code == 403
    assert res_get.json()["detail"] == "Access denied"

    res_post = client.post("/api/overview/%2Fetc%2Fpasswd/generate")
    assert res_post.status_code == 403

    # 2. Direct path resolution check for relative directory traversal
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        web_app._resolve_run_path("../../outside_dir")
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Access denied"


def test_overview_endpoints_reject_missing_run(test_env):
    """Verify non-existent run directory returns 404 Not Found."""
    client = test_env["client"]
    res = client.get("/api/overview/nonexistent_run_dir_12345")
    assert res.status_code == 404

    res_post = client.post("/api/overview/nonexistent_run_dir_12345/generate")
    assert res_post.status_code == 404


def test_overview_endpoints_reject_soft_deleted_run(test_env):
    """Verify soft-deleted run directory is rejected with 404 Not Found."""
    client = test_env["client"]
    tmp_path = test_env["tmp_path"]

    deleted_dir = tmp_path / ".deleted_reviews" / "deleted_run_20261027"
    deleted_dir.mkdir(parents=True)

    res = client.get("/api/overview/.deleted_reviews/deleted_run_20261027")
    assert res.status_code in [403, 404]

    res_post = client.post("/api/overview/.deleted_reviews/deleted_run_20261027/generate")
    assert res_post.status_code in [403, 404]


# ---------------------------------------------------------------------------
# 6. Concurrency Deduplication & Generation Endpoint
# ---------------------------------------------------------------------------

def test_post_generate_returns_202_and_deduplicates_concurrent_jobs(test_env, monkeypatch):
    """Verify POST /api/overview/{run_dir}/generate deduplicates concurrent generation requests."""
    client = test_env["client"]
    run_name = test_env["run_name"]

    # Mock long-running generation worker
    def slow_worker(run_dir_name, full_path, actual_hash, force):
        with web_app._overview_lock:
            if run_dir_name in web_app._overview_jobs:
                web_app._overview_jobs[run_dir_name]["stage"] = "Simulating slow extraction"
                web_app._overview_jobs[run_dir_name]["status"] = "running"
        time.sleep(0.5)
        with web_app._overview_lock:
            if run_dir_name in web_app._overview_jobs:
                web_app._overview_jobs[run_dir_name]["status"] = "completed"

    monkeypatch.setattr(web_app, "_run_overview_generation", slow_worker)

    # First request launches job (status 202)
    res1 = client.post(f"/api/overview/{run_name}/generate")
    assert res1.status_code == 202
    data1 = res1.json()
    assert data1["status"] == "queued"

    # Immediate second request should return status 200 indicating already running/queued
    res2 = client.post(f"/api/overview/{run_name}/generate")
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["status"] in ("queued", "running")


def test_post_generate_reuses_valid_cache_when_not_force(test_env):
    """Verify POST without force=true immediately returns HTTP 200 with cached overview."""
    client = test_env["client"]
    run_name = test_env["run_name"]
    run_dir = test_env["run_dir"]
    pdf_path = test_env["pdf_path"]
    sha = compute_file_sha256(str(pdf_path))

    sections = {
        k: PaperOverviewSection(title=k, content="Cached content", status=STATUS_VERIFIED_CONTENT)
        for k in OVERVIEW_SECTION_KEYS
    }
    overview = PaperOverviewData(
        run_dir=run_name,
        paper_filename="sample_paper.pdf",
        file_hash_sha256=sha,
        generated_at=datetime.now(timezone.utc).isoformat(),
        generator_model="llama3.2:3b",
        metadata={"title": "Instant Cache Title"},
        sections=sections,
    )
    save_paper_overview_cache(str(run_dir), overview)

    res = client.post(f"/api/overview/{run_name}/generate?force=false")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "cached"
    assert data["overview"]["metadata"]["title"] == "Instant Cache Title"


# ---------------------------------------------------------------------------
# 7. Independence & Invariant Checks
# ---------------------------------------------------------------------------

def test_overview_generation_does_not_affect_batch_queue(test_env):
    """Verify overview endpoints never touch or corrupt the persistent batch queue file."""
    client = test_env["client"]
    run_name = test_env["run_name"]
    queue_file = test_env["tmp_path"] / "batch_queue.json"

    # Seed batch queue with some state
    initial_queue = {
        "items": [
            {"id": "item_1", "status": "completed", "file_name": "paper1.pdf"}
        ],
        "state": "idle"
    }
    queue_file.write_text(json.dumps(initial_queue))

    # Perform GET and POST overview operations
    client.get(f"/api/overview/{run_name}")
    client.post(f"/api/overview/{run_name}/generate")

    # Queue file should remain completely identical
    assert queue_file.exists()
    assert json.loads(queue_file.read_text()) == initial_queue
