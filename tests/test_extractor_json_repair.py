import json
from pathlib import Path

import pymupdf

from agents.agent_extractor import extract_criterion_evidence
from core.data_models import Paper, PaperMetadata


class FakeConfig:
    domain = "testing"

    def __init__(self):
        self.extraction_failures = {}

    def get_llm_config(self):
        return {
            "extractor_provider": "ollama",
            "extractor_model": "llama3.2:3b",
            "temperature": 0,
            "max_retries": 0,
        }

    def get_prompt(self, name):
        if name == "extractor_system":
            return (Path(__file__).resolve().parents[1] / "config/prompts/extractor_system.txt").read_text(encoding="utf-8")
        if name == "extractor_criterion":
            return (Path(__file__).resolve().parents[1] / "config/prompts/extractor_criterion.txt").read_text(encoding="utf-8")
        return None

    def get_system_config(self):
        return {"max_content_tokens": 10000}


def make_paper(tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    source_text = (
        "A direct quote from the paper. "
        "Additional source text ensures the PDF page block contains enough text for extraction. "
        "The evidence itself must still be copied exactly from the source."
    )
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), source_text)
        doc.save(pdf_path)
    paper = Paper(
        filename="paper.pdf",
        original_path=str(pdf_path),
        content_markdown=source_text,
        token_count=6,
        metadata=PaperMetadata(title="Test paper"),
    )
    criterion = {
        "id": "test_criterion",
        "name": "Test criterion",
        "description": "Test JSON repair.",
        "scale": {"type": "numeric", "range": [1, 5], "labels": {}},
    }
    return paper, criterion


def test_extractor_repairs_malformed_json_and_verifies_quotes(tmp_path, monkeypatch):
    paper, criterion = make_paper(tmp_path)
    config = FakeConfig()
    llm_call = {}
    malformed = (
        '{"score":4,'
        '"score_justification": This paper has a clear contribution.,'
        '"evidence":[{"quote":"A direct quote from the paper.",'
        '"page_reference":"N/A","relevance": This supports the score.}],'
        '"strengths":["Clear evidence"],"weaknesses":["Limited scope"],'
        '"confidence":0.8}'
    )

    def fake_call_llm(**kwargs):
        llm_call.update(kwargs)
        return {"success": True, "content": malformed, "cost": 0.0}

    monkeypatch.setattr("agents.agent_extractor.call_llm", fake_call_llm)

    result = extract_criterion_evidence(paper, criterion, config)
    assert result is not None
    assert result.score == 4
    assert len(result.evidence) == 1
    assert result.evidence[0].quote == "A direct quote from the paper."
    assert result.evidence[0].verification_status == "Verified"
    assert result.evidence[0].pdf_page_numbers == [1]
    assert result.evidence[0].page_reference == "PDF Page 1"
    assert result.confidence == 0.8
    prompt_source = llm_call["messages"][0]["content"][0]["text"]
    assert "<PDF_PAGE_1_START>" in prompt_source
    assert "copy each evidence quote verbatim" in prompt_source.lower()
    assert llm_call["response_format"]["type"] == "json_schema"
    assert "score" in llm_call["response_format"]["json_schema"]["schema"]["required"]


def test_target_ollama_paraphrase_stays_unverified_in_extraction(tmp_path, monkeypatch):
    pdf_path = tmp_path / "paper.pdf"
    source = "A representer theorem characterizes optimal solutions for a two-layer ReLU network."
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), source)
        doc.save(pdf_path)
    paper = Paper(
        filename=pdf_path.name,
        original_path=str(pdf_path),
        content_markdown=source,
        token_count=12,
        metadata=PaperMetadata(title="Paraphrase regression"),
    )
    criterion = {
        "id": "test_criterion",
        "name": "Test criterion",
        "description": "Check that evidence is copied exactly.",
        "scale": {"type": "numeric", "range": [1, 5], "labels": {}},
    }
    fabricated_quote = (
        "We show that the optimal solutions of the convex optimization problem for "
        "two-layer ReLU networks can be exactly characterized using the representer theorem."
    )
    response = json.dumps({
        "score": 4,
        "score_justification": "A theoretical result is presented.",
        "evidence": [{
            "quote": fabricated_quote,
            "page_reference": "PDF Page 1",
            "relevance": "It appears related to the criterion.",
        }],
        "strengths": [],
        "weaknesses": [],
        "confidence": 0.8,
    })
    monkeypatch.setattr(
        "agents.agent_extractor.call_llm",
        lambda **kwargs: {"success": True, "content": response, "cost": 0.0},
    )

    result = extract_criterion_evidence(paper, criterion, FakeConfig())
    assert result is not None
    evidence = result.evidence[0]
    assert evidence.verification_status == "Unverified"
    assert evidence.quote == fabricated_quote
    assert evidence.page_reference == "N/A"
    assert evidence.pdf_page_numbers == []
    assert evidence.verification_note == "No conservative exact-text match was found in the source PDF."


def test_extractor_rejects_missing_required_schema_fields(tmp_path, monkeypatch):
    paper, criterion = make_paper(tmp_path)
    config = FakeConfig()
    monkeypatch.setattr(
        "agents.agent_extractor.call_llm",
        lambda **kwargs: {"success": True, "content": '{"score":4}', "cost": 0.0},
    )

    result = extract_criterion_evidence(paper, criterion, config)
    assert result is None
    assert "score_justification" in config.extraction_failures["test_criterion"]


def test_extractor_rejects_invalid_field_types(tmp_path, monkeypatch):
    paper, criterion = make_paper(tmp_path)
    config = FakeConfig()
    invalid = json.dumps({
        "score": "4",
        "score_justification": "Valid explanation.",
        "evidence": [],
        "strengths": [],
        "weaknesses": [],
        "confidence": 0.8,
    })
    monkeypatch.setattr(
        "agents.agent_extractor.call_llm",
        lambda **kwargs: {"success": True, "content": invalid, "cost": 0.0},
    )

    result = extract_criterion_evidence(paper, criterion, config)
    assert result is None
    assert "score" in config.extraction_failures["test_criterion"]


def test_altered_quote_remains_unverified_and_unchanged(tmp_path, monkeypatch):
    paper, criterion = make_paper(tmp_path)
    config = FakeConfig()
    response = json.dumps({
        "score": 4,
        "score_justification": "The statement supports the assessment.",
        "evidence": [{
            "quote": "A direct quotation from the paper.",
            "page_reference": "PDF Page 1",
            "relevance": "Supports the assessment.",
        }],
        "strengths": [],
        "weaknesses": [],
        "confidence": 0.8,
    })
    monkeypatch.setattr(
        "agents.agent_extractor.call_llm",
        lambda **kwargs: {"success": True, "content": response, "cost": 0.0},
    )

    result = extract_criterion_evidence(paper, criterion, config)
    assert result is not None
    evidence = result.evidence[0]
    assert evidence.verification_status == "Unverified"
    assert evidence.quote == "A direct quotation from the paper."
    assert evidence.page_reference == "N/A"
