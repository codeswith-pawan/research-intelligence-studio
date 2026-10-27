"""Rule-based detection of common academic paper sections."""

import re
from typing import Dict, List


SECTION_ALIASES = {
    "title_page": ["title", "title page", "paper title"],
    "abstract": ["abstract", "summary"],
    "introduction": ["introduction", "background", "intro"],
    "literature_review": [
        "literature review",
        "literature survey",
        "related work",
        "related works",
        "background and related work",
        "related work and background",
        "related work and preliminaries",
        "prior work",
        "state of the art",
        "previous work",
    ],
    "methodology": [
        "methodology",
        "methods",
        "materials and methods",
        "research methods",
        "proposed method",
        "approach",
        "model architecture",
        "architecture",
        "system design",
        "proposed approach",
        "the model",
        "framework",
    ],
    "results": [
        "results",
        "result analysis",
        "results analysis",
        "experiments",
        "experimental results",
        "evaluation",
        "data analysis",
        "results and analysis",
        "results and discussion",
        "experimental evaluation",
        "experiments and results",
    ],
    "discussion": ["discussion", "analysis and discussion", "analysis"],
    "conclusion": [
        "conclusion",
        "conclusions",
        "conclusion and future work",
        "concluding remarks",
        "future work",
        "summary and conclusion",
        "summary and conclusions",
    ],
    "acknowledgements": ["acknowledgements", "acknowledgments"],
    "references": [
        "references",
        "bibliography",
        "works cited",
        "literature cited",
    ],
}


def _normalize_heading(line: str) -> str:
    """Normalize a candidate heading for alias matching."""
    heading = re.sub(r"^\s{0,3}#{1,6}\s*", "", line).strip()
    heading = re.sub(r"\s+#+\s*$", "", heading).strip()
    heading = re.sub(r"^\d+(?:\.\d+)*[.)]?\s+", "", heading)
    heading = heading.strip(" :.-\t").lower()
    return re.sub(r"\s+", " ", heading)


def detect_academic_sections(markdown_text: str) -> Dict[str, dict]:
    """
    Detect common academic sections from Markdown headings.

    Returns a record for each known section with status and matching heading.
    This detects heading presence only; it does not judge section quality or
    guarantee PDF page references.
    """
    detected: Dict[str, str] = {}
    alias_to_section = {
        alias.lower(): section
        for section, aliases in SECTION_ALIASES.items()
        for alias in aliases
    }

    for line in markdown_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # Support Markdown headings and PDF-extracted emphasis/dash formats.
        candidate = re.sub(r"^\s{0,3}#{1,6}\s*", "", stripped)
        candidate = re.sub(r"[*_`]", "", candidate).strip()
        candidate = re.sub(r"\s+#+\s*$", "", candidate).strip()
        candidate = re.sub(r"^\d+(?:\.\d+)*[.)]?\s+", "", candidate)
        candidate = candidate.strip(" :.-\t").lower()

        # PDF extraction can split a heading's first letter from its word.
        candidate = re.sub(r"\b([a-z])\s+([a-z]{2,})\b", r"\1\2", candidate)
        candidate = re.sub(r"\s+", " ", candidate).strip()

        candidates = [candidate]
        for separator in ("—", "–", ":", " - "):
            if separator in candidate:
                candidates.append(candidate.split(separator, 1)[0].strip())

        section = next(
            (alias_to_section[item] for item in candidates if item in alias_to_section),
            None,
        )
        if section and section not in detected:
            detected[section] = stripped

    title_candidate = None
    for line in markdown_text.splitlines():
        stripped = line.strip()
        if stripped:
            title_candidate = re.sub(r"^#{1,6}\s*", "", stripped).strip()
            if title_candidate:
                break

    result = {
        section: {
            "status": "present" if section in detected else "uncertain",
            "heading": detected.get(section),
        }
        for section in SECTION_ALIASES
    }
    result["title_page"]["title_candidate"] = title_candidate
    return result


def save_section_analysis(
    markdown_text: str,
    output_dir: str,
    paper_filename: str,
) -> str:
    """Save section detection results as a separate JSON report."""
    import json
    from pathlib import Path

    output_path = Path(output_dir) / "section_analysis"
    output_path.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", paper_filename)
    report_path = output_path / f"{safe_name}.json"

    report = {
        "paper_filename": paper_filename,
        "analysis_type": "rule_based_heading_detection",
        "sections": detect_academic_sections(markdown_text),
        "limitations": [
            "Heading detection does not assess section quality.",
            "An uncertain status does not prove that a section is absent.",
            "PDF page references are not generated by this analyzer.",
        ],
    }

    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return str(report_path)
