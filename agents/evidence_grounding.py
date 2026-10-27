"""Conservative grounding helpers for model-generated review narratives."""

import re
from typing import Iterable, Optional, Set, Tuple


EVIDENCE_GROUNDING_INSTRUCTIONS = """
Evidence-grounding rules for every narrative field:
- The supplied evidence records are the only allowed source for paper-specific claims.
- Each factual sentence in the executive summary, detailed assessment, criterion narratives,
  and revision suggestions must cite a supplied VERIFIED evidence ID using exactly
  [Evidence: criterion_id:Qn]. Put the citation before the final punctuation on every sentence,
  not just once at the end of a paragraph.
- Never cite an unverified quotation. Do not turn a missing or unverified quote into a factual
  criticism. Do not claim that something is absent from the paper because it was not found in
  the extracted text. Omit unsupported statements instead of guessing or proposing generic flaws.
- For a criterion narrative, cite evidence from that same criterion. If no verified quotation
  supports a statement, omit it; the application will keep the manual-review status and report
  that the narrative was omitted.
- Scores and provisional recommendations are diagnostic inputs, not evidence for paper claims.
""".strip()

_EVIDENCE_TAG = re.compile(r"\[Evidence:\s*([^\]]+)\]", re.IGNORECASE)
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")

NO_GROUNDED_SUMMARY = (
    "No source-grounded synthesis summary is available; review the verified evidence manually."
)
NO_GROUNDED_CRITERION = (
    "No source-grounded narrative is available for this criterion; review its verified evidence manually."
)


def evidence_reference_id(criterion_id: str, evidence_index: int) -> str:
    """Return the stable identifier shared by prompt context and saved report evidence."""
    return f"{criterion_id}:Q{evidence_index}"


def build_grounded_extraction_context(extractions: Iterable) -> Tuple[list, Set[str]]:
    """Expose only source-verified quotation text to the synthesis model."""
    context = []
    allowed_ids: Set[str] = set()
    for extraction in extractions:
        verified = []
        unverified_count = 0
        for index, item in enumerate(extraction.evidence, start=1):
            if item.verification_status == "Verified":
                reference_id = evidence_reference_id(extraction.criterion_id, index)
                allowed_ids.add(reference_id)
                verified.append({
                    "evidence_id": reference_id,
                    "page_reference": item.page_reference,
                    "quote": item.quote,
                })
            else:
                unverified_count += 1
        context.append({
            "criterion_id": extraction.criterion_id,
            "score": extraction.score,
            "verified_evidence": verified,
            "unverified_evidence_excluded": unverified_count,
        })
    return context, allowed_ids


def format_grounded_evidence_summary(extractions: Iterable) -> str:
    """Format verified quotations by criterion in human-readable markdown for LLM synthesis."""
    lines = []
    for extraction in extractions:
        cid = extraction.criterion_id
        verified = [
            (evidence_reference_id(cid, idx), item)
            for idx, item in enumerate(extraction.evidence, start=1)
            if item.verification_status == "Verified"
        ]
        lines.append(f"### Criterion: {cid} (Score: {extraction.score})")
        if verified:
            for eid, item in verified:
                q = item.quote.replace("\n", " ").strip()
                lines.append(f'  - [{eid}] ({item.page_reference}): "{q}"')
        else:
            lines.append("  - (NO verified quotations available)")
    return "\n".join(lines).strip()


def _sentence_has_valid_evidence(
    sentence: str,
    allowed_ids: Set[str],
    required_criterion_id: Optional[str] = None,
) -> bool:
    tags = _EVIDENCE_TAG.findall(sentence)
    if not tags:
        return False
    if not re.search(r"\w", _EVIDENCE_TAG.sub("", sentence)):
        return False

    cited_ids = []
    for tag in tags:
        raw_items = [item.strip() for item in tag.split(",") if item.strip()]
        if not raw_items:
            return False
        last_prefix = required_criterion_id
        for item in raw_items:
            if ":" in item:
                last_prefix = item.split(":", 1)[0]
                cited_ids.append(item)
            elif item.startswith("Q") and last_prefix:
                cited_ids.append(f"{last_prefix}:{item}")
            else:
                cited_ids.append(item)

    if not cited_ids or any(item not in allowed_ids for item in cited_ids):
        return False
    if required_criterion_id is not None:
        prefix = f"{required_criterion_id}:Q"
        if any(not item.startswith(prefix) for item in cited_ids):
            return False
    return True


def sanitize_grounded_text(
    text: str,
    allowed_ids: Set[str],
    *,
    required_criterion_id: Optional[str] = None,
    fallback: Optional[str] = None,
) -> Tuple[Optional[str], int]:
    """Keep only sentences that cite verified evidence IDs; report omitted sentences."""
    if not isinstance(text, str) or not text.strip():
        return fallback, 0

    # Normalize tag placement so a tag following sentence-ending punctuation is not detached
    normalized_text = re.sub(r"([.!?])\s*(\[Evidence:\s*[^\]]+\])", r" \2\1", text.strip())
    sentences = [part.strip() for part in _SENTENCE_BOUNDARY.split(normalized_text) if part.strip()]
    retained = []
    omitted = 0
    for sentence in sentences:
        if _sentence_has_valid_evidence(sentence, allowed_ids, required_criterion_id):
            retained.append(sentence)
        else:
            omitted += 1

    if retained:
        return " ".join(retained), omitted
    return fallback, omitted


def sanitize_synthesis_payload(payload, allowed_ids: Set[str], return_breakdown: bool = False):
    """Remove synthesis statements without valid, source-verified citations.

    Returns:
        If return_breakdown is False: (sanitized_payload, omitted_count)
        If return_breakdown is True: (sanitized_payload, omitted_count, omitted_breakdown_dict)
    """
    data = payload.model_dump()
    omitted = 0
    breakdown = {
        "executive_summary": 0,
        "major_strengths": 0,
        "major_concerns": 0,
        "minor_issues": 0,
        "revision_suggestions": 0,
        "criterion_narrative": {},
    }

    data["executive_summary"], count = sanitize_grounded_text(
        data.get("executive_summary", ""), allowed_ids, fallback=NO_GROUNDED_SUMMARY
    )
    omitted += count
    breakdown["executive_summary"] = count

    for key in ("major_strengths", "major_concerns", "minor_issues"):
        kept = []
        key_omitted = 0
        for item in data["detailed_assessment"].get(key, []):
            grounded_item, count = sanitize_grounded_text(item, allowed_ids)
            omitted += count
            key_omitted += count
            if grounded_item:
                kept.append(grounded_item)
        data["detailed_assessment"][key] = kept
        breakdown[key] = key_omitted

    grounded_narratives = {}
    for criterion_id, narrative in data.get("criterion_narrative", {}).items():
        grounded_narrative, count = sanitize_grounded_text(
            narrative,
            allowed_ids,
            required_criterion_id=criterion_id,
            fallback=NO_GROUNDED_CRITERION,
        )
        omitted += count
        breakdown["criterion_narrative"][criterion_id] = count
        grounded_narratives[criterion_id] = grounded_narrative
    data["criterion_narrative"] = grounded_narratives

    kept_suggestions = []
    suggestions_omitted = 0
    for suggestion in data.get("revision_suggestions", []):
        grounded_suggestion, count = sanitize_grounded_text(suggestion, allowed_ids)
        omitted += count
        suggestions_omitted += count
        if grounded_suggestion:
            kept_suggestions.append(grounded_suggestion)
    data["revision_suggestions"] = kept_suggestions
    breakdown["revision_suggestions"] = suggestions_omitted

    validated_payload = payload.__class__.model_validate(data)
    if return_breakdown:
        return validated_payload, omitted, breakdown
    return validated_payload, omitted
