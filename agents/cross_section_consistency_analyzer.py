"""Conservative checks for possible cross-section numerical inconsistencies."""
import re
from typing import Any, Dict, List, Optional

from agents.evidence_section_mapper import _section_spans


NUMBER_PATTERN = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*"
    r"(%|percent\b|accuracy\b|accurate\b|clusters?\b|samples?\b|"
    r"instances?\b|records?\b|participants?\b|subjects?\b|"
    r"patients?\b|days?\b|years?\b)?",
    re.IGNORECASE,
)


def _metric_signature(context: str, unit: str) -> Optional[str]:
    """Return a conservative metric category for a numeric claim."""
    normalized = context.casefold()

    if re.search(r"pattern matching|similarity|\\bsimilar\\b", normalized):
        return "similarity"
    if re.search(r"accuracy|\\baccurate\\b|correctly classified", normalized):
        return "accuracy"
    if unit in {"sample", "samples"}:
        return "sample_count"
    if unit in {"cluster", "clusters"}:
        return "cluster_count"
    if unit in {"instance", "instances", "record", "records"}:
        return "record_count"
    if unit in {"day", "days", "year", "years"}:
        return "duration"
    return None


def _extract_numeric_claims(section: str, text: str) -> List[Dict[str, Any]]:
    claims = []
    for match in NUMBER_PATTERN.finditer(text):
        # Ignore numbered citations such as [17] and [20].
        if match.start() > 0 and text[match.start() - 1] == "[":
            continue

        value_text = match.group(1)
        unit = (match.group(2) or "").casefold()
        try:
            value = float(value_text)
        except ValueError:
            continue

        if not unit and len(value_text) == 1:
            continue

        context = text[max(0, match.start() - 100):min(len(text), match.end() + 100)]
        context = context.replace("\\n", " ").strip()
        claims.append({
            "section": section,
            "value": value,
            "unit": unit,
            "metric": _metric_signature(context, unit),
            "context": context,
        })
    return claims


def analyze_cross_section_consistency(markdown_text: str) -> Dict[str, Any]:
    """Flag repeated numerical values that differ across recognized paper sections.

    These are review prompts, not confirmed contradictions. This heuristic does
    not understand units, experimental conditions, or whether values are comparable.
    """
    spans = _section_spans(markdown_text or "")
    relevant = {
        section: text for section, text in spans
        if section in {"abstract", "methodology", "results", "discussion", "conclusion"}
    }

    claims = []
    for section, text in relevant.items():
        claims.extend(_extract_numeric_claims(section, text))

    findings = []
    for index, first in enumerate(claims):
        for second in claims[index + 1:]:
            if first["section"] == second["section"]:
                continue
            if first["unit"] != second["unit"]:
                continue
            # Compare only when both values have the same recognized metric type.
            if not first["metric"] or first["metric"] != second["metric"]:
                continue
            if first["value"] == second["value"]:
                continue
            # Require contextual overlap to avoid flagging every different metric.
            first_words = set(re.findall(r"[a-z]{4,}", first["context"].casefold()))
            second_words = set(re.findall(r"[a-z]{4,}", second["context"].casefold()))
            overlap = first_words & second_words
            if len(overlap) < 2:
                continue
            finding = {
                "type": "possible_numerical_inconsistency",
                "severity": "medium",
                "status": "requires_human_review",
                "sections": [first["section"], second["section"]],
                "values": [first["value"], second["value"]],
                "unit": first["unit"] or None,
                "shared_context_terms": sorted(overlap)[:10],
                "evidence": [first["context"], second["context"]],
            }
            if finding not in findings:
                findings.append(finding)

    return {
        "analysis_type": "heuristic_cross_section_consistency",
        "sections_checked": sorted(relevant),
        "numeric_claims_checked": len(claims),
        "finding_count": len(findings),
        "findings": findings,
        "limitations": [
            "Only recognized section headings are checked.",
            "Numerical differences may be legitimate when metrics or conditions differ.",
            "A missing section is not proof of inconsistency.",
            "Findings are heuristic prompts for human review, not confirmed contradictions.",
        ],
    }
