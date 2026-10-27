import json
import math
from typing import List, Dict, Any, Tuple, Optional

from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError, field_validator

from core.data_models import Paper, Extraction, Review, WeightedBreakdown, DetailedAssessment, EvidenceItem
from core.config_loader import Config
from core.llm_wrapper import call_llm
from core.json_response import parse_json_object
from utilities.helpers import get_weights_table
from core.synthesis_contract import (
    evaluate_section_statuses,
    generate_deterministic_fallback_summary,
    STATUS_SYNTHESIS_ERROR,
    STATUS_NOT_IDENTIFIED,
)
from agents.evidence_grounding import (
    EVIDENCE_GROUNDING_INSTRUCTIONS,
    NO_GROUNDED_CRITERION,
    build_grounded_extraction_context,
    format_grounded_evidence_summary,
    sanitize_synthesis_payload,
)


MIN_VERIFIED_QUOTES_PER_CRITERION = 2


class _SynthesisAssessment(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    major_strengths: List[StrictStr]
    major_concerns: List[StrictStr]
    minor_issues: List[StrictStr]


class _SynthesisPayload(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    executive_summary: StrictStr
    detailed_assessment: _SynthesisAssessment
    criterion_narrative: Dict[StrictStr, StrictStr]
    recommendation: StrictStr
    recommendation_rationale: StrictStr
    revision_suggestions: List[StrictStr]
    decision_confidence: float

    @field_validator("decision_confidence", mode="before")
    @classmethod
    def validate_confidence(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("decision_confidence must be numeric")
        if not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
            raise ValueError("decision_confidence must be between 0 and 1")
        return float(value)


def calculate_recommendation(
    extractions: List[Extraction],
    config: Config
) -> Tuple[str, str, float, Dict[str, WeightedBreakdown]]:
    """Calculate the weighted diagnostic score using every configured criterion weight."""
    weighted_scores = {}
    total_score = 0.0

    for extraction in extractions:
        criterion = config.get_criterion_by_id(extraction.criterion_id)
        if not criterion:
            continue

        scale = criterion.get('scale', {})
        labels = scale.get('labels', {})
        score_range = scale.get('range', [])
        if isinstance(score_range, (list, tuple)) and len(score_range) == 2:
            max_score = float(score_range[1])
        else:
            try:
                max_score = float(max(int(value) for value in labels))
            except (TypeError, ValueError):
                max_score = 0.0

        if max_score <= 0:
            continue

        weight = float(criterion.get('weight', 0))
        weighted_score = (extraction.score / max_score) * weight
        total_score += weighted_score
        weighted_scores[extraction.criterion_id] = WeightedBreakdown(
            score=extraction.score,
            weight=weight,
            weighted_score=weighted_score,
        )

    total_weight = sum(float(criterion.get('weight', 0)) for criterion in config.get_criteria())
    final_score = (total_score / total_weight) * 100 if total_weight > 0 else 0.0

    thresholds = config.get_recommendation_thresholds()
    recommendation = thresholds[-1]['label'] if thresholds else "Reject"
    for item in thresholds:
        if final_score >= item['threshold']:
            recommendation = item['label']
            break

    return recommendation, f"Overall score: {final_score:.1f}", final_score, weighted_scores


def _coverage(extractions: List[Extraction], config: Config, synthesis_valid: bool):
    criteria = config.get_criteria()
    criterion_ids = [str(item.get("id")) for item in criteria if item.get("id")]
    extraction_by_id = {
        extraction.criterion_id: extraction
        for extraction in extractions
        if extraction.criterion_id in criterion_ids
    }
    failed_ids = [criterion_id for criterion_id in criterion_ids if criterion_id not in extraction_by_id]
    extraction_failures = dict(getattr(config, "extraction_failures", {}) or {})
    for criterion_id in failed_ids:
        extraction_failures.setdefault(criterion_id, "Extraction did not produce a validated result.")

    verified_by_criterion = {}
    verified_count = 0
    unverified_count = 0
    evidence_by_criterion = {}
    for criterion_id, extraction in extraction_by_id.items():
        evidence_by_criterion[criterion_id] = extraction.evidence
        verified = sum(
            1 for item in extraction.evidence
            if item.verification_status == "Verified"
        )
        unverified = len(extraction.evidence) - verified
        verified_by_criterion[criterion_id] = verified
        verified_count += verified
        unverified_count += unverified

    insufficient = [
        criterion_id for criterion_id in criterion_ids
        if verified_by_criterion.get(criterion_id, 0) < MIN_VERIFIED_QUOTES_PER_CRITERION
    ]
    total = len(criterion_ids)
    successful = len(extraction_by_id)
    criterion_coverage = successful / total if total else 0.0
    evidence_coverage = (
        sum(1 for count in verified_by_criterion.values() if count > 0) / total
        if total else 0.0
    )
    all_quotes = verified_count + unverified_count
    quote_integrity = verified_count / all_quotes if all_quotes else 0.0
    evidence_depth = (
        sum(min(verified_by_criterion.get(criterion_id, 0), MIN_VERIFIED_QUOTES_PER_CRITERION)
            for criterion_id in criterion_ids)
        / (total * MIN_VERIFIED_QUOTES_PER_CRITERION)
        if total else 0.0
    )
    model_confidence = (
        sum(extraction.confidence for extraction in extraction_by_id.values()) / successful
        if successful else 0.0
    )
    components = {
        "mean_extractor_confidence": model_confidence,
        "criterion_coverage": criterion_coverage,
        "verified_evidence_coverage": evidence_coverage,
        "quote_integrity": quote_integrity,
        "evidence_depth": evidence_depth,
        "synthesis_schema_valid": 1.0 if synthesis_valid else 0.0,
    }
    reliability = math.prod(components.values())
    reliability = max(0.0, min(1.0, reliability))

    warnings = []
    if total == 0:
        warnings.append("No criteria are configured; no editorial recommendation can be supported.")
    if failed_ids:
        warnings.append(
            f"{len(failed_ids)} of {total} configured criteria did not produce a schema-valid extraction."
        )
    if insufficient:
        warnings.append(
            f"Fewer than {MIN_VERIFIED_QUOTES_PER_CRITERION} verified quotations support: "
            + ", ".join(insufficient) + "."
        )
    if unverified_count:
        warnings.append(
            f"{unverified_count} quotation(s) did not match source text and are excluded from verified evidence."
        )
    if not synthesis_valid:
        warnings.append("Synthesis did not pass schema validation; no editorial narrative was accepted.")
    warnings.append("Reliability confidence is a heuristic coverage estimate, not a statistically calibrated probability.")
    warnings.append(
        "Exact quotation matching verifies text presence in the source manuscript but does not "
        "establish semantic relevance to the evaluated criteria; human review is required to confirm rubric alignment."
    )

    if not synthesis_valid:
        status = "failed"
    elif total == 0:
        status = "incomplete"
    elif failed_ids:
        status = "incomplete"
    elif insufficient or unverified_count:
        status = "manual_review"
    else:
        status = "complete"

    return {
        "criterion_ids": criterion_ids,
        "failed_ids": failed_ids,
        "errors": extraction_failures,
        "successful": successful,
        "total": total,
        "verified": verified_count,
        "unverified": unverified_count,
        "insufficient": insufficient,
        "evidence": evidence_by_criterion,
        "components": {key: round(value, 4) for key, value in components.items()},
        "confidence": round(reliability, 4),
        "warnings": warnings,
        "status": status,
    }


def _build_review(
    paper: Paper,
    extractions: List[Extraction],
    config: Config,
    response_cost: float = 0.0,
    synthesis_payload: Optional[_SynthesisPayload] = None,
    synthesis_error: Optional[str] = None,
    grounding_omissions: int = 0,
    omitted_breakdown: Optional[Dict[str, Any]] = None,
) -> Review:
    provisional, _, score, breakdown = calculate_recommendation(extractions, config)
    coverage = _coverage(extractions, config, synthesis_payload is not None)
    if grounding_omissions:
        coverage["warnings"].append(
            f"{grounding_omissions} synthesis statement(s) without valid verified-evidence citations "
            "were omitted. Check the full manuscript before relying on narrative findings."
        )
    status = coverage["status"]
    extractor_name = (
        extractions[0].model_used
        if extractions
        else f"{config.get_llm_config()['extractor_provider']}/{config.get_llm_config()['extractor_model']}"
    )
    synthesizer_name = (
        f"{config.get_llm_config()['synthesizer_provider']}/"
        f"{config.get_llm_config()['synthesizer_model']}"
    )
    total_cost = sum(item.cost for item in extractions) + response_cost

    if synthesis_payload is None:
        if not extractions:
            summary = (
                "No validated criterion extraction or synthesized narrative is available. The analysis failed."
            )
            rationale = (
                "Extraction failed for all criteria; no score or recommendation could be determined."
            )
            score = None
            provisional = None
            breakdown = {}
        else:
            fallback_details = generate_deterministic_fallback_summary(
                paper_title=paper.metadata.title or paper.filename,
                extractions=extractions,
                score=score,
                provisional_rec=provisional,
                status=STATUS_SYNTHESIS_ERROR if synthesis_error else STATUS_NOT_IDENTIFIED,
                reason=synthesis_error,
            )
            summary = (
                "No validated synthesized narrative is available. The weighted score is diagnostic; "
                f"review all criterion outputs manually. {fallback_details}"
            )
            rationale = (
                f"The diagnostic weighted score is {score:.1f}/100, which maps to {provisional} "
                "under configured thresholds. A reliable system recommendation is withheld because "
                "synthesis failed validation."
            )
        assessment = DetailedAssessment(
            major_strengths=[],
            major_concerns=[],
            minor_issues=[],
        )
        narratives = {}
        suggestions = []
        status = "failed"
        if synthesis_error:
            coverage["warnings"].append(synthesis_error)
        confidence = 0.0
    else:
        summary = synthesis_payload.executive_summary
        assessment = DetailedAssessment(**synthesis_payload.detailed_assessment.model_dump())
        narratives = synthesis_payload.criterion_narrative
        suggestions = synthesis_payload.revision_suggestions
        confidence = coverage["confidence"]
        if status == "complete":
            rationale = (
                f"The weighted score of {score:.1f}/100 maps to {provisional} under the configured "
                "criterion weights and recommendation thresholds. The system output is advisory and "
                "requires editorial judgment."
            )
        else:
            rationale = (
                f"The diagnostic weighted score is {score:.1f}/100, which maps provisionally to "
                f"{provisional}. A final system recommendation is withheld because required criteria "
                "or verified evidence are insufficient."
            )

    section_statuses, section_diagnostics = evaluate_section_statuses(
        summary_text=summary,
        major_strengths=assessment.major_strengths,
        major_concerns=assessment.major_concerns,
        minor_issues=assessment.minor_issues,
        suggestions=suggestions,
        criterion_narratives=narratives,
        extractions=extractions,
        synthesis_error=synthesis_error,
        omitted_breakdown=omitted_breakdown,
    )

    return Review(
        paper_id=paper.id,
        paper_title=paper.metadata.title or "No Title Found",
        paper_filename=paper.filename,
        overall_score=score,
        weighted_breakdown=breakdown,
        recommendation=provisional if status == "complete" else "Needs Manual Review",
        provisional_recommendation=provisional,
        recommendation_rationale=rationale,
        executive_summary=summary,
        detailed_assessment=assessment,
        criterion_narrative=narratives,
        revision_suggestions=suggestions,
        decision_confidence=confidence,
        extractor_model_used=extractor_name,
        synthesizer_model_used=synthesizer_name,
        total_cost=total_cost,
        review_status=status,
        section_statuses=section_statuses,
        section_diagnostics=section_diagnostics,
        total_criteria=coverage["total"],
        successful_criteria=coverage["successful"],
        failed_criteria=len(coverage["failed_ids"]),
        verified_evidence_count=coverage["verified"],
        unverified_evidence_count=coverage["unverified"],
        criteria_with_insufficient_evidence=coverage["insufficient"],
        criterion_errors=coverage["errors"],
        criterion_evidence=coverage["evidence"],
        confidence_method="Heuristic reliability estimate; not statistically calibrated",
        reliability_components=coverage["components"],
        warnings=coverage["warnings"],
    )


def _rescue_misplaced_fields(raw: dict) -> dict:
    """Rescue top-level synthesis fields that small models sometimes place inside criterion_narrative.

    llama3.2:3b (and similar small models) occasionally nest 'decision_confidence',
    'recommendation', or 'recommendation_rationale' as keys inside the
    'criterion_narrative' dict instead of at the JSON top level.  When that
    happens, Pydantic reports an error like:
        criterion_narrative.decision_confidence: Input should be a valid string
    because _SynthesisPayload expects criterion_narrative values to be strings.

    This function detects such misplacements and moves them to the top level
    *before* schema validation.  It never fabricates a value: if the field is
    already present at the top level with a valid type it is left untouched.
    Only numeric decision_confidence, string recommendation / recommendation_rationale
    are candidates; anything else is left in criterion_narrative for Pydantic to
    reject normally.
    """
    if not isinstance(raw, dict):
        return raw

    narratives = raw.get("criterion_narrative")
    if not isinstance(narratives, dict):
        return raw

    # Fields that belong at the top level and can be misplaced inside criterion_narrative
    _RESCUABLE: dict = {
        "decision_confidence": (int, float),
        "recommendation": str,
        "recommendation_rationale": str,
    }

    rescued = dict(raw)
    clean_narratives = {}
    for key, value in narratives.items():
        if key in _RESCUABLE and not isinstance(value, str):
            # Non-string values for these keys are almost certainly misplaced scalars.
            expected_types = _RESCUABLE[key]
            if isinstance(value, expected_types) and key not in rescued:
                rescued[key] = value
                print(
                    f"[SynthesisRescue] Moved misplaced '{key}' from criterion_narrative "
                    f"to top level (value={value!r})."
                )
            else:
                # Already exists at top level or wrong type — skip and let Pydantic decide.
                print(
                    f"[SynthesisRescue] Skipped misplaced '{key}' in criterion_narrative "
                    f"(top-level already set or type mismatch)."
                )
        elif key in _RESCUABLE and isinstance(value, str) and key not in rescued:
            # String value for a rescuable field — might be a legitimate criterion with
            # the same name, but more likely misplaced.  Only rescue if no top-level.
            rescued[key] = value
            print(
                f"[SynthesisRescue] Moved misplaced string '{key}' from "
                f"criterion_narrative to top level."
            )
        else:
            clean_narratives[key] = value

    rescued["criterion_narrative"] = clean_narratives

    # Also rescue revision_suggestions if nested inside detailed_assessment
    detailed = rescued.get("detailed_assessment")
    if isinstance(detailed, dict):
        detailed_copy = dict(detailed)
        if "revision_suggestions" in detailed_copy:
            rev_sug = detailed_copy.pop("revision_suggestions")
            if "revision_suggestions" not in rescued or not rescued["revision_suggestions"]:
                if isinstance(rev_sug, list):
                    rescued["revision_suggestions"] = rev_sug
                    print("[SynthesisRescue] Moved misplaced 'revision_suggestions' from detailed_assessment to top level.")
            rescued["detailed_assessment"] = detailed_copy

    return rescued


def synthesize_review(
    paper: Paper,
    extractions: List[Extraction],
    config: Config
) -> Optional[Review]:
    """Synthesize only a strictly validated response; otherwise return a diagnostic report."""
    print(f"[Agent 2] Synthesizing review for: {paper.filename}")
    if not extractions:
        return _build_review(
            paper, extractions, config,
            synthesis_error="No criterion extraction passed validation.",
        )

    llm_config = config.get_llm_config()
    prompt_template = config.get_prompt("synthesizer_user")
    system_prompt = (
        config.get_prompt("synthesizer_system")
        + "\n\n"
        + EVIDENCE_GROUNDING_INSTRUCTIONS
    )
    provisional, _, score, _ = calculate_recommendation(extractions, config)
    grounded_extractions, allowed_evidence_ids = build_grounded_extraction_context(extractions)
    prompt = prompt_template.format(
        paper_title=paper.metadata.title,
        paper_abstract=paper.metadata.abstract,
        json_dump_of_extractions=json.dumps(
            grounded_extractions,
            indent=2,
            ensure_ascii=False,
        ),
        evidence_summary=format_grounded_evidence_summary(extractions),
        weights_table=get_weights_table(config.get_criteria()),
        calculated_score=f"{score:.1f}",
        calculated_recommendation=f"{provisional} (provisional)",
    )

    response = call_llm(
        prompt=prompt,
        system_prompt=system_prompt,
        provider=llm_config['synthesizer_provider'],
        model=llm_config['synthesizer_model'],
        temperature=llm_config['temperature'],
        max_retries=llm_config['max_retries'],
        response_format="json",
        role="synthesis",
    )
    if not response.get("success"):
        err = response.get("error") or "provider request failed"
        return _build_review(
            paper, extractions, config,
            synthesis_error=f"Configured synthesis provider request failed: {err}",
        )

    grounding_omissions = 0
    try:
        raw_parsed = parse_json_object(response.get("content"))
        # Rescue fields that small models sometimes nest inside criterion_narrative.
        raw_parsed = _rescue_misplaced_fields(raw_parsed)

        # Identify criteria with and without verified evidence
        criteria_with_verified = {
            e.criterion_id
            for e in extractions
            if any(item.verification_status == "Verified" for item in e.evidence)
        }
        criteria_without_verified = {
            e.criterion_id for e in extractions
        } - criteria_with_verified

        # Ensure all criteria (with or without verified evidence) have safe, evidence-grounded
        # representations without crashing the entire synthesis when an explanation is empty.
        narratives = raw_parsed.get("criterion_narrative")
        if not isinstance(narratives, dict):
            narratives = {}
            raw_parsed["criterion_narrative"] = narratives

        for cid in criteria_without_verified:
            if cid not in narratives or not str(narratives.get(cid, "")).strip():
                narratives[cid] = NO_GROUNDED_CRITERION

        for cid in criteria_with_verified:
            if cid not in narratives or not str(narratives.get(cid, "")).strip():
                ext = next((e for e in extractions if e.criterion_id == cid), None)
                verified_items = [
                    (idx, item)
                    for idx, item in enumerate(ext.evidence, start=1)
                    if item.verification_status == "Verified"
                ] if ext else []
                if verified_items:
                    eid = f"{cid}:Q{verified_items[0][0]}"
                    narratives[cid] = (
                        f"Verified source evidence is recorded for {cid} [Evidence: {eid}]. "
                        f"Automated narrative explanation was omitted by the synthesis model; "
                        f"review the criterion score ({ext.score}/10) and source quotation directly."
                    )
                else:
                    narratives[cid] = NO_GROUNDED_CRITERION

        payload = _SynthesisPayload.model_validate(raw_parsed)
        if not payload.executive_summary.strip():
            raise ValueError("executive_summary must not be empty")
        if not payload.recommendation_rationale.strip():
            raise ValueError("recommendation_rationale must not be empty")

        # Fallback safeguard: ensure non-empty explanation for all criteria with verified evidence
        for cid in criteria_with_verified:
            narr = payload.criterion_narrative.get(cid, "")
            if not narr or not narr.strip():
                ext = next((e for e in extractions if e.criterion_id == cid), None)
                verified_items = [
                    (idx, item)
                    for idx, item in enumerate(ext.evidence, start=1)
                    if item.verification_status == "Verified"
                ] if ext else []
                if verified_items:
                    eid = f"{cid}:Q{verified_items[0][0]}"
                    payload.criterion_narrative[cid] = (
                        f"Verified source evidence is recorded for {cid} [Evidence: {eid}]. "
                        f"Automated narrative explanation was omitted by the synthesis model; "
                        f"review the criterion score ({ext.score}/10) and source quotation directly."
                    )
                else:
                    payload.criterion_narrative[cid] = NO_GROUNDED_CRITERION

        if any(
            not item.strip()
            for item in (
                payload.detailed_assessment.major_strengths
                + payload.detailed_assessment.major_concerns
                + payload.detailed_assessment.minor_issues
                + payload.revision_suggestions
            )
        ):
            raise ValueError("Synthesis lists must not contain empty items")
        expected_ids = {
            extraction.criterion_id for extraction in extractions
        }
        missing_narratives = sorted(expected_ids - set(payload.criterion_narrative))
        for cid in missing_narratives:
            if cid in criteria_with_verified:
                ext = next((e for e in extractions if e.criterion_id == cid), None)
                verified_items = [
                    (idx, item)
                    for idx, item in enumerate(ext.evidence, start=1)
                    if item.verification_status == "Verified"
                ] if ext else []
                eid = f"{cid}:Q{verified_items[0][0]}" if verified_items else f"{cid}:Q1"
                payload.criterion_narrative[cid] = (
                    f"Verified source evidence is recorded for {cid} [Evidence: {eid}]. "
                    f"Automated narrative explanation was omitted by the synthesis model; "
                    f"review the criterion score and source quotation directly."
                )
            else:
                payload.criterion_narrative[cid] = NO_GROUNDED_CRITERION
        payload, grounding_omissions, omitted_breakdown = sanitize_synthesis_payload(
            payload, allowed_evidence_ids, return_breakdown=True
        )
    except ValidationError as exc:
        issues = []
        for item in exc.errors(include_input=False)[:8]:
            location = ".".join(str(piece) for piece in item.get("loc", ())) or "response"
            issues.append(f"{location}: {item.get('msg', 'invalid value')}")
        error = "Synthesis schema validation failed: " + "; ".join(issues)
        return _build_review(paper, extractions, config, synthesis_error=error)
    except Exception as exc:
        error = f"Synthesis response rejected: {type(exc).__name__}: {str(exc)[:180]}"
        return _build_review(paper, extractions, config, synthesis_error=error)

    return _build_review(
        paper,
        extractions,
        config,
        response_cost=float(response.get("cost", 0.0) or 0.0),
        synthesis_payload=payload,
        grounding_omissions=grounding_omissions,
        omitted_breakdown=omitted_breakdown,
    )
