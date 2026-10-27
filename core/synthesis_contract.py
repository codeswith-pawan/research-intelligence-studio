"""Shared Synthesis and Output Contract for Research Intelligence Studio.

Defines the standard output specification, section names, valid section statuses,
diagnostic message generation, and output contract validation across all review workflows.
"""

from typing import Dict, List, Optional, Tuple, Any, Set
from pydantic import BaseModel, Field

# Core Section Names
SECTION_SUMMARY = "executive_summary"
SECTION_STRENGTHS = "major_strengths"
SECTION_CONCERNS = "major_concerns"
SECTION_MINOR_ISSUES = "minor_issues"
SECTION_SUGGESTIONS = "revision_suggestions"
SECTION_CRITERION_NARRATIVE = "criterion_narrative"

CORE_SECTIONS = [
    SECTION_SUMMARY,
    SECTION_STRENGTHS,
    SECTION_CONCERNS,
    SECTION_MINOR_ISSUES,
    SECTION_SUGGESTIONS,
    SECTION_CRITERION_NARRATIVE,
]

# Valid Section Statuses
STATUS_VERIFIED = "verified_content"
STATUS_NOT_IDENTIFIED = "not_identified_in_text"
STATUS_UNSUPPORTED = "unsupported_by_direct_quotation"
STATUS_FILTERED = "filtered_by_evidence_gate"
STATUS_SYNTHESIS_ERROR = "synthesis_error"

VALID_SECTION_STATUSES: Set[str] = {
    STATUS_VERIFIED,
    STATUS_NOT_IDENTIFIED,
    STATUS_UNSUPPORTED,
    STATUS_FILTERED,
    STATUS_SYNTHESIS_ERROR,
}


def get_section_diagnostic_message(
    section_name: str,
    status: str,
    omitted_count: int = 0
) -> str:
    """Generate a truthful, human-readable diagnostic explanation for a section status."""
    display_name = section_name.replace("_", " ").title()

    if status == STATUS_VERIFIED:
        return f"{display_name} is grounded in verified source quotations."

    if status == STATUS_FILTERED:
        if omitted_count > 0:
            return (
                f"Candidate statements proposed for {display_name.lower()} were omitted by the evidence "
                f"safety gate because they lacked valid citations to verified source quotations "
                f"({omitted_count} statement(s) filtered)."
            )
        return (
            f"Candidate statements proposed for {display_name.lower()} were omitted by the evidence "
            f"safety gate because they lacked valid citations to verified source quotations."
        )

    if status == STATUS_UNSUPPORTED:
        return (
            f"Candidate findings for {display_name.lower()} were not supported by direct source-verified "
            f"quotations and were omitted in accordance with evidence grounding policy."
        )

    if status == STATUS_SYNTHESIS_ERROR:
        return f"{display_name} is unavailable due to synthesis schema validation or processing failure."

    if status == STATUS_NOT_IDENTIFIED:
        if section_name in (SECTION_CONCERNS, SECTION_MINOR_ISSUES):
            return "No specific weaknesses or concerns were identified in the verified source text."
        if section_name == SECTION_SUGGESTIONS:
            return "No specific revision suggestions were identified in the verified source text."
        if section_name == SECTION_STRENGTHS:
            return "No specific strengths were identified in the verified source text."
        return f"No specific content was identified in the verified source text for {display_name.lower()}."

    return f"Status: {status}."


def evaluate_section_statuses(
    summary_text: Optional[str],
    major_strengths: List[str],
    major_concerns: List[str],
    minor_issues: List[str],
    suggestions: List[str],
    criterion_narratives: Dict[str, str],
    extractions: List[Any],
    synthesis_error: Optional[str] = None,
    omitted_breakdown: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Determine explicit truthful section statuses and diagnostic messages.

    Evaluates:
    - verified_content: Content is present and successfully cited verified evidence
    - filtered_by_evidence_gate: Candidate statements were generated but filtered out due to citation rules
    - unsupported_by_direct_quotation: Extracted quotes were unverified
    - synthesis_error: Synthesis failed validation or provider error
    - not_identified_in_text: Neither verified evidence nor candidate items identified
    """
    omitted = omitted_breakdown or {}
    statuses: Dict[str, str] = {}
    diagnostics: Dict[str, str] = {}

    # Gather evidence statistics
    total_verified_quotes = 0
    total_unverified_quotes = 0
    for ext in extractions:
        for ev in getattr(ext, "evidence", []):
            if getattr(ev, "verification_status", "") == "Verified":
                total_verified_quotes += 1
            else:
                total_unverified_quotes += 1

    # 1. Executive Summary
    summary_omitted = omitted.get(SECTION_SUMMARY, 0)
    has_valid_summary = (
        bool(summary_text)
        and summary_text.strip()
        and not summary_text.startswith("No source-grounded")
        and not summary_text.startswith("No validated")
        and not summary_text.startswith("Executive summary narrative synthesis was omitted")
    )

    if has_valid_summary:
        statuses[SECTION_SUMMARY] = STATUS_VERIFIED
    elif synthesis_error:
        statuses[SECTION_SUMMARY] = STATUS_SYNTHESIS_ERROR
    elif summary_omitted > 0:
        statuses[SECTION_SUMMARY] = STATUS_FILTERED
    elif total_verified_quotes == 0 and total_unverified_quotes > 0:
        statuses[SECTION_SUMMARY] = STATUS_UNSUPPORTED
    else:
        statuses[SECTION_SUMMARY] = STATUS_NOT_IDENTIFIED

    diagnostics[SECTION_SUMMARY] = get_section_diagnostic_message(
        SECTION_SUMMARY, statuses[SECTION_SUMMARY], summary_omitted
    )

    # 2. Major Strengths
    str_omitted = omitted.get(SECTION_STRENGTHS, 0)
    if major_strengths:
        statuses[SECTION_STRENGTHS] = STATUS_VERIFIED
    elif synthesis_error:
        statuses[SECTION_STRENGTHS] = STATUS_SYNTHESIS_ERROR
    elif str_omitted > 0:
        statuses[SECTION_STRENGTHS] = STATUS_FILTERED
    elif total_verified_quotes == 0 and total_unverified_quotes > 0:
        statuses[SECTION_STRENGTHS] = STATUS_UNSUPPORTED
    else:
        statuses[SECTION_STRENGTHS] = STATUS_NOT_IDENTIFIED

    diagnostics[SECTION_STRENGTHS] = get_section_diagnostic_message(
        SECTION_STRENGTHS, statuses[SECTION_STRENGTHS], str_omitted
    )

    # 3. Major Concerns
    con_omitted = omitted.get(SECTION_CONCERNS, 0)
    if major_concerns:
        statuses[SECTION_CONCERNS] = STATUS_VERIFIED
    elif synthesis_error:
        statuses[SECTION_CONCERNS] = STATUS_SYNTHESIS_ERROR
    elif con_omitted > 0:
        statuses[SECTION_CONCERNS] = STATUS_FILTERED
    elif total_verified_quotes == 0 and total_unverified_quotes > 0:
        statuses[SECTION_CONCERNS] = STATUS_UNSUPPORTED
    else:
        statuses[SECTION_CONCERNS] = STATUS_NOT_IDENTIFIED

    diagnostics[SECTION_CONCERNS] = get_section_diagnostic_message(
        SECTION_CONCERNS, statuses[SECTION_CONCERNS], con_omitted
    )

    # 4. Minor Issues
    min_omitted = omitted.get(SECTION_MINOR_ISSUES, 0)
    if minor_issues:
        statuses[SECTION_MINOR_ISSUES] = STATUS_VERIFIED
    elif synthesis_error:
        statuses[SECTION_MINOR_ISSUES] = STATUS_SYNTHESIS_ERROR
    elif min_omitted > 0:
        statuses[SECTION_MINOR_ISSUES] = STATUS_FILTERED
    elif total_verified_quotes == 0 and total_unverified_quotes > 0:
        statuses[SECTION_MINOR_ISSUES] = STATUS_UNSUPPORTED
    else:
        statuses[SECTION_MINOR_ISSUES] = STATUS_NOT_IDENTIFIED

    diagnostics[SECTION_MINOR_ISSUES] = get_section_diagnostic_message(
        SECTION_MINOR_ISSUES, statuses[SECTION_MINOR_ISSUES], min_omitted
    )

    # 5. Revision Suggestions
    sug_omitted = omitted.get(SECTION_SUGGESTIONS, 0)
    if suggestions:
        statuses[SECTION_SUGGESTIONS] = STATUS_VERIFIED
    elif synthesis_error:
        statuses[SECTION_SUGGESTIONS] = STATUS_SYNTHESIS_ERROR
    elif sug_omitted > 0:
        statuses[SECTION_SUGGESTIONS] = STATUS_FILTERED
    elif total_verified_quotes == 0 and total_unverified_quotes > 0:
        statuses[SECTION_SUGGESTIONS] = STATUS_UNSUPPORTED
    else:
        statuses[SECTION_SUGGESTIONS] = STATUS_NOT_IDENTIFIED

    diagnostics[SECTION_SUGGESTIONS] = get_section_diagnostic_message(
        SECTION_SUGGESTIONS, statuses[SECTION_SUGGESTIONS], sug_omitted
    )

    # 6. Criterion Narratives
    crit_omitted_dict = omitted.get(SECTION_CRITERION_NARRATIVE, {})
    all_narratives_verified = True
    any_narratives_filtered = False
    for cid, narr in criterion_narratives.items():
        c_omitted = crit_omitted_dict.get(cid, 0) if isinstance(crit_omitted_dict, dict) else 0
        if c_omitted > 0:
            any_narratives_filtered = True
        if not narr or "No source-grounded" in narr:
            all_narratives_verified = False

    if all_narratives_verified and criterion_narratives:
        statuses[SECTION_CRITERION_NARRATIVE] = STATUS_VERIFIED
    elif synthesis_error:
        statuses[SECTION_CRITERION_NARRATIVE] = STATUS_SYNTHESIS_ERROR
    elif any_narratives_filtered:
        statuses[SECTION_CRITERION_NARRATIVE] = STATUS_FILTERED
    elif total_verified_quotes == 0 and total_unverified_quotes > 0:
        statuses[SECTION_CRITERION_NARRATIVE] = STATUS_UNSUPPORTED
    else:
        statuses[SECTION_CRITERION_NARRATIVE] = STATUS_NOT_IDENTIFIED

    diagnostics[SECTION_CRITERION_NARRATIVE] = get_section_diagnostic_message(
        SECTION_CRITERION_NARRATIVE, statuses[SECTION_CRITERION_NARRATIVE]
    )

    return statuses, diagnostics


def generate_deterministic_fallback_summary(
    paper_title: str,
    extractions: List[Any],
    score: Optional[float],
    provisional_rec: Optional[str],
    status: str,
    reason: Optional[str] = None
) -> str:
    """Generate a truthful, deterministic structured summary without fabricating paper claims."""
    criteria_names = [getattr(e, "criterion_id", "unknown") for e in extractions]
    verified_count = 0
    unverified_count = 0
    for e in extractions:
        for ev in getattr(e, "evidence", []):
            if getattr(ev, "verification_status", "") == "Verified":
                verified_count += 1
            else:
                unverified_count += 1

    lines = [
        f"This review evaluated manuscript '{paper_title}' across {len(extractions)} configured criteria "
        f"({', '.join(criteria_names)})."
    ]

    if verified_count > 0:
        lines.append(
            f"The extraction pipeline verified {verified_count} direct source quotation(s)"
            + (f" ({unverified_count} quotation(s) were unverified and excluded)." if unverified_count else ".")
        )
    else:
        lines.append("No direct quotations could be verified against the source text.")

    if score is not None and provisional_rec:
        lines.append(
            f"The diagnostic weighted rubric score is {score:.1f}/100, provisionally mapping to '{provisional_rec}'."
        )

    if status == STATUS_FILTERED:
        lines.append(
            "Narrative synthesis statements proposed by the model were omitted by the evidence safety gate "
            "(filtered_by_evidence_gate) because they lacked valid citations to verified source quotations. "
            "Consult the verified evidence records below for direct source text."
        )
    elif status == STATUS_SYNTHESIS_ERROR:
        lines.append(
            "Automated narrative synthesis did not pass schema validation or encountered an inference error "
            "(synthesis_error). Editorial narrative was withheld; consult individual criterion evidence records."
        )
    elif status == STATUS_UNSUPPORTED:
        lines.append(
            "Source-grounded narrative could not be generated because extracted claims lacked direct verified quotations "
            "(unsupported_by_direct_quotation)."
        )

    if reason:
        lines.append(f"Diagnostic note: {reason}")

    return " ".join(lines)


def validate_review_output_contract(review: Any) -> Tuple[bool, List[str]]:
    """Validate that a Review object satisfies the Phase 4 Shared Output Contract."""
    errors = []

    # 1. Identification
    if not getattr(review, "paper_id", None):
        errors.append("paper_id is missing or empty")
    if not getattr(review, "paper_title", None):
        errors.append("paper_title is missing or empty")
    if not getattr(review, "paper_filename", None):
        errors.append("paper_filename is missing or empty")

    # 2. Statuses & Recommendations
    rec = getattr(review, "recommendation", None)
    prov = getattr(review, "provisional_recommendation", None)
    if not rec:
        errors.append("recommendation is missing")
    if getattr(review, "review_status", None) not in ("complete", "incomplete", "manual_review", "failed"):
        errors.append(f"Invalid review_status: {getattr(review, 'review_status', None)}")

    # 3. Section Statuses
    section_statuses = getattr(review, "section_statuses", {})
    for sec in (SECTION_SUMMARY, SECTION_STRENGTHS, SECTION_CONCERNS, SECTION_MINOR_ISSUES, SECTION_SUGGESTIONS):
        st = section_statuses.get(sec)
        if not st:
            errors.append(f"section_statuses missing key: {sec}")
        elif st not in VALID_SECTION_STATUSES:
            errors.append(f"Invalid section status for {sec}: {st}")

    # 4. Content Integrity
    summary = getattr(review, "executive_summary", "")
    if not summary or not summary.strip():
        errors.append("executive_summary must not be empty")

    assessment = getattr(review, "detailed_assessment", None)
    if not assessment:
        errors.append("detailed_assessment is missing")
    else:
        for field in ("major_strengths", "major_concerns", "minor_issues"):
            val = getattr(assessment, field, None)
            if val is None or not isinstance(val, list):
                errors.append(f"detailed_assessment.{field} must be a list")

    suggestions = getattr(review, "revision_suggestions", None)
    if suggestions is None or not isinstance(suggestions, list):
        errors.append("revision_suggestions must be a list")

    return (len(errors) == 0, errors)
