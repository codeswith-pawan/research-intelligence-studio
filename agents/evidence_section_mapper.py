"""Conservatively map evidence quotes to detected academic sections."""

import re
from typing import Any, Dict, List, Tuple

from agents.academic_section_analyzer import detect_academic_sections


def _normalize_for_match(text: str) -> str:
    """Normalize Markdown, punctuation, and whitespace for matching."""
    text = re.sub(r"[*_`#]", "", str(text or "")).casefold()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _section_spans(markdown_text: str) -> List[Tuple[str, str]]:
    """Return section text using the first recognized heading per section."""
    lines = (markdown_text or "").splitlines()
    headings: List[Tuple[int, str]] = []
    seen_sections = set()

    for index, line in enumerate(lines):
        if not line.strip():
            continue

        detected = detect_academic_sections(line)
        for section, details in detected.items():
            if (
                section != "title_page"
                and section not in seen_sections
                and details.get("status") == "present"
                and details.get("heading")
            ):
                headings.append((index, section))
                seen_sections.add(section)
                break

    spans = []
    for position, (start, section) in enumerate(headings):
        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        spans.append((section, "\n".join(lines[start:end])))

    return spans


def map_evidence_to_sections(
    markdown_text: str,
    evidence_records: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return copied records with conservative section-mapping annotations.

    A quote maps only when found in exactly one detected section.
    This function neither mutates the inputs nor writes files.
    """
    spans = [
        (
            name,
            _normalize_for_match(body),
            re.sub(r"\s+", "", _normalize_for_match(body)),
        )
        for name, body in _section_spans(markdown_text)
    ]
    mapped_records = []

    for record in evidence_records:
        result = dict(record)
        quote = _normalize_for_match(record.get("quote", ""))
        compact_quote = re.sub(r"\s+", "", quote)
        matches = []

        if len(quote) >= 20:
            for section, body, compact_body in spans:
                if quote in body or (
                    len(compact_quote) >= 30 and compact_quote in compact_body
                ):
                    matches.append(section)

        matches = list(dict.fromkeys(matches))
        if len(matches) == 1:
            result["academic_section"] = matches[0]
            result["section_mapping_status"] = "mapped"
        elif len(matches) > 1:
            result["academic_section"] = None
            result["section_mapping_status"] = "ambiguous"
        else:
            result["academic_section"] = None
            result["section_mapping_status"] = "unmapped"

        result["section_mapping_method"] = "normalized_quote_match"
        mapped_records.append(result)

    return mapped_records
