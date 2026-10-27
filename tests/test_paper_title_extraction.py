import json
from pathlib import Path
from fastapi.testclient import TestClient

from core.paper_ingestor import extract_metadata, extract_title_from_text
import web_app


def test_extract_title_skips_google_attribution_banner():
    """Verify that front-matter attribution notices are skipped in favor of the real title."""
    text = """Provided proper attribution is provided, Google hereby grants permission to
reproduce the tables and figures in this paper solely for use in journalistic or
scholarly works.

## **Attention Is All You Need**

**Ashish Vaswani**
Google Brain
"""
    meta = extract_metadata(text)
    assert meta.title == "Attention Is All You Need"


def test_extract_title_skips_journal_header_banner():
    """Verify journal submission and publication banners are skipped in favor of the real heading."""
    text = """Journal of Machine Learning Research 25 (2024) 1-42 Submitted 11/22; Revised 10/23; Published 4/24

# **Learning with Norm Constrained, Over-parameterized, Two-layer** **Neural Networks**

**Fanghui Liu**
Department of Computer Science
"""
    meta = extract_metadata(text)
    assert meta.title == "Learning with Norm Constrained, Over-parameterized, Two-layer Neural Networks"


def test_extract_title_ordinary_heading():
    """Verify standard markdown headings with no front-matter are extracted directly."""
    text = """# Deep Residual Learning for Image Recognition

Kaiming He, Xiangyu Zhang, Shaoqing Ren, Jian Sun

## Abstract
Deeper neural networks are more difficult to train.
"""
    meta = extract_metadata(text)
    assert meta.title == "Deep Residual Learning for Image Recognition"
    assert "Deeper neural networks" in meta.abstract


def test_extract_title_no_reliable_title_returns_no_title_found():
    """Verify that when only front-matter/disclaimers exist, No Title Found is returned rather than junk."""
    text = """Provided proper attribution is provided, Google hereby grants permission to
reproduce the tables and figures in this paper solely for use in journalistic or
scholarly works.
"""
    meta = extract_metadata(text)
    assert meta.title == "No Title Found"


def test_upload_extracts_and_persists_secondary_title_deterministically(tmp_path, monkeypatch):
    """Verify that upload persists original_filename as primary and extracts secondary title deterministically."""
    project_root = Path(tmp_path)
    config_dir = project_root / "config"
    prompts_dir = config_dir / "prompts"
    prompts_dir.mkdir(parents=True)
    (config_dir / "criteria.yaml").write_text("criteria: []\n", encoding="utf-8")
    for p in ("extractor_system.txt", "extractor_user.txt", "extractor_criterion.txt", "synthesizer_system.txt", "synthesizer_user.txt"):
        (prompts_dir / p).write_text(f"prompt {p}", encoding="utf-8")

    monkeypatch.setattr(web_app, "PROJECT_ROOT", str(project_root))
    client = TestClient(web_app.app)

    # Use the sample attend_all_of_yu.pdf fixture
    sample_pdf_path = Path(__file__).parent / "fixtures" / "attend_all_of_yu.pdf"
    if not sample_pdf_path.is_file():
        sample_pdf_path = Path("run_upload_20261027_022355_8f7decd7/papers/attend_all_of_yu.pdf")
    pdf_bytes = sample_pdf_path.read_bytes()

    res = client.post(
        "/api/upload",
        files={"file": ("attend_all_of_yu.pdf", pdf_bytes, "application/pdf")},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["original_filename"] == "attend_all_of_yu.pdf"
    assert data["paper_title"] == "Attention Is All You Need"

    run_dir = project_root / data["run_dir"]
    meta = json.loads((run_dir / "input" / "run_metadata.json").read_text(encoding="utf-8"))
    assert meta["original_filename"] == "attend_all_of_yu.pdf"
    assert meta["paper_title_extracted"] == "Attention Is All You Need"
