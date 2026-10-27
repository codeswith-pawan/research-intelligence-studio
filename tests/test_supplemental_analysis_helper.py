import json

import web_app


def test_supplemental_helper_saves_all_three_reports(tmp_path):
    outputs_dir = tmp_path / "outputs"

    result = web_app._save_additional_analysis_reports(
        markdown_text=(
            "The model achieved 95% accuracy.\n\n"
            "## References\n1. A. Author, Complete Journal Reference, 2024."
        ),
        outputs_dir=str(outputs_dir),
        paper_filename="sample.pdf",
    )

    assert set(result) == {
        "citation_reference",
        "document_structure",
        "cross_section_consistency",
    }
    assert all(item["status"] == "completed" for item in result.values())

    for name in result:
        report_path = outputs_dir / "quality_analysis" / f"sample.pdf.{name}.json"
        assert report_path.is_file()
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["paper_filename"] == "sample.pdf"
        assert report["status"] == "completed"


def test_supplemental_helper_preserves_existing_reports(tmp_path):
    report_dir = tmp_path / "outputs" / "quality_analysis"
    report_dir.mkdir(parents=True)
    analyzer_names = (
        "citation_reference",
        "document_structure",
        "cross_section_consistency",
    )
    originals = {}

    for name in analyzer_names:
        path = report_dir / f"sample.pdf.{name}.json"
        originals[path] = '{"existing": true}\n'
        path.write_text(originals[path], encoding="utf-8")

    result = web_app._save_additional_analysis_reports(
        markdown_text="## Results\nThe result is 95%.",
        outputs_dir=str(tmp_path / "outputs"),
        paper_filename="sample.pdf",
    )

    assert all(
        result[name]["status"] == "preserved_existing"
        for name in analyzer_names
    )
    for path, original_content in originals.items():
        assert path.read_text(encoding="utf-8") == original_content
