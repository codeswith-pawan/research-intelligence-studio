import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

import web_app
from core.paper_ingestor import extract_metadata, sanitize_extracted_title, INVALID_TITLE_SET
from core.paper_overview import load_paper_overview_cache

client = TestClient(web_app.app)


def test_sanitize_extracted_title_rejects_keywords_and_headers():
    """Verify sanitize_extracted_title rejects keywords, abstracts, and invalid strings."""
    assert sanitize_extracted_title("Keywords") is None
    assert sanitize_extracted_title("KEYWORDS: Deep Learning, NLP") is None
    assert sanitize_extracted_title("**Keywords**") is None
    assert sanitize_extracted_title("Abstract") is None
    assert sanitize_extracted_title("untitled") is None
    assert sanitize_extracted_title("Author One | author@uni.edu") is None
    assert sanitize_extracted_title("No title found") is None
    assert sanitize_extracted_title("Valid Manuscript Title: An Empirical Study") == "Valid Manuscript Title: An Empirical Study"


def test_extract_metadata_skips_keyword_headings():
    """Verify that extract_metadata does not pick '# **Keywords**' as the manuscript title."""
    markdown_content = """# **Keywords**
deep learning, transformers, review systems

# Attention Is All You Need
Ashish Vaswani, Noam Shazeer
Google Brain

## Abstract
The dominant sequence transduction models are based on complex recurrent or convolutional neural networks...
"""
    meta = extract_metadata(markdown_content)
    # The title should be the actual paper title, NOT 'Keywords'
    assert meta.title == "Attention Is All You Need"
    assert "keywords" not in meta.title.lower()


def test_cache_healing_repairs_stale_keywords_title(tmp_path):
    """Verify load_paper_overview_cache automatically repairs cached data that had title 'Keywords'."""
    run_dir = tmp_path / "test_run_healing"
    run_dir.mkdir(parents=True)
    outputs_dir = run_dir / "outputs"
    outputs_dir.mkdir(parents=True)

    # Ingestion cache has real metadata
    ingestion_cache = {
        "metadata": {
            "title": "Agentic Paper Review System: An Evidence-Grounded Multi-Agent Framework",
            "authors": ["Test Author"],
            "abstract": "This paper presents...",
            "token_count": 1000
        }
    }
    with open(outputs_dir / "ingestion_cache.json", "w") as f:
        json.dump(ingestion_cache, f)

    # Overview cache was erroneously created with title: "Keywords"
    corrupt_overview = {
        "schema_version": "1.0",
        "run_dir": "test_run_healing",
        "paper_filename": "paper.pdf",
        "file_hash_sha256": "abc123hash",
        "generated_at": "2026-11-14T00:00:00Z",
        "generator_model": "ollama/llama3.2:3b",
        "metadata": {
            "title": "Keywords",
            "authors": [],
            "abstract": "This paper presents..."
        },
        "sections": {
            "research_problem": {
                "title": "Research Problem",
                "status": "verified_content",
                "content": "Problem description...",
                "source_references": [],
                "traceable_references": []
            }
        }
    }
    with open(outputs_dir / "paper_overview.json", "w") as f:
        json.dump(corrupt_overview, f)

    # Healing should trigger on load
    overview, status = load_paper_overview_cache(str(run_dir))
    assert overview is not None
    assert status == "valid"
    assert overview.metadata["title"] == "Agentic Paper Review System: An Evidence-Grounded Multi-Agent Framework"

    # Disk file should also be repaired
    with open(outputs_dir / "paper_overview.json", "r") as f:
        disk_data = json.load(f)
    assert disk_data["metadata"]["title"] == "Agentic Paper Review System: An Evidence-Grounded Multi-Agent Framework"


def test_api_overview_returns_authoritative_review_and_clean_title():
    """Verify GET /api/overview/{run_dir} returns authoritatively matched review metrics and title."""
    run_id = "run_upload_20261111_215936_2acfdab0"
    res = client.get(f"/api/overview/{run_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "cached"
    overview = data["overview"]

    # Title verification
    title = overview["metadata"]["title"]
    assert title.lower() not in INVALID_TITLE_SET
    assert not title.lower().startswith("keyword")
    assert "Agentic Paper Review System" in title

    # Authoritative review verification
    auth_rev = data.get("authoritative_review")
    assert auth_rev is not None
    assert auth_rev["run_id"] == run_id
    assert auth_rev["diagnostic_score"] == 66.6
    assert auth_rev["verified_quotations"] == 11
    assert auth_rev["unverified_quotations"] == 7
    assert auth_rev["total_quotations"] == 18
    assert auth_rev["review_status"] == "Needs Manual Review"


def test_run_isolation_nonexistent_run_returns_404_or_not_generated():
    """Verify that a nonexistent or isolated run does not leak or fall back to other run caches."""
    fake_run = "run_upload_99999999_000000_fakeid00"
    res = client.get(f"/api/overview/{fake_run}")
    # Either 404 or a clean status 'not_generated' with no leaked data
    if res.status_code == 200:
        data = res.json()
        assert data.get("status") in ("not_generated", "error")
        assert "overview" not in data or data["overview"] is None
    else:
        assert res.status_code == 404


def test_run_isolation_distinct_runs_preserve_identities():
    """Verify two historical runs maintain independent files, titles, scores, and evidence."""
    run_a = "run_upload_20261111_215936_2acfdab0"
    run_b = "run_upload_20261028_005048_2b50a89b"

    res_a = client.get(f"/api/overview/{run_a}")
    res_b = client.get(f"/api/overview/{run_b}")

    assert res_a.status_code == 200
    assert res_b.status_code == 200

    data_a = res_a.json()
    data_b = res_b.json()

    # Different run dirs
    assert data_a["run_dir"] == run_a
    assert data_b["run_dir"] == run_b

    auth_a = data_a["authoritative_review"]
    auth_b = data_b["authoritative_review"]

    # Different manuscripts and hashes
    assert auth_a["manuscript_filename"] != auth_b["manuscript_filename"]
    assert auth_a["file_hash_sha256"] != auth_b["file_hash_sha256"]
    assert auth_a["manuscript_filename"] == "Agentic_Paper_Review_System_Research_Paper_Formatted.docx"
    assert auth_b["manuscript_filename"] == "1_reserch.pdf"

    # Distinct scores and quotation counts (no leakage)
    assert auth_a["diagnostic_score"] == 66.6
    assert auth_a["total_quotations"] == 18
    assert auth_a["verified_quotations"] == 11

    assert auth_b["diagnostic_score"] == 65.6
    assert auth_b["total_quotations"] == 8
    assert auth_b["verified_quotations"] == 2


def test_provenance_labels_in_sections():
    """Verify all sections in Paper Overview have compliant provenance status tags."""
    run_id = "run_upload_20261111_215936_2acfdab0"
    res = client.get(f"/api/overview/{run_id}")
    assert res.status_code == 200
    sections = res.json()["overview"]["sections"]

    allowed_statuses = {
        "verified_content",
        "not_identified_in_text",
    }

    for key, sec in sections.items():
        status = sec.get("status")
        assert status in allowed_statuses, f"Section {key} had invalid status {status}"
