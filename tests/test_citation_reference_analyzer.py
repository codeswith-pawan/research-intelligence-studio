from agents.citation_reference_analyzer import analyze_citations_and_references


def test_matches_numbered_citations_to_multiline_references():
    text = """# Sample Paper

The approach follows prior work [1].
Another method is described in [2, 3].
A later result cites [1-2].

## References
1. A. Author, "A research study," Example Journal, 2022.
2. B. Author, "Another research study,"
   Example Conference, 2023.
3. C. Author, "Third research study," Example Journal, 2021.
"""
    result = analyze_citations_and_references(text)

    assert result["in_text_citation_numbers"] == [1, 2, 3]
    assert result["citations_without_reference"] == []
    assert result["references_not_cited"] == []
    assert len(result["references"]) == 3
    assert "Another research study" in result["references"][1]["text"]


def test_reports_citations_without_matching_reference():
    text = """The claim cites [2] and [7].

References
2. A. Author, "Existing source," Journal, 2022.
"""
    result = analyze_citations_and_references(text)

    assert result["citations_without_reference"] == [7]
    assert result["references_not_cited"] == []


def test_reports_references_that_are_not_cited():
    text = """The paper cites [1].

References
1. A. Author, "Used source," Journal, 2022.
2. B. Author, "Unused source," Journal, 2023.
"""
    result = analyze_citations_and_references(text)

    assert result["references_not_cited"] == [2]


def test_flags_potentially_incomplete_reference_without_claiming_error():
    text = """The paper cites [1] and [2].

References
1. A. Author, "Complete source," Journal, 2022.
2. European Commission, "Food Waste Prevention Guidelines,"
"""
    result = analyze_citations_and_references(text)

    assert result["potentially_incomplete_references"] == [2]
    assert any("human review" in item for item in result["limitations"])


def test_handles_empty_text_and_missing_reference_heading():
    result = analyze_citations_and_references("")

    assert result["in_text_citation_numbers"] == []
    assert result["references"] == []
    assert result["reference_heading_found"] is False
    assert result["citations_without_reference"] == []


def test_detects_duplicate_reference_numbers():
    text = """References
1. A. Author, "First source," Journal, 2022.
1. B. Author, "Duplicate source," Journal, 2023.
"""
    result = analyze_citations_and_references(text)

    assert result["duplicate_reference_numbers"] == [1]


def test_matches_bracketed_numbered_references_and_ignores_zero_subscript():
    text = """# Paper Title

We compare with prior work [1] and extended baselines [2, 3].
Formula: rate = d_model [0] * step [−] 1.

## References
[1] J. Ba, J. Kiros, and G. Hinton. Layer normalization. arXiv:1607.06450, 2016.
[2] D. Bahdanau, K. Cho, and Y. Bengio. Neural machine translation. CoRR, 2014.
[3] D. Britz, A. Goldie, M. Luong, and Q. Le. Exploration of NMT. CoRR, 2017.
"""
    result = analyze_citations_and_references(text)

    assert result["in_text_citation_numbers"] == [1, 2, 3]
    assert result["citations_without_reference"] == []
    assert result["references_not_cited"] == []
    assert len(result["references"]) == 3
    assert result["references"][0]["number"] == 1
    assert "Layer normalization" in result["references"][0]["text"]


def test_matches_bold_bracketed_references():
    text = """Prior methods [1] achieved good results.

## References
**[1]** Author One, "Seminal Work," NeurIPS, 2017.
"""
    result = analyze_citations_and_references(text)

    assert result["in_text_citation_numbers"] == [1]
    assert result["citations_without_reference"] == []
    assert len(result["references"]) == 1
    assert result["references"][0]["number"] == 1
