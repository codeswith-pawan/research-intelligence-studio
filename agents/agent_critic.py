"""
The Critic Agent (Modified Synthesizer)

STAGE 4: Synthesizes the review with literature-grounded assessment.

Adds:
- Research Trajectory section showing where the paper fits in the literature
- Novelty-adjusted scoring
- Integration of fact-checker results
- Literature context in the final review
"""

import json
from typing import List, Dict, Any, Tuple, Optional
from pydantic import ValidationError

from core.data_models import (
    Paper,
    Review,
    GroundedReview,
    WeightedBreakdown,
    NoveltyRankedExtraction,
    BaselineReference,
    FactCheckResult,
    LiteratureContext,
    DetailedAssessment
)
from core.config_loader import Config
from core.llm_wrapper import call_llm
from core.json_response import parse_json_object
from core.synthesis_contract import evaluate_section_statuses
from utilities.helpers import get_weights_table, calculate_novelty_adjusted_score
from agents.evidence_grounding import (
    EVIDENCE_GROUNDING_INSTRUCTIONS,
    NO_GROUNDED_CRITERION,
    build_grounded_extraction_context,
    format_grounded_evidence_summary,
    sanitize_grounded_text,
    sanitize_synthesis_payload,
)


def _repair_schema_mismatch(
    review_data: Dict[str, Any],
    base_score: float,
    extractions: List[NoveltyRankedExtraction],
    recommendation: str,
    rationale: str
) -> Dict[str, Any]:
    """
    Repair schema mismatches when LLM doesn't return expected JSON structure.

    Handles common issues like:
    - 'summary' instead of 'executive_summary'
    - 'strengths'/'weaknesses' as separate arrays instead of nested in 'detailed_assessment'
    - Missing required fields

    Args:
        review_data: Raw JSON from LLM
        base_score: Calculated base score
        extractions: Novelty-ranked extractions
        recommendation: Calculated recommendation
        rationale: Recommendation rationale

    Returns:
        Repaired review data matching expected schema
    """
    import re

    repaired = {}

    # Map 'summary' or 'review_summary' to 'executive_summary'
    if 'executive_summary' not in review_data:
        if 'summary' in review_data:
            repaired['executive_summary'] = review_data['summary']
            print(f"[Critic Schema Repair] Mapped 'summary' → 'executive_summary'")
        elif 'review_summary' in review_data:
            repaired['executive_summary'] = review_data['review_summary']
            print(f"[Critic Schema Repair] Mapped 'review_summary' → 'executive_summary'")
        else:
            repaired['executive_summary'] = f"This paper was evaluated across {len(extractions)} criteria with an overall score of {base_score:.1f}/100. {rationale}"
            print(f"[Critic Schema Repair] Using fallback executive_summary")
        print(f"[Critic Schema Repair] executive_summary length: {len(repaired['executive_summary'])}")

    # Handle detailed_assessment - may come as 'strengths'/'weaknesses' or nested
    if 'detailed_assessment' not in review_data or not isinstance(review_data.get('detailed_assessment'), dict):
        strengths = []
        concerns = []
        minor_issues = []

        # Extract from separate arrays if available
        if 'strengths' in review_data and isinstance(review_data['strengths'], list):
            strengths = review_data['strengths'][:4]
            print(f"[Critic Schema Repair] Extracted {len(strengths)} strengths from 'strengths' array")

        if 'weaknesses' in review_data and isinstance(review_data['weaknesses'], list):
            weaknesses = review_data['weaknesses']
            # Split into major concerns (first 3) and minor issues (rest)
            concerns = weaknesses[:3]
            minor_issues = weaknesses[3:6]
            print(f"[Critic Schema Repair] Extracted {len(concerns)} concerns and {len(minor_issues)} from 'weaknesses' array")

        # If still empty, populate from extractions
        if not strengths:
            for e in extractions:
                strengths.extend(e.strengths[:1])
            strengths = strengths[:4]
        if not concerns:
            for e in extractions:
                if e.score < 70:
                    concerns.extend(e.weaknesses[:1])
            concerns = concerns[:3]

        repaired['detailed_assessment'] = {
            'major_strengths': strengths,
            'major_concerns': concerns,
            'minor_issues': minor_issues
        }
        print(f"[Critic Schema Repair] Created 'detailed_assessment' from separate arrays")

    # Map 'recommendation' - may be full sentence, extract the decision
    if 'recommendation' in review_data:
        raw_rec = review_data['recommendation']
        # Try to extract standard decision from text
        decision_keywords = {
            'Accept': ['accept', 'accepted', 'publish'],
            'Accept with Revisions': ['accept with revisions', 'accept with minor revisions', 'minor revisions'],
            'Revise and Resubmit': ['revise and resubmit', 'resubmit', 'major revisions'],
            'Reject': ['reject', 'rejected', 'not suitable']
        }

        found_decision = None
        for decision, keywords in decision_keywords.items():
            if any(kw in raw_rec.lower() for kw in keywords):
                found_decision = decision
                break

        if found_decision:
            repaired['recommendation'] = found_decision
            print(f"[Critic Schema Repair] Extracted decision '{found_decision}' from recommendation text")
        else:
            # Use calculated recommendation
            repaired['recommendation'] = recommendation
            repaired['recommendation_rationale'] = raw_rec  # Use full text as rationale
            print(f"[Critic Schema Repair] Using calculated recommendation, stored LLM text as rationale")
    else:
        repaired['recommendation'] = recommendation

    # recommendation_rationale - may be separate or derived
    if 'recommendation_rationale' not in repaired and 'recommendation_rationale' not in review_data:
        repaired['recommendation_rationale'] = rationale
        print(f"[Critic Schema Repair] Added recommendation_rationale from calculated rationale")

    # revision_suggestions - extract from 'verification_notes' or derive from weaknesses
    if 'revision_suggestions' not in review_data or not isinstance(review_data.get('revision_suggestions'), list):
        suggestions = []

        if 'verification_notes' in review_data:
            # Parse suggestions from verification notes
            notes = review_data['verification_notes']
            if 'contingent on:' in notes.lower():
                parts = re.split(r'\d+\)', notes)
                for part in parts[1:4]:  # First 3 suggestions
                    part = part.strip()
                    if part and len(part) > 10:
                        suggestions.append(part[:100])  # Truncate if too long

        if not suggestions:
            # Derive from low-scoring criteria
            for e in extractions:
                if e.score < 70 and e.weaknesses:
                    suggestions.append(f"Improve {e.criterion_id}: {e.weaknesses[0][:80]}")
                if len(suggestions) >= 5:
                    break

        repaired['revision_suggestions'] = suggestions[:5]
        print(f"[Critic Schema Repair] Generated {len(suggestions)} revision suggestions")

    # decision_confidence - derive from extraction confidence
    if 'decision_confidence' not in review_data or not isinstance(review_data.get('decision_confidence'), (int, float)):
        avg_confidence = sum(e.confidence for e in extractions) / len(extractions) if extractions else 0.7
        repaired['decision_confidence'] = avg_confidence
        print(f"[Critic Schema Repair] Set decision_confidence to {avg_confidence:.2f}")

    # criterion_narrative - build from extractions if not provided
    if 'criterion_narrative' not in review_data or not isinstance(review_data.get('criterion_narrative'), dict):
        # Build criterion_narrative from extractions
        criterion_narrative = {}
        for e in extractions:
            narrative = f"**Score: {e.score}/100**\n\n{e.score_justification}\n\n"
            if e.strengths:
                narrative += "**Strengths:**\n" + "\n".join(f"- {s}" for s in e.strengths[:3]) + "\n\n"
            if e.weaknesses:
                narrative += "**Weaknesses:**\n" + "\n".join(f"- {w}" for w in e.weaknesses[:3]) + "\n\n"
            criterion_narrative[e.criterion_id] = narrative
        repaired['criterion_narrative'] = criterion_narrative
        print(f"[Critic Schema Repair] Built criterion_narrative from {len(extractions)} extractions")

    # Copy over any fields that are already correct
    for key, value in review_data.items():
        if key not in repaired:
            repaired[key] = value

    return repaired


def _calculate_novelty_adjusted_score(
    base_score: float,
    extractions: List[NoveltyRankedExtraction],
    config: Config
) -> float:
    """
    Adjust the overall score based on novelty rankings.

    Delegates to the shared calculate_novelty_adjusted_score from helpers,
    using novelty config from the Config object if available.

    Args:
        base_score: The base calculated score
        extractions: List of novelty-ranked extractions
        config: System configuration

    Returns:
        Novelty-adjusted score (can be higher or lower)
    """
    # Get novelty adjustment factors from config if available
    try:
        novelty_config = config.get_novelty_config()
        base_factor = novelty_config.get("base_factor", 0.025)
        contradiction_penalty = novelty_config.get("contradiction_penalty", 0.05)
        extension_bonus = novelty_config.get("extension_bonus", 0.03)
    except (AttributeError, Exception):
        base_factor = 0.025
        contradiction_penalty = 0.05
        extension_bonus = 0.03

    return calculate_novelty_adjusted_score(
        base_score=base_score,
        extractions=extractions,
        base_factor=base_factor,
        contradiction_penalty=contradiction_penalty,
        extension_bonus=extension_bonus
    )


def _generate_research_trajectory(
    paper: Paper,
    baseline: BaselineReference,
    extractions: List[NoveltyRankedExtraction],
    fact_checks: List[FactCheckResult],
    config: Config
) -> str:
    """
    Generate the Research Trajectory section for the review.

    Args:
        paper: The target paper
        baseline: Baseline reference
        extractions: Novelty-ranked extractions
        fact_checks: Fact-check results
        config: System configuration

    Returns:
        Formatted research trajectory section
    """
    # Build summary of novelty findings
    novelty_summary = "### Novelty Assessment by Criterion\n\n"

    for extraction in extractions:
        novelty_labels = {
            1: "No novelty - replicates prior work",
            2: "Marginal novelty - minor extension",
            3: "Moderate novelty - builds on prior work",
            4: "High novelty - significant advance",
            5: "Exceptional novelty - groundbreaking"
        }

        novelty_summary += f"**{extraction.criterion_id}**: {novelty_labels.get(extraction.novelty_ranking, 'Unknown')}\n"

        if extraction.contradicts_baseline:
            novelty_summary += f" - ⚠️ Note: Contradicts established findings\n"

        if extraction.extends_baseline and extraction.prior_work_gaps:
            novelty_summary += f" - ✓ Addresses gaps: {', '.join(extraction.prior_work_gaps[:2])}\n"

        novelty_summary += "\n"

    # Build fact-check summary if available
    fact_check_summary = ""
    if fact_checks:
        disputed = [fc for fc in fact_checks if fc.verification_status == "disputed"]
        if disputed:
            fact_check_summary = "### ⚠️ Claims Requiring Further Review\n\n"
            for fc in disputed[:3]:  # Top 3
                fact_check_summary += f"**{fc.criterion_id}**: {fc.claim[:80]}...\n"
                fact_check_summary += f"- {fc.recommendation}\n\n"

    # Generate the trajectory using LLM
    grounded_context, allowed_evidence_ids = build_grounded_extraction_context(extractions)
    prompt = f"""
Based on the following information, write a "Research Trajectory and Position" section
for an academic review.

TARGET PAPER:
Title: {paper.metadata.title}
Abstract: {paper.metadata.abstract}

BASELINE REFERENCE:
Sub-topic: {baseline.sub_topic}
State of the Art: {baseline.key_findings_summary}

Key Baseline Papers:
{json.dumps([{"title": p.title, "year": p.year, "citations": p.citation_count, "findings": p.key_findings}
              for p in baseline.baseline_papers[:3]], indent=2)}

NOVELTY ASSESSMENT:
{novelty_summary}

{fact_check_summary}

SOURCE-VERIFIED TARGET-PAPER QUOTATIONS:
{json.dumps(grounded_context, indent=2, ensure_ascii=False)}

TASK:
Write a concise (200-300 words) "Research Trajectory and Position" section that:

1. **Position in Research Landscape**: Does this paper extend, challenge, or pivot
   from the existing line of research? Use phrases like "builds on", "extends",
   "challenges", "offers a new perspective on", etc.

2. **Key Contributions**: What specific questions does this paper answer that
   baseline papers do not? Be specific.

3. **Novelty Summary**: Overall assessment of the paper's novelty based on the
   criterion-by-criterion analysis above.

4. **Verification Notes** (if applicable): Note any claims that require further
   verification based on fact-checking.

Write in clear, academic prose suitable for inclusion in a peer review. Cite every factual
sentence about the target paper with a supplied verified evidence ID immediately before its
final punctuation, in the form [Evidence: criterion_id:Q1]. Do not infer missing content from
the extracted text or use unverified quotation text. Omit claims without a verified citation.
"""

    response = call_llm(
        prompt=prompt,
        system_prompt=(
            "You are an expert academic reviewer skilled at positioning research within the broader literature.\n\n"
            + EVIDENCE_GROUNDING_INSTRUCTIONS
        ),
        provider=config.get_llm_config()['synthesizer_provider'],
        model=config.get_llm_config()['synthesizer_model'],
        temperature=config.get_agent_config()['critic_temperature'],
        max_retries=2,
        role="synthesis"
    )

    if response['success']:
        grounded_text, _ = sanitize_grounded_text(
            response.get("content", ""),
            allowed_evidence_ids,
            fallback=(
                "No source-grounded research-trajectory statement is available; "
                "verify the target paper and baseline sources manually."
            ),
        )
        return grounded_text

    return (
        "No source-grounded research-trajectory statement is available; "
        "verify the target paper and baseline sources manually."
    )


def _create_fallback_review(
    paper: Paper,
    extractions: List[NoveltyRankedExtraction],
    config: Config,
    base_score: float,
    breakdown: Dict[str, WeightedBreakdown],
    recommendation: str,
    rationale: str,
    novelty_adjusted_score: Optional[float] = None,
    literature_context: LiteratureContext = None,
    research_trajectory: str = "",
    synthesis_error: str = "Synthesis did not pass schema validation.",
) -> GroundedReview:
    """Return a clearly marked diagnostic record without inventing review prose."""
    from agents.agent_synthesizer import _coverage

    llm_config = config.get_llm_config()
    final_score = novelty_adjusted_score if novelty_adjusted_score is not None else base_score
    coverage = _coverage(extractions, config, synthesis_valid=False)
    coverage["warnings"].append(synthesis_error)
    extractor_model = (
        extractions[0].model_used if extractions else
        f"{llm_config.get('extractor_provider', 'unknown')}/{llm_config.get('extractor_model', 'unknown')}"
    )
    synthesizer_model = (
        f"{llm_config.get('synthesizer_provider', 'unknown')}/"
        f"{llm_config.get('synthesizer_model', 'unknown')}"
    )
    fallback_assessment = DetailedAssessment(
        major_strengths=[],
        major_concerns=[],
        minor_issues=[],
    )
    section_statuses, section_diagnostics = evaluate_section_statuses(
        summary_text=None,
        major_strengths=[],
        major_concerns=[],
        minor_issues=[],
        suggestions=[],
        criterion_narratives={},
        extractions=extractions,
        synthesis_error=synthesis_error,
        omitted_breakdown=None,
    )
    return GroundedReview(
        paper_id=paper.id,
        paper_title=paper.metadata.title or "No Title Found",
        paper_filename=paper.filename,
        overall_score=final_score,
        weighted_breakdown=breakdown,
        recommendation="Needs Manual Review",
        provisional_recommendation=recommendation,
        recommendation_rationale=(
            f"The diagnostic weighted score is {final_score:.1f}/100. "
            "A final system recommendation is withheld because literature synthesis failed validation."
        ),
        executive_summary=(
            "No validated literature-grounded synthesis is available. The displayed score is diagnostic; "
            "review criterion results manually."
        ),
        detailed_assessment=fallback_assessment,
        criterion_narrative={},
        revision_suggestions=[],
        decision_confidence=0.0,
        synthesizer_model_used=synthesizer_model,
        extractor_model_used=extractor_model,
        total_cost=sum(item.cost for item in extractions),
        literature_context=literature_context or LiteratureContext(),
        research_trajectory_section="",
        base_score=base_score,
        novelty_adjusted_score=novelty_adjusted_score,
        llm_fallback_used=True,
        review_status="failed",
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


def synthesize_grounded_review(
    paper: Paper,
    extractions: List[NoveltyRankedExtraction],
    config: Config,
    baseline: Optional[BaselineReference] = None,
    fact_checks: List[FactCheckResult] = None
) -> Optional[GroundedReview]:
    """
    Critic Agent: Synthesize review with literature-grounded assessment.

    Args:
        paper: The target paper
        extractions: Novelty-ranked extractions
        config: System configuration
        baseline: Optional baseline reference
        fact_checks: Optional fact-check results

    Returns:
        GroundedReview with literature context and research trajectory
    """
    print(f"[Critic] Synthesizing grounded review for: {paper.filename}")

    llm_config = config.get_llm_config()
    if not extractions:
        print(f"[Critic Error] No extractions passed validation for {paper.filename}; saving a diagnostic record.")
        _, rationale, base_score, breakdown = calculate_recommendation(extractions, config)
        return _create_fallback_review(
            paper=paper,
            extractions=extractions,
            config=config,
            base_score=base_score,
            breakdown=breakdown,
            recommendation="Needs Manual Review",
            rationale=rationale,
            literature_context=LiteratureContext(),
            synthesis_error="No criterion extraction passed validation.",
        )

    # Use critic-specific prompts if available, otherwise fall back to synthesizer
    try:
        prompt_template = config.get_prompt("critic_user")
        system_prompt = config.get_prompt("critic_system")
    except:
        prompt_template = config.get_prompt("synthesizer_user")
        system_prompt = config.get_prompt("synthesizer_system")

    # Calculate recommendation and score
    recommendation, rationale, base_score, breakdown = calculate_recommendation(extractions, config)

    # Calculate novelty-adjusted score if baseline is available
    novelty_adjusted_score = None
    if baseline:
        novelty_adjusted_score = _calculate_novelty_adjusted_score(base_score, extractions, config)
        print(f"[Critic] Base score: {base_score:.1f}, Novelty-adjusted: {novelty_adjusted_score:.1f}")

    # Generate research trajectory section if literature context is available
    research_trajectory = ""
    literature_context = LiteratureContext()

    if baseline:
        research_trajectory = _generate_research_trajectory(
            paper=paper,
            baseline=baseline,
            extractions=extractions,
            fact_checks=fact_checks or [],
            config=config
        )

        literature_context = LiteratureContext(
            baseline_reference=baseline,
            fact_checks=fact_checks or [],
            total_api_calls=baseline.total_api_calls
        )

    # Synthesis gets verified source quotations and criterion scores only. In particular,
    # unverified quote text and extractor-generated criticism are not passed through.
    grounded_context, allowed_evidence_ids = build_grounded_extraction_context(extractions)
    grounded_by_id = {item["criterion_id"]: item for item in grounded_context}
    extractions_list = []
    for extraction in extractions:
        item = dict(grounded_by_id[extraction.criterion_id])
        novelty_ranking = getattr(extraction, "novelty_ranking", None)
        if novelty_ranking is not None:
            item["novelty_ranking"] = novelty_ranking
        extractions_list.append(item)
    extractions_json = json.dumps(extractions_list, indent=2)
    weights_table = get_weights_table(config.get_criteria())

    # Build prompt with optional literature context
    literature_section = ""
    if baseline and research_trajectory:
        literature_section = f"""

## LITERATURE CONTEXT (For Your Reference)
{research_trajectory}

When writing your review, consider how the paper's claims relate to this literature context.
"""

    prompt = prompt_template.format(
        paper_title=paper.metadata.title,
        paper_abstract=paper.metadata.abstract,
        json_dump_of_extractions=extractions_json,
        evidence_summary=format_grounded_evidence_summary(extractions),
        weights_table=weights_table,
        calculated_score=f"{novelty_adjusted_score if novelty_adjusted_score is not None else base_score:.1f}",
        calculated_recommendation=f"{recommendation} (Rationale: {rationale})"
    )

    # Add literature context to prompt if available
    if literature_section:
        prompt += literature_section

    # Prepare system prompt with JSON mode requirement for OpenAI
    actual_system_prompt = system_prompt
    if llm_config['synthesizer_provider'].lower() in ['openai', 'custom_openai']:
        # OpenAI requires "json" in messages when using JSON mode
        actual_system_prompt = "Respond ONLY with valid JSON. Your output must be JSON-formatted.\n\n" + system_prompt
    actual_system_prompt += "\n\n" + EVIDENCE_GROUNDING_INSTRUCTIONS

    # Strictly validate the synthesis schema; missing fields produce a diagnostic report.
    from agents.agent_synthesizer import _SynthesisPayload

    max_attempts = max(1, int(config.get_agent_config().get("critic_max_json_retries", 1)))
    payload = None
    response_cost = 0.0
    schema_error = "Synthesis did not pass schema validation."
    for json_attempt in range(max_attempts):
        response = call_llm(
            prompt=prompt,
            system_prompt=actual_system_prompt,
            provider=llm_config["synthesizer_provider"],
            model=llm_config["synthesizer_model"],
            temperature=llm_config["temperature"],
            max_retries=llm_config["max_retries"],
            response_format="json",
            role="synthesis",
        )
        if not response.get("success"):
            schema_error = "Configured synthesis provider request failed."
            break
        try:
            candidate = parse_json_object(response.get("content"))
            # Rescue fields that small models sometimes nest inside criterion_narrative.
            from agents.agent_synthesizer import _rescue_misplaced_fields
            candidate = _rescue_misplaced_fields(candidate)

            criteria_with_verified = {
                e.criterion_id
                for e in extractions
                if any(item.verification_status == "Verified" for item in e.evidence)
            }
            criteria_without_verified = {
                e.criterion_id for e in extractions
            } - criteria_with_verified

            narratives = candidate.get("criterion_narrative")
            if isinstance(narratives, dict):
                for cid in criteria_without_verified:
                    if cid not in narratives or not str(narratives.get(cid, "")).strip():
                        narratives[cid] = NO_GROUNDED_CRITERION

            payload = _SynthesisPayload.model_validate(candidate)
            expected_ids = {item.criterion_id for item in extractions}
            actual_ids = set(payload.criterion_narrative)
            if actual_ids != expected_ids:
                missing = sorted(expected_ids - actual_ids)
                extra = sorted(actual_ids - expected_ids)
                raise ValueError(
                    f"Criterion narratives differ from extracted criteria; missing={missing}, extra={extra}."
                )
            if not payload.executive_summary.strip() or not payload.recommendation_rationale.strip():
                raise ValueError("Required synthesis text is empty.")
            for cid in criteria_with_verified:
                narr = payload.criterion_narrative.get(cid, "")
                if not narr or not narr.strip():
                    raise ValueError(f"A criterion narrative is empty for criterion with verified evidence: {cid}")
            payload, grounding_omissions, omitted_breakdown = sanitize_synthesis_payload(
                payload, allowed_evidence_ids, return_breakdown=True
            )
            response_cost = float(response.get("cost", 0.0) or 0.0)
            break
        except (ValidationError, ValueError) as exc:
            if isinstance(exc, ValidationError):
                issues = []
                for issue in exc.errors(include_input=False)[:8]:
                    location = ".".join(str(part) for part in issue.get("loc", ())) or "response"
                    issues.append(f"{location}: {issue.get('msg', 'invalid value')}")
                schema_error = "Synthesis schema validation failed: " + "; ".join(issues)
            else:
                schema_error = f"Synthesis response rejected: {str(exc)[:180]}"
            payload = None
            if json_attempt + 1 < max_attempts:
                prompt += "\n\nIMPORTANT: Return a complete JSON object matching every required field and criterion."
                continue
            break

    if payload is None:
        return _create_fallback_review(
            paper=paper,
            extractions=extractions,
            config=config,
            base_score=base_score,
            breakdown=breakdown,
            recommendation=recommendation,
            rationale=rationale,
            novelty_adjusted_score=novelty_adjusted_score,
            literature_context=literature_context,
            synthesis_error=schema_error,
        )

    review_data = payload.model_dump()
    total_cost = sum(item.cost for item in extractions) + response_cost

    extractor_model_name = extractions[0].model_used if extractions else "unknown_extractor"
    synthesizer_model_name = f"{llm_config['synthesizer_provider']}/{llm_config['synthesizer_model']}"

    # Preserve the novelty-adjusted score as a diagnostic and apply evidence safeguards.
    from agents.agent_synthesizer import _coverage

    final_score = novelty_adjusted_score if novelty_adjusted_score is not None else base_score
    coverage = _coverage(extractions, config, synthesis_valid=True)
    if grounding_omissions:
        coverage["warnings"].append(
            f"{grounding_omissions} synthesis statement(s) without valid verified-evidence citations "
            "were omitted. Check the full manuscript before relying on narrative findings."
        )
    thresholds = config.get_recommendation_thresholds()
    provisional_recommendation = thresholds[-1]["label"] if thresholds else "Reject"
    for threshold in thresholds:
        if final_score >= threshold["threshold"]:
            provisional_recommendation = threshold["label"]
            break
    review_status = coverage["status"]
    final_recommendation = (
        provisional_recommendation if review_status == "complete" else "Needs Manual Review"
    )
    if review_status == "complete":
        final_rationale = (
            f"The weighted score of {final_score:.1f}/100 maps to {provisional_recommendation} "
            "under the configured thresholds. The system output is advisory and requires editorial judgment."
        )
    else:
        final_rationale = (
            f"The diagnostic weighted score is {final_score:.1f}/100, which maps provisionally to "
            f"{provisional_recommendation}. A final recommendation is withheld because required criteria "
            "or verified evidence are insufficient."
        )

    section_statuses, section_diagnostics = evaluate_section_statuses(
        summary_text=payload.executive_summary,
        major_strengths=payload.detailed_assessment.major_strengths,
        major_concerns=payload.detailed_assessment.major_concerns,
        minor_issues=payload.detailed_assessment.minor_issues,
        suggestions=payload.revision_suggestions,
        criterion_narratives=payload.criterion_narrative,
        extractions=extractions,
        synthesis_error=None,
        omitted_breakdown=omitted_breakdown if "omitted_breakdown" in locals() else None,
    )

    return GroundedReview(
        paper_id=paper.id,
        paper_title=paper.metadata.title or "No Title Found",
        paper_filename=paper.filename,
        overall_score=final_score,
        recommendation=final_recommendation,
        provisional_recommendation=provisional_recommendation,
        recommendation_rationale=final_rationale,
        weighted_breakdown=breakdown,
        synthesizer_model_used=synthesizer_model_name,
        extractor_model_used=extractor_model_name,
        total_cost=total_cost,
        literature_context=literature_context,
        research_trajectory_section=research_trajectory,
        base_score=base_score,
        novelty_adjusted_score=novelty_adjusted_score,
        executive_summary=payload.executive_summary,
        detailed_assessment=DetailedAssessment(**payload.detailed_assessment.model_dump()),
        criterion_narrative=payload.criterion_narrative,
        revision_suggestions=payload.revision_suggestions,
        decision_confidence=coverage["confidence"],
        review_status=review_status,
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
        llm_fallback_used=False,
    )

# Re-export calculate_recommendation for backward compatibility
from agents.agent_synthesizer import calculate_recommendation


def synthesize_review(
    paper: Paper,
    extractions: List[NoveltyRankedExtraction],
    config: Config
) -> Optional[Review]:
    """
    Standard synthesis without literature grounding (backward compatible).

    Delegates to the original synthesizer for backward compatibility.
    """
    from agents.agent_synthesizer import synthesize_review as _synthesize_review_standard

    # Convert to standard extractions if needed
    standard_extractions = []
    for e in extractions:
        # Create standard Extraction from NoveltyRankedExtraction
        standard_extractions.append(Extraction(
            paper_id=e.paper_id,
            criterion_id=e.criterion_id,
            score=e.score,
            score_justification=e.score_justification,
            evidence=e.evidence,
            strengths=e.strengths,
            weaknesses=e.weaknesses,
            confidence=e.confidence,
            model_used=e.model_used,
            extraction_timestamp=e.extraction_timestamp,
            cost=e.cost
        ))

    return _synthesize_review_standard(paper, standard_extractions, config)
