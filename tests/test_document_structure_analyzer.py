from agents.document_structure_analyzer import analyze_document_structure


def test_detects_figure_captions_and_numbering_gaps():
    text = """# Paper

Fig. 1. Methodology flowchart
Some explanation.
Figure 3: Model performance
"""
    result = analyze_document_structure(text)

    assert result["figure_count"] == 2
    assert [item["number"] for item in result["figures"]] == [1, 3]
    assert result["figure_numbering"]["numbering_gaps"] == [2]
    assert result["detection_status"]["figures"] == "detected"


def test_detects_numbered_tables_and_markdown_table_lines():
    text = """Table 1. Dataset summary

| Model | Accuracy |
| --- | --- |
| SVM | 0.91 |
"""
    result = analyze_document_structure(text)

    assert result["table_caption_count"] == 1
    assert result["tables"][0]["number"] == 1
    assert result["markdown_table_line_count"] == 3
    assert result["detection_status"]["tables"] == "detected"


def test_detects_common_equation_markers():
    text = r"""The loss is defined as:
$L = x^2$
\[
\sum_{i=1}^{n} x_i
\]
"""
    result = analyze_document_structure(text)

    assert result["equation_like_line_count"] >= 2
    assert result["detection_status"]["equations"] == "detected"


def test_detects_duplicate_caption_numbers():
    text = """Fig. 1. First figure
Figure 1. Duplicate number
"""
    result = analyze_document_structure(text)

    assert result["figure_numbering"]["duplicate_numbers"] == [1]


def test_empty_text_reports_not_detected_not_absent():
    result = analyze_document_structure("")

    assert result["figure_count"] == 0
    assert result["table_caption_count"] == 0
    assert result["equation_like_line_count"] == 0
    assert result["detection_status"]["figures"] == "not_detected_in_extracted_text"
    assert result["detection_status"]["tables"] == "not_detected_in_extracted_text"
    assert result["detection_status"]["equations"] == "not_detected_in_extracted_text"
