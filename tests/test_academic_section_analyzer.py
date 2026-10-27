from agents.academic_section_analyzer import detect_academic_sections


def test_detect_academic_sections_from_markdown():
    text = """# Smart Kitchen Analytics

## Abstract
This paper studies food waste.

## 1. Introduction
Food waste is a major challenge.

## 2. Related Work
Previous studies use machine learning.

## 3. Methodology
We train a prediction model.

## 4. Results
The model performs well.

## 5. Conclusion
The approach can reduce waste.

## References
Reference list goes here.
"""
    sections = detect_academic_sections(text)

    assert sections["abstract"]["status"] == "present"
    assert sections["introduction"]["status"] == "present"
    assert sections["literature_review"]["status"] == "present"
    assert sections["methodology"]["status"] == "present"
    assert sections["results"]["status"] == "present"
    assert sections["conclusion"]["status"] == "present"
    assert sections["references"]["status"] == "present"


def test_missing_sections_are_uncertain():
    sections = detect_academic_sections(
        "# A Research Paper\n\n## Abstract\nShort abstract."
    )

    assert sections["abstract"]["status"] == "present"
    assert sections["methodology"]["status"] == "uncertain"
    assert sections["references"]["status"] == "uncertain"


def test_empty_document_does_not_crash():
    sections = detect_academic_sections("")

    assert sections
    assert all(item["status"] == "uncertain" for item in sections.values())


def test_numbered_and_alternative_headings():
    text = """## 1. Introduction
Introduction text.

## 2. Materials and Methods
Methods text.

## 3. Experimental Results
Results text.

## 4. Bibliography
References go here.
"""
    sections = detect_academic_sections(text)

    assert sections["introduction"]["status"] == "present"
    assert sections["methodology"]["status"] == "present"
    assert sections["results"]["status"] == "present"
    assert sections["references"]["status"] == "present"


def test_title_candidate_is_extracted_without_claiming_title_page():
    text = """# Machine Learning for Healthcare

## Abstract
This paper studies healthcare prediction.
"""
    sections = detect_academic_sections(text)

    assert (
        sections["title_page"]["title_candidate"]
        == "Machine Learning for Healthcare"
    )
    assert sections["title_page"]["status"] == "uncertain"


def test_save_section_analysis_creates_separate_json_report(tmp_path):
    import json

    from agents.academic_section_analyzer import save_section_analysis

    markdown = """# Test Research Paper

## Abstract
A short abstract for testing.

## Introduction
A short introduction for testing.
"""
    report_path = save_section_analysis(
        markdown_text=markdown,
        output_dir=str(tmp_path),
        paper_filename="test_paper.pdf",
    )

    report_file = tmp_path / "section_analysis" / "test_paper.pdf.json"
    assert report_file.exists()
    assert report_path == str(report_file)

    report = json.loads(report_file.read_text(encoding="utf-8"))
    assert report["paper_filename"] == "test_paper.pdf"
    assert report["sections"]["abstract"]["status"] == "present"
    assert report["sections"]["introduction"]["status"] == "present"
    assert "limitations" in report


def test_section_analysis_report_is_isolated_per_output_directory(tmp_path):
    from agents.academic_section_analyzer import save_section_analysis

    markdown = """# Isolated Test Paper

## Abstract
Test abstract content.

## Methodology
Test methodology content.
"""
    run_a = tmp_path / "run_a" / "outputs"
    run_b = tmp_path / "run_b" / "outputs"

    report_a = save_section_analysis(markdown, str(run_a), "paper.pdf")
    report_b = save_section_analysis(markdown, str(run_b), "paper.pdf")

    assert report_a != report_b
    assert (run_a / "section_analysis" / "paper.pdf.json").exists()
    assert (run_b / "section_analysis" / "paper.pdf.json").exists()
