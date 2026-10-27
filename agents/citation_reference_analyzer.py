"""Conservative, rule-based analysis of numbered citations and references."""
import re
from typing import Any, Dict, List


def _clean_heading(line: str) -> str:
    """Normalize headings, including PDF extraction with split/bold letters."""
    line = re.sub(r"[*_`#]", "", line).casefold()
    return re.sub(r"[^a-z]", "", line)


def _find_reference_heading(lines: List[str]) -> int:
    aliases = {"references", "bibliography", "workscited", "literaturecited"}
    for index, line in enumerate(lines):
        if _clean_heading(line) in aliases:
            return index
    return len(lines)


def _parse_numbered_references(lines: List[str]) -> List[Dict[str, Any]]:
    """Collect numbered reference entries, retaining continuation lines."""
    entries: List[Dict[str, Any]] = []
    current_number = None
    current_lines: List[str] = []
    start_pattern = re.compile(r"^\s*[*_]*(?:\[(\d{1,3})\]|(\d{1,3})[.)])[*_]*\s+(.+?)\s*$")

    def flush() -> None:
        if current_number is not None:
            text = " ".join(part.strip() for part in current_lines if part.strip())
            entries.append({
                "number": current_number,
                "text": text,
                "potentially_incomplete": len(text) < 35 or not bool(
                    re.search(r"\b(?:19|20)\d{2}\b", text)
                ),
            })

    for line in lines:
        match = start_pattern.match(line)
        if match:
            flush()
            current_number = int(match.group(1) or match.group(2))
            current_lines = [match.group(3)]
        elif current_number is not None and line.strip():
            current_lines.append(line.strip())
    flush()
    return entries


def analyze_citations_and_references(markdown_text: str) -> Dict[str, Any]:
    """Analyze numbered citations and references without asserting factual validity."""
    lines = (markdown_text or "").splitlines()
    heading_index = _find_reference_heading(lines)
    body = "\n".join(lines[:heading_index])
    reference_text = lines[heading_index + 1:] if heading_index < len(lines) else []

    citation_numbers = set()
    for match in re.finditer(r"\[([0-9,\s\-–]+)\]", body):
        for token in re.split(r"[,;]", match.group(1)):
            token = token.strip()
            range_match = re.fullmatch(r"(\d{1,3})\s*[-–]\s*(\d{1,3})", token)
            if range_match:
                start, end = map(int, range_match.groups())
                if start <= end and end - start <= 100:
                    citation_numbers.update(n for n in range(start, end + 1) if n > 0)
            elif token.isdigit():
                val = int(token)
                if val > 0:
                    citation_numbers.add(val)

    references = _parse_numbered_references(reference_text)
    reference_numbers = [entry["number"] for entry in references]
    reference_number_set = set(reference_numbers)

    # Conservative screening findings; these are prompts for human review,
    # not confirmed citation or bibliography errors.
    findings = []
    for number in sorted(citation_numbers - reference_number_set):
        findings.append({
            "type": "citation_without_matching_reference",
            "severity": "medium",
            "status": "requires_human_review",
            "reference_number": number,
            "message": f"Citation [{number}] has no matching numbered reference.",
        })
    for number in sorted(reference_number_set - citation_numbers):
        findings.append({
            "type": "reference_not_cited_in_detected_text",
            "severity": "low",
            "status": "requires_human_review",
            "reference_number": number,
            "message": f"Reference {number} was not matched to a detected in-text citation.",
        })
    for number in sorted({
        item for item in reference_numbers if reference_numbers.count(item) > 1
    }):
        findings.append({
            "type": "duplicate_reference_number",
            "severity": "medium",
            "status": "requires_human_review",
            "reference_number": number,
            "message": f"Reference number {number} appears more than once.",
        })
    for number in sorted(
        entry["number"] for entry in references if entry["potentially_incomplete"]
    ):
        findings.append({
            "type": "potentially_incomplete_reference",
            "severity": "low",
            "status": "requires_human_review",
            "reference_number": number,
            "message": f"Reference {number} may be incomplete.",
        })

    return {
        "finding_count": len(findings),
        "findings": findings,
        "analysis_type": "rule_based_numbered_citation_reference_analysis",
        "reference_heading_found": heading_index < len(lines),
        "in_text_citation_numbers": sorted(citation_numbers),
        "references": references,
        "citations_without_reference": sorted(citation_numbers - reference_number_set),
        "references_not_cited": sorted(reference_number_set - citation_numbers),
        "duplicate_reference_numbers": sorted({
            number for number in reference_numbers
            if reference_numbers.count(number) > 1
        }),
        "potentially_incomplete_references": [
            entry["number"] for entry in references
            if entry["potentially_incomplete"]
        ],
        "limitations": [
            "Detects numbered bracket citations only; author-year citations are not matched.",
            "Reference numbering and formatting can be distorted during PDF text extraction.",
            "Potentially incomplete entries require human review and are not confirmed errors.",
            "A citation match does not verify that a source supports the claim."
        ],
    }
