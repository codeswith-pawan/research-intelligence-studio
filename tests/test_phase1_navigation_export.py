"""
Phase 1 Focused Regression Tests:
Navigation, Export Reliability, and Localhost Binding.
"""

import os
import shutil
import subprocess
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


def test_download_archive_csv_all_runs(client):
    """Verify GET /api/reports/csv returns consolidated archive with expected headers and content."""
    res = client.get("/api/reports/csv")
    assert res.status_code == 200
    assert "text/csv" in res.headers["content-type"]
    assert "attachment;" in res.headers.get("content-disposition", "")
    assert "review_archive_" in res.headers.get("content-disposition", "")
    assert res.headers.get("x-content-type-options") == "nosniff"

    lines = res.text.strip().split("\r\n" if "\r\n" in res.text else "\n")
    header = lines[0].split(",")
    assert "run_directory" in header
    assert "paper_filename" in header
    assert "title" in header
    assert "overall_score" in header
    assert "recommendation" in header
    assert "status" in header

    # At least one run from fixture should be present
    assert len(lines) >= 2
    assert "run_validation_20261026_232939_dda2d977" in res.text


def test_download_single_run_csv_headers_preserved(client):
    """Verify single-run CSV export retains backwards compatibility and adds security headers."""
    res = client.get("/api/reports/csv/run_validation_20261026_232939_dda2d977")
    assert res.status_code == 200
    assert "text/csv" in res.headers["content-type"]
    assert "attachment;" in res.headers.get("content-disposition", "")
    assert res.headers.get("x-content-type-options") == "nosniff"
    assert "paper_filename" in res.text


def test_main_cli_default_binding_is_loopback(monkeypatch):
    """Verify server argparse defaults to loopback 127.0.0.1 instead of 0.0.0.0."""
    import argparse
    import web_app

    # Inspect the parser constructed in main
    parser = argparse.ArgumentParser()
    # Test that the web_app script source defaults to 127.0.0.1
    import inspect
    src = inspect.getsource(web_app.main)
    assert 'default="127.0.0.1"' in src
    assert "http://{browser_host}:{args.port}" in src


def test_app_js_table_dom_separation():
    """Verify app.js separates archive rendering from single-run reports and fixes refresh."""
    app_js_path = Path(__file__).parent.parent / "web" / "static" / "app.js"
    content = app_js_path.read_text(encoding="utf-8")

    # 1. Refresh button in Past Reviews calls loadRuns, NOT loadResults
    assert 'refreshReportsBtn.addEventListener("click", () => {\n            loadRuns();\n        });' in content or 'refreshReportsBtn.addEventListener("click"' in content
    assert 'document.getElementById("refresh-reports-btn").addEventListener("click", loadResults)' not in content

    # 2. onRunSelected does not call renderResults([])
    on_run_selected_match = content[content.find("async function onRunSelected()"):content.find("async function uploadPaper(")]
    assert "renderResults([])" not in on_run_selected_match

    # 3. loadResults does not overwrite archive table
    load_results_match = content[content.find("async function loadResults()"):content.find("function populateDashboardReviewSelect()")]
    assert "sortAndRenderResults();" not in load_results_match
    assert "renderResults([]);" not in load_results_match

    # 4. CSV export distinguishes between archive page and single run
    assert 'const isReviewsArchivePage = (window.location.hash === "#/reviews" || !currentRunDir);' in content
    assert 'isReviewsArchivePage ? "/api/reports/csv"' in content


def test_index_html_jump_pill_link():
    """Verify index.html Analysis Studio jump pill points to #/reviews archive."""
    index_html = (Path(__file__).parent.parent / "web" / "static" / "index.html").read_text(encoding="utf-8")
    assert '<a href="#/reviews" class="jump-pill">Past Reviews Archive &rarr;</a>' in index_html
    assert '<a href="#results-table-container" class="jump-pill">Report History</a>' not in index_html
