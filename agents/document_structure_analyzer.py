"""Rule-based detection of figures, tables, and equation-like content in extracted text."""
import re
from typing import Any, Dict, List


FIGURE_PATTERN = re.compile(
    r"^\s*(?:figure|fig\.?)\s*\.?\s*(\d+)\s*[:.)-]?\s*(.*)$",
    re.IGNORECASE,
)
TABLE_PATTERN = re.compile(
    r"^\s*table\s*\.?\s*(\d+)\s*[:.)-]?\s*(.*)$",
    re.IGNORECASE,
)
EQUATION_MARKERS = re.compile(
    r"\$[^$\n]+\$|\\\(|\\\[|\\begin\{(?:equation|align|math)\}|"
    r"\\(?:frac|sum|int|sqrt|times|cdot|prod)\b|[∑√∫≤≥≠≈∞]",
)


def _numbering_issues(items: List[Dict[str, Any]]) -> Dict[str, List[int]]:
    numbers = [item["number"] for item in items]
    duplicates = sorted({number for number in numbers if numbers.count(number) > 1})
    missing = []
    if numbers:
        observed = set(numbers)
        missing = [
            number for number in range(min(numbers), max(numbers) + 1)
            if number not in observed
        ]
    return {"duplicate_numbers": duplicates, "numbering_gaps": missing}


def analyze_document_structure(markdown_text: str) -> Dict[str, Any]:
    """Detect text-visible document elements; this does not inspect visual PDF layout."""
    figures: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = []
    equation_lines: List[Dict[str, Any]] = []
    markdown_table_lines: List[Dict[str, Any]] = []

    for line_number, line in enumerate((markdown_text or "").splitlines(), start=1):
        figure_match = FIGURE_PATTERN.match(line)
        if figure_match:
            figures.append({
                "number": int(figure_match.group(1)),
                "caption": figure_match.group(2).strip(),
                "line": line_number,
            })

        table_match = TABLE_PATTERN.match(line)
        if table_match:
            tables.append({
                "number": int(table_match.group(1)),
                "caption": table_match.group(2).strip(),
                "line": line_number,
            })

        if line.count("|") >= 2:
            markdown_table_lines.append({"line": line_number, "text": line.strip()})

        if EQUATION_MARKERS.search(line):
            equation_lines.append({"line": line_number, "text": line.strip()[:300]})

    return {
        "analysis_type": "rule_based_extracted_text_structure_analysis",
        "figures": figures,
        "figure_count": len(figures),
        "figure_numbering": _numbering_issues(figures),
        "tables": tables,
        "table_caption_count": len(tables),
        "table_numbering": _numbering_issues(tables),
        "markdown_table_lines": markdown_table_lines,
        "markdown_table_line_count": len(markdown_table_lines),
        "equation_like_lines": equation_lines,
        "equation_like_line_count": len(equation_lines),
        "detection_status": {
            "figures": "detected" if figures else "not_detected_in_extracted_text",
            "tables": (
                "detected" if tables or markdown_table_lines
                else "not_detected_in_extracted_text"
            ),
            "equations": (
                "detected" if equation_lines
                else "not_detected_in_extracted_text"
            ),
        },
        "limitations": [
            "Analyzes extracted text only; it does not visually inspect PDF pages.",
            "Figures, tables, or equations may be present but omitted by text extraction.",
            "Equation detection is heuristic and may include false positives.",
            "A detected caption does not establish that the associated figure or table is scientifically valid.",
        ],
    }
