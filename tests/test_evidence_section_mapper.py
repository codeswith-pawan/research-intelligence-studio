from agents.evidence_section_mapper import map_evidence_to_sections


def test_maps_quote_to_unique_academic_section():
    markdown = """# Test Paper

## Abstract
This is a short abstract.

## Introduction
Hostel food waste prediction requires reliable daily data collection and careful analysis.

## Methodology
The model uses a clustering algorithm.
"""
    evidence = [{
        "criterion_id": "data_quality",
        "quote": "Hostel food waste prediction requires reliable daily data collection and careful analysis.",
        "page_reference": "N/A",
    }]

    result = map_evidence_to_sections(markdown, evidence)

    assert result[0]["academic_section"] == "introduction"
    assert result[0]["section_mapping_status"] == "mapped"
    assert evidence[0].get("academic_section") is None


def test_marks_quote_in_multiple_sections_ambiguous():
    quote = "The proposed method uses reliable daily data collection and careful analysis."
    markdown = f"""## Introduction
{quote}

## Results
{quote}
"""
    evidence = [{"quote": quote}]

    result = map_evidence_to_sections(markdown, evidence)

    assert result[0]["academic_section"] is None
    assert result[0]["section_mapping_status"] == "ambiguous"


def test_marks_quote_not_found_unmapped():
    markdown = """## Abstract
This paper studies food waste.
"""
    evidence = [{"quote": "A unique quotation that does not occur in this paper."}]

    result = map_evidence_to_sections(markdown, evidence)

    assert result[0]["academic_section"] is None
    assert result[0]["section_mapping_status"] == "unmapped"
