import pymupdf

from agents.agent_extractor import (
    _find_quote_pdf_pages,
    _find_source_substring,
    _normalize_quote,
    _page_labeled_pdf_text,
    _verify_evidence_quote,
    _verify_pdf_quote,
)
from core.data_models import Paper, PaperMetadata


def test_normalization_preserves_case_and_punctuation():
    assert _normalize_quote("Evidence-based, REVIEW!") == "Evidence-based, REVIEW!"


def test_normalization_collapses_whitespace_and_pdf_line_hyphenation():
    assert _normalize_quote("Evidence  based\nreview") == "Evidence based review"
    assert _normalize_quote("inter-\nnational model") == "international model"


def test_source_match_returns_the_exact_substring_with_original_line_hyphenation():
    source = "The inter-\nnational model is described here."
    assert _find_source_substring(source, "international model") == "inter-\nnational model"


def test_pdf_quote_match_uses_exact_text_and_one_based_page_numbers(tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    with pymupdf.open() as document:
        page1 = document.new_page()
        page1.insert_text((72, 72), "Evidence-based review supports the claim.")
        page2 = document.new_page()
        page2.insert_text((72, 72), "The sample statement appears on this page.")
        document.save(pdf_path)

    pages = _find_quote_pdf_pages(
        str(pdf_path), "Evidence-based review\n supports   the claim."
    )
    assert pages == [1]
    exact_quote, exact_pages = _verify_pdf_quote(
        str(pdf_path), "Evidence-based review supports the claim."
    )
    assert exact_quote == "Evidence-based review supports the claim."
    assert exact_pages == [1]
    assert _find_quote_pdf_pages(str(pdf_path), "The sample statement appears") == [2]


def test_pdf_quote_match_uses_visual_reading_order_with_one_based_page_number(tmp_path):
    pdf_path = tmp_path / "out-of-order-text.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        # Add the lower line first so PDF content order differs from visual order.
        page.insert_text((72, 160), "Second visual line follows.")
        page.insert_text((72, 72), "First visual line begins.")
        document.save(pdf_path)

    exact_quote, pages = _verify_pdf_quote(
        str(pdf_path), "First visual line begins. Second visual line follows."
    )
    assert exact_quote is not None
    assert pages == [1]


def test_pdf_prompt_labels_each_page_using_one_based_pdf_indices(tmp_path):
    pdf_path = tmp_path / "page-labeled.pdf"
    with pymupdf.open() as document:
        filler = " Additional page text supports stable source extraction and page mapping."
        document.new_page().insert_text((72, 72), "Text from first page." + filler * 3)
        document.new_page().insert_text((72, 72), "Text from second page." + filler * 3)
        document.save(pdf_path)

    paper = Paper(
        filename=pdf_path.name,
        original_path=str(pdf_path),
        content_markdown="Unlabeled fallback text.",
        token_count=4,
        metadata=PaperMetadata(title="Page label test"),
    )
    source = _page_labeled_pdf_text(paper)
    assert source is not None
    assert "<PDF_PAGE_1_START>" in source
    assert "Text from first page." in source
    assert "<PDF_PAGE_1_END>" in source
    assert "<PDF_PAGE_2_START>" in source
    assert "Text from second page." in source
    assert "<PDF_PAGE_2_END>" in source


def test_short_context_prompt_selects_relevant_exact_page_excerpts(tmp_path):
    pdf_path = tmp_path / "relevant-excerpts.pdf"
    with pymupdf.open() as document:
        unrelated = document.new_page()
        unrelated.insert_textbox(
            (72, 72, 520, 720),
            "Background discussion about unrelated application data. " * 30,
        )
        relevant = document.new_page()
        relevant.insert_textbox(
            (72, 72, 520, 720),
            "The theorem assumes bounded norms. Its proof establishes the mathematical result. "
            "The argument identifies the assumptions required for correctness.",
        )
        document.save(pdf_path)

    paper = Paper(
        filename=pdf_path.name,
        original_path=str(pdf_path),
        content_markdown="Unlabeled fallback text.",
        token_count=100,
        metadata=PaperMetadata(title="Relevant excerpt test"),
    )
    criterion = {
        "id": "theoretical_contribution",
        "name": "Theoretical Contribution and Mathematical Correctness",
        "description": "Assess theorems, proofs, assumptions, and correctness.",
        "sub_questions": ["Are the main results supported by valid proofs?"],
    }
    source = _page_labeled_pdf_text(
        paper,
        criterion=criterion,
        max_source_chars=900,
    )
    assert source is not None
    assert len(source) <= 900
    assert "<PDF_PAGE_2_START>" in source
    assert "mathematical result" in source
    assert "<PDF_PAGE_1_START>" not in source


def test_pdf_quote_matching_rejects_punctuation_case_and_word_changes(tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Evidence-based review supports the claim.")
        document.save(pdf_path)

    assert _find_quote_pdf_pages(
        str(pdf_path), "Evidence based review supports the claim."
    ) == []
    assert _find_quote_pdf_pages(
        str(pdf_path), "evidence-based review supports the claim."
    ) == []
    assert _find_quote_pdf_pages(
        str(pdf_path), "Evidence-focused review supports the claim."
    ) == []


def test_verifier_rejects_a_saved_ollama_paraphrase_of_similar_source_text(tmp_path):
    pdf_path = tmp_path / "paraphrased-claim.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text(
            (72, 72),
            "A representer theorem characterizes optimal solutions for a two-layer ReLU network.",
        )
        document.save(pdf_path)

    # This is a saved Ollama evidence string from the target paper's prior run.
    fabricated_quote = (
        "We show that the optimal solutions of the convex optimization problem for "
        "two-layer ReLU networks can be exactly characterized using the representer theorem."
    )
    exact_quote, pages = _verify_pdf_quote(str(pdf_path), fabricated_quote)
    assert exact_quote is None
    assert pages == []


def test_find_quote_pdf_pages_returns_empty_for_missing_or_blank_quote(tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "Text that does not match.")
        document.save(pdf_path)

    assert _find_quote_pdf_pages(str(pdf_path), "missing quote") == []
    assert _find_quote_pdf_pages(str(pdf_path), "  ") == []
    assert _find_quote_pdf_pages("/path/that/does/not/exist.pdf", "quote") == []


def test_verify_evidence_quote_docx_matching_and_unmatched(tmp_path):
    docx_path = tmp_path / "reserch.docx"
    docx_path.write_text("Dummy binary content placeholder")
    content = (
        "Smart Kitchen Analytics for Hostels: A Machine Learning Approach to Food-Waste Prediction.\n"
        "Decision Tree and Polynomial SVM achieved superior empirical convergence."
    )
    paper = Paper(
        id="docx-paper-001",
        filename="reserch.docx",
        original_path=str(docx_path),
        content_markdown=content,
        token_count=len(content.split()),
        metadata=PaperMetadata(title="Smart Kitchen Analytics"),
    )

    # 1. Successful exact-text match returns exact_quote, [], None
    valid_quote = "Decision Tree and Polynomial SVM achieved superior empirical convergence."
    exact_q, pages, note = _verify_evidence_quote(paper, valid_quote)
    assert exact_q == valid_quote
    assert pages == []
    assert note is None

    # 2. Unmatched quote returns quote, [], failure note
    unmatched = "Completely hallucinated findings on quantum computing."
    exact_q, pages, note = _verify_evidence_quote(paper, unmatched)
    assert exact_q == unmatched
    assert pages == []
    assert note == "No conservative exact-text match was found in extracted document text."
