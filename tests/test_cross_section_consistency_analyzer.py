from agents.cross_section_consistency_analyzer import (
    analyze_cross_section_consistency,
)


def test_flags_different_sample_counts_in_related_sections():
    text = """
## Abstract
The food waste prediction model was evaluated on 100 samples
from the campus meal dataset.

## Methodology
The model was trained using the collected food waste dataset.

## Results
The food waste prediction model was evaluated on 200 samples
from the campus meal dataset.

## Conclusion
The model may help improve food waste monitoring.
"""
    result = analyze_cross_section_consistency(text)

    assert result["numeric_claims_checked"] >= 2
    assert result["finding_count"] >= 1
    assert any(
        finding["type"] == "possible_numerical_inconsistency"
        and finding["status"] == "requires_human_review"
        for finding in result["findings"]
    )


def test_does_not_flag_equal_values_across_sections():
    text = """
## Abstract
The food waste model achieved 95% accuracy on the evaluation dataset.

## Results
The food waste model achieved 95% accuracy on the evaluation dataset.
"""
    result = analyze_cross_section_consistency(text)

    assert result["finding_count"] == 0


def test_does_not_flag_unrelated_numbers():
    text = """
## Methodology
The food waste dataset contains 100 samples collected for model training.

## Conclusion
The mobile application was deployed in 30 days for campus users.
"""
    result = analyze_cross_section_consistency(text)

    assert result["finding_count"] == 0


def test_empty_text_returns_valid_result():
    result = analyze_cross_section_consistency("")

    assert result["finding_count"] == 0
    assert result["findings"] == []
    assert "limitations" in result


def test_findings_are_marked_for_human_review():
    text = """
## Abstract
The campus food waste model used 100 samples from the same dataset.

## Results
The campus food waste model used 200 samples from the same dataset.
"""
    result = analyze_cross_section_consistency(text)

    for finding in result["findings"]:
        assert finding["status"] == "requires_human_review"
        assert len(finding["evidence"]) == 2
        assert len(finding["sections"]) == 2
