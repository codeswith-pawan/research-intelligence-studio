import os
import html
import pandas as pd
import pymupdf
from typing import List, Optional, Any, Union
from datetime import datetime
from core.data_models import Review, Paper, GroundedReview
from core.config_loader import Config
from utilities.helpers import sanitize_model_name
from agents.evidence_grounding import evidence_reference_id
from core.synthesis_contract import (
    SECTION_SUMMARY,
    SECTION_STRENGTHS,
    SECTION_CONCERNS,
    SECTION_MINOR_ISSUES,
    SECTION_SUGGESTIONS,
    STATUS_NOT_IDENTIFIED,
    get_section_diagnostic_message,
)


def _format_section_items_or_status(
    items: List[str],
    section_name: str,
    review: Review,
    ordered: bool = False
) -> str:
    """Format section items or return explicit truthful diagnostic status message."""
    if items:
        if ordered:
            return "".join(f"{i}. {item}\n" for i, item in enumerate(items, 1))
        return "".join(f"- {item}\n" for item in items)

    status = getattr(review, "section_statuses", {}).get(section_name, STATUS_NOT_IDENTIFIED)
    diag = getattr(review, "section_diagnostics", {}).get(
        section_name, get_section_diagnostic_message(section_name, status)
    )
    return f"*(Status: {status})* {diag}\n"

def save_review_markdown(review: Review, paper: Paper, config: Config, output_dir="outputs/reviews"):
    """
    Saves the final review as a detailed Markdown file
    using the new <paper>_<ext_model>_<syn_model>_<timestamp> format.
    """
    os.makedirs(output_dir, exist_ok=True)
    
    paper_base_name = os.path.splitext(paper.filename)[0]
    # Format timestamp for filename (needs to be Windows-compatible)
    timestamp = review.synthesis_timestamp.strftime('%Y%m%d_%H%M%S')
    
    ext_model_name = sanitize_model_name(review.extractor_model_used)
    syn_model_name = sanitize_model_name(review.synthesizer_model_used)
    
    filename = f"{paper_base_name}_{ext_model_name}_{syn_model_name}_{timestamp}.md"
    
    filepath = os.path.join(output_dir, filename)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(f"# Review: {review.paper_title}\n\n")
        f.write(f"**Paper ID:** `{paper.id}`\n")
        f.write(f"**Original Filename:** {paper.filename}\n")
        f.write(f"**Review Date:** {review.synthesis_timestamp.strftime('%Y-%m-%d %H:%M')}\n\n")
        
        f.write(f"## Overall Assessment\n\n")
        f.write(f"**Recommendation:** **{review.recommendation.upper()}**\n")
        if review.overall_score is not None:
            f.write(f"**Overall Score:** **{review.overall_score:.1f} / 100**\n")
        else:
            f.write(f"**Overall Score:** None (Analysis Failed)\n")
        f.write(f"**Rationale:** {review.recommendation_rationale}\n")
        f.write(f"**Confidence:** {review.decision_confidence:.0%}\n\n")
        
        f.write(f"## Executive Summary\n\n{review.executive_summary}\n\n")
        
        f.write(f"## Detailed Assessment\n\n")
        f.write("### Major Strengths\n")
        f.write(_format_section_items_or_status(
            review.detailed_assessment.major_strengths, SECTION_STRENGTHS, review
        ))
        f.write("\n### Major Concerns\n")
        f.write(_format_section_items_or_status(
            review.detailed_assessment.major_concerns, SECTION_CONCERNS, review
        ))
        f.write("\n### Minor Issues\n")
        f.write(_format_section_items_or_status(
            review.detailed_assessment.minor_issues, SECTION_MINOR_ISSUES, review
        ))

        f.write(f"\n## Revision Suggestions\n\n")
        f.write(_format_section_items_or_status(
            review.revision_suggestions, SECTION_SUGGESTIONS, review, ordered=True
        ))
            
        f.write(f"\n## Criterion-by-Criterion Analysis\n\n")
        for crit_id, narrative in review.criterion_narrative.items():
            criterion = config.get_criterion_by_id(crit_id)
            breakdown = review.weighted_breakdown.get(crit_id)
            if criterion and breakdown:
                f.write(f"### {criterion.get('name', crit_id)} (Weight: {breakdown.weight}%, Score: {breakdown.score})\n")
                f.write(f"{narrative}\n\n")
            
        f.write(f"## Metadata\n\n")
        f.write(f"- **Extractor Model:** {review.extractor_model_used}\n")
        f.write(f"- **Synthesizer Model:** {review.synthesizer_model_used}\n")
        f.write(f"- **Total API Cost:** ${review.total_cost:.4f}\n")
        
    print(f"[Output] Saved Markdown review to {filepath}")

def save_consolidated_csv(reviews: List[Review], output_dir="outputs/reports"):
    """
    Saves a CSV summary of all reviews.
    - Adds 'paper_filename' (human-readable)
    - Fixes the 'title' column bug
    - Removes 'paper_id' (meaningless to user)
    """
    os.makedirs(output_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filepath = os.path.join(output_dir, f"report_consolidated_{timestamp}.csv")
    
    report_data = []
    for review in reviews:
        
        paper_base_name = os.path.splitext(review.paper_filename)[0]
        
        title = review.paper_title
        if title.strip() == "No Title Found" or title.strip().startswith("Placeholder:"):
            title = paper_base_name

        data = {
            "paper_filename": paper_base_name,
            "title": title,
            "overall_score": round(review.overall_score, 1) if review.overall_score is not None else "",
            "recommendation": review.recommendation,
            "confidence": review.decision_confidence,
            "total_cost_usd": review.total_cost,
            "extractor_model_used": review.extractor_model_used,
            "synthesizer_model_used": review.synthesizer_model_used,
            "timestamp": review.synthesis_timestamp # Pass through the timestamp
        }
        
        for crit_id, breakdown in review.weighted_breakdown.items():
            data[f"{crit_id}_score"] = breakdown.score
        
        report_data.append(data)
        
    df = pd.DataFrame(report_data)
    
    all_cols = list(df.columns)
    core_cols = [
        "paper_filename", "title", "extractor_model_used", "synthesizer_model_used", 
        "overall_score", "recommendation", "confidence", "total_cost_usd", "timestamp"
    ]

    score_cols = [col for col in all_cols if col.endswith("_score") and col != "overall_score"]
    other_cols = [c for c in all_cols if c not in core_cols and c not in score_cols]
    
    final_cols = [c for c in (core_cols + score_cols + other_cols) if c in df.columns]
    
    df = df[final_cols]
    
    df.to_csv(filepath, index=False, encoding='utf-8')
    print(f"[Output] Saved consolidated CSV report to {filepath}")


# ============================================================================
# LITERATURE-GROUNDED REVIEW OUTPUT
# ============================================================================

def save_review_markdown(
    review: Review,
    arg2: Any = None,
    paper: Optional[Paper] = None,
    config: Optional[Config] = None,
    output_path: Optional[str] = None,
    include_literature_context: bool = False,
    _backward_compat_mode: bool = False
):
    """
    Save a review as Markdown.

    This function handles both standard reviews and grounded reviews.
    Supports both legacy and modern calling conventions for backward compatibility.

    Args:
        review: The review (Review or GroundedReview)
        arg2: Second argument (could be output_path (modern) or paper (legacy))
        paper: Optional paper object
        config: Optional config
        output_path: Optional output path (keyword-only for modern calls)
        include_literature_context: Whether to include literature context sections
        _backward_compat_mode: Internal flag for backward compatibility

    Legacy calling convention: save_review_markdown(review, paper, config, output_path)
    Modern calling convention: save_review_markdown(review, output_path, paper=paper, config=config)
    """
    # Handle backward compatibility - detect calling convention
    if output_path is None and arg2 is not None:
        # Legacy call: save_review_markdown(review, paper, config, output_dir)
        # arg2 is actually paper, paper is config, config is output_dir
        actual_paper = arg2 if isinstance(arg2, Paper) else None
        actual_config = paper if isinstance(paper, Config) else None
        actual_output_dir = config if isinstance(config, str) else None

        if actual_output_dir and actual_paper and actual_config:
            # Legacy call: construct full file path from directory using established naming convention
            paper_base_name = os.path.splitext(actual_paper.filename)[0]
            timestamp = review.synthesis_timestamp.strftime('%Y%m%d_%H%M%S')
            ext_model_name = sanitize_model_name(review.extractor_model_used)
            syn_model_name = sanitize_model_name(review.synthesizer_model_used)
            filename = f"{paper_base_name}_{ext_model_name}_{syn_model_name}_{timestamp}.md"
            output_path = os.path.join(actual_output_dir, filename)
            paper = actual_paper
            config = actual_config
        elif isinstance(arg2, str):
            # Modern call: save_review_markdown(review, output_path)
            output_path = arg2
        else:
            raise ValueError(f"Cannot determine calling convention. arg2 type: {type(arg2)}")
    elif arg2 is not None and isinstance(arg2, str):
        # Modern call with positional output_path
        output_path = arg2

    if output_path is None:
        raise ValueError("output_path must be provided")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        # Header
        f.write(f"# Review: {review.paper_title}\n\n")
        f.write(f"**Paper ID:** `{review.paper_id}`\n")
        f.write(f"**Filename:** {review.paper_filename}\n")
        f.write(f"**Review Date:** {review.synthesis_timestamp.strftime('%Y-%m-%d %H:%M')}\n\n")

        # Overall Assessment
        f.write(f"## Overall Assessment\n\n")
        f.write(f"**Review Status:** **{review.review_status.upper()}**\n")
        f.write(
            f"**Criteria:** {review.successful_criteria}/{review.total_criteria} successful; "
            f"{review.failed_criteria} failed\n"
        )
        f.write(f"**Verified Evidence:** {review.verified_evidence_count}\n")
        f.write(f"**Unverified Evidence:** {review.unverified_evidence_count}\n")
        f.write(f"**Final System Recommendation:** **{review.recommendation.upper()}**\n")
        f.write(f"**Recommendation:** **{review.recommendation.upper()}**\n")
        if review.provisional_recommendation:
            f.write(f"**Provisional Recommendation:** {review.provisional_recommendation}\n")
        if review.overall_score is not None:
            f.write(f"**Overall Score:** **{review.overall_score:.1f} / 100**\n")
        else:
            f.write(f"**Overall Score:** None (Analysis Failed)\n")
        f.write(f"**Rationale:** {review.recommendation_rationale}\n")
        f.write(
            f"**Heuristic Reliability Estimate:** {review.decision_confidence:.1%} "
            "(not statistically calibrated)\n\n"
        )
        f.write("This system output is advisory and does not replace an editor's decision.\n\n")
        if review.warnings:
            f.write("### Warnings\n\n")
            for warning in review.warnings:
                f.write(f"- {warning}\n")
            f.write("\n")
        if review.criterion_errors:
            f.write("### Criterion Extraction Errors\n\n")
            for criterion_id, error in review.criterion_errors.items():
                f.write(f"- **{criterion_id}:** {error}\n")
            f.write("\n")

        # Literature-Grounded Sections (if available and enabled)
        if include_literature_context and isinstance(review, GroundedReview):
            # Research Trajectory
            if review.research_trajectory_section:
                f.write(f"\n{review.research_trajectory_section}\n\n")

            # Novelty-Adjusted Score
            if review.novelty_adjusted_score is not None:
                base_score = (
                    review.base_score
                    if review.base_score is not None
                    else review.overall_score
                )
                adjustment = review.novelty_adjusted_score - base_score
                f.write(f"### Novelty Impact on Score\n\n")
                f.write(f"Base Score: {base_score:.1f}\n")
                f.write(f"Novelty Adjustment: {adjustment:+.1f}\n")
                f.write(f"**Novelty-Adjusted Score: {review.novelty_adjusted_score:.1f}**\n\n")

            # Fact-Check Summary
            if review.literature_context and review.literature_context.fact_checks:
                from agents.agent_fact_checker import summarize_fact_checks
                f.write("\n" + summarize_fact_checks(review.literature_context.fact_checks) + "\n")

        # Executive Summary
        f.write(f"## Executive Summary\n\n{review.executive_summary}\n\n")

        # Detailed Assessment
        f.write(f"## Detailed Assessment\n\n")
        f.write("### Major Strengths\n")
        f.write(_format_section_items_or_status(
            review.detailed_assessment.major_strengths, SECTION_STRENGTHS, review
        ))
        f.write("\n### Major Concerns\n")
        f.write(_format_section_items_or_status(
            review.detailed_assessment.major_concerns, SECTION_CONCERNS, review
        ))
        f.write("\n### Minor Issues\n")
        f.write(_format_section_items_or_status(
            review.detailed_assessment.minor_issues, SECTION_MINOR_ISSUES, review
        ))

        # Revision Suggestions
        f.write(f"\n## Revision Suggestions\n\n")
        f.write(_format_section_items_or_status(
            review.revision_suggestions, SECTION_SUGGESTIONS, review, ordered=True
        ))

        # Source-checked evidence
        f.write(f"\n## Evidence\n\n")
        if not review.criterion_evidence:
            f.write("No criterion evidence is available.\n\n")
        for criterion_id, evidence_items in review.criterion_evidence.items():
            f.write(f"### {criterion_id}\n\n")
            for evidence_index, item in enumerate(evidence_items, start=1):
                f.write(
                    f"- **Evidence ID:** `{evidence_reference_id(criterion_id, evidence_index)}`\n"
                )
                f.write(f"- **Verification:** {item.verification_status.upper()}\n")
                f.write(f"- **PDF reference:** {item.page_reference}\n")
                f.write("> " + item.quote.replace("\n", "\n> ") + "\n\n")
                f.write(f"  Relevance: {item.relevance}\n")
                if item.verification_note:
                    f.write(f"  Note: {item.verification_note}\n")
                f.write("\n")

        # Criterion-by-Criterion Analysis
        f.write(f"\n## Criterion-by-Criterion Analysis\n\n")
        for crit_id, narrative in review.criterion_narrative.items():
            breakdown = review.weighted_breakdown.get(crit_id)
            if breakdown:
                f.write(f"### {crit_id} (Weight: {breakdown.weight}%, Score: {breakdown.score})\n")
                f.write(f"{narrative}\n\n")

        # Metadata
        f.write(f"## Metadata\n\n")
        f.write(f"- **Extractor Model:** {review.extractor_model_used}\n")
        f.write(f"- **Synthesizer Model:** {review.synthesizer_model_used}\n")
        f.write(f"- **Total API Cost:** ${review.total_cost:.4f}\n")

        # Fallback notice if applicable
        if include_literature_context and isinstance(review, GroundedReview) and review.llm_fallback_used:
            f.write(f"\n---\n\n**⚠️ Note:** This review was generated using fallback mode due to LLM output formatting issues. The scores and assessment are based on the extracted evidence data rather than a full LLM-generated narrative.\n")

    print(f"[Output] Saved review to {output_path}")

    # Companion PDF export directly from validated review data
    if output_path.endswith(".md"):
        pdf_path = output_path[:-3] + ".pdf"
        try:
            save_review_pdf(
                review=review,
                output_path=pdf_path,
                paper=paper,
                config=config,
                include_literature_context=include_literature_context
            )
            print(f"[Output] Saved review PDF to {pdf_path}")
        except Exception as e:
            print(f"[Output] Warning: failed to generate PDF companion report: {e}")


def generate_review_pdf_html(
    review: Review,
    paper: Optional[Paper] = None,
    config: Optional[Config] = None,
    include_literature_context: bool = False
) -> str:
    """Generate publication-quality HTML formatted for PyMuPDF Story PDF rendering."""
    def h(val: Any) -> str:
        return html.escape(str(val) if val is not None else "")

    status = (review.review_status or "unknown").lower()
    if status == "complete":
        status_class = "badge-complete"
        status_text = "COMPLETE"
    elif status == "manual_review":
        status_class = "badge-manual"
        status_text = "MANUAL REVIEW"
    else:
        status_class = "badge-failed"
        status_text = "PROCESSING FAILURE" if status == "failed" else status.upper()

    score_val = f"{review.overall_score:.1f} / 100" if review.overall_score is not None else "None"
    score_sub = "Diagnostic Score" if status != "complete" else "Weighted Rubric Score"
    prov_rec = review.provisional_recommendation or "Revise and Resubmit"
    final_rec = review.recommendation or "Needs Manual Review"

    # Score breakdown table rows
    breakdown_rows = []
    if review.weighted_breakdown:
        for cid, wb in review.weighted_breakdown.items():
            breakdown_rows.append(
                f"<tr><td><strong>{h(cid)}</strong></td>"
                f"<td>{wb.weight:.1f}%</td>"
                f"<td>{wb.score} / 5</td>"
                f"<td>{wb.weighted_score:.1f}</td></tr>"
            )
    breakdown_table_html = "".join(breakdown_rows)

    # Detailed Assessment items
    def format_list_or_status(items: List[str], sec_name: str) -> str:
        if items:
            lis = "".join(f"<li style='margin-bottom: 2pt;'>{h(it)}</li>" for it in items)
            return f"<ul style='margin: 2pt 0 4pt 14pt; padding: 0;'>{lis}</ul>"
        status_val = getattr(review, "section_statuses", {}).get(sec_name, STATUS_NOT_IDENTIFIED)
        diag = getattr(review, "section_diagnostics", {}).get(
            sec_name, get_section_diagnostic_message(sec_name, status_val)
        )
        return f"<p style='color: #64748b; font-style: italic; margin: 2pt 0 4pt 0;'><em>(Status: {h(status_val)})</em> {h(diag)}</p>"

    strengths_html = format_list_or_status(review.detailed_assessment.major_strengths, SECTION_STRENGTHS)
    concerns_html = format_list_or_status(review.detailed_assessment.major_concerns, SECTION_CONCERNS)
    minor_html = format_list_or_status(review.detailed_assessment.minor_issues, SECTION_MINOR_ISSUES)
    suggestions_html = format_list_or_status(review.revision_suggestions, SECTION_SUGGESTIONS)

    # Criterion Narratives
    criterion_narratives_html = []
    for cid, narr in review.criterion_narrative.items():
        wb = review.weighted_breakdown.get(cid)
        meta_sub = f" (Weight: {wb.weight:.1f}%, Score: {wb.score}/5)" if wb else ""
        criterion_narratives_html.append(
            f"<div style='margin-bottom: 6pt;'>"
            f"<h3 style='margin-bottom: 2pt; color: #1e3a8a;'>{h(cid)}{meta_sub}</h3>"
            f"<p style='margin: 0; color: #334155;'>{h(narr)}</p>"
            f"</div>"
        )

    # Evidence items
    evidence_html = []
    if review.criterion_evidence:
        for cid, items in review.criterion_evidence.items():
            evidence_html.append(f"<h3 style='color: #1e3a8a; border-bottom: 0.5pt solid #cbd5e1; padding-bottom: 2pt; margin-top: 8pt;'>{h(cid)}</h3>")
            for idx, ev in enumerate(items, 1):
                eid = evidence_reference_id(cid, idx)
                vstatus = (ev.verification_status or "Unverified").upper()
                vclass = "badge-verified" if vstatus == "VERIFIED" else "badge-unverified"
                page_ref = ev.page_reference or "N/A"
                quote_text = ev.quote.replace("\n", " ").strip()
                rel_text = ev.relevance or "Criterion support"
                note_html = f"<div style='color: #991b1b; font-size: 7.5pt; margin-top: 2pt;'>Note: {h(ev.verification_note)}</div>" if ev.verification_note else ""

                evidence_html.append(
                    f"<div class='evidence-card'>"
                    f"<div style='margin-bottom: 2pt; font-size: 8pt;'>"
                    f"<strong><code>{h(eid)}</code></strong> · <span class='badge {vclass}'>{h(vstatus)}</span> · <span style='color: #475569;'>{h(page_ref)}</span>"
                    f"</div>"
                    f"<blockquote>“{h(quote_text)}”</blockquote>"
                    f"<div style='font-size: 8pt; color: #475569; margin-top: 2pt;'><strong>Relevance:</strong> {h(rel_text)}</div>"
                    f"{note_html}"
                    f"</div>"
                )
    else:
        evidence_html.append("<p style='color: #64748b; font-style: italic;'>No criterion evidence records available.</p>")

    # Warnings
    warnings_html = ""
    if review.warnings:
        warn_lis = "".join(f"<li style='margin-bottom: 2pt;'>{h(w)}</li>" for w in review.warnings)
        warnings_html = (
            f"<div class='warning-box'>"
            f"<div style='font-weight: 700; color: #92400e; margin-bottom: 2pt;'>Review Warnings & Safety Gate Flags:</div>"
            f"<ul style='margin: 0; padding-left: 12pt; color: #78350f;'>{warn_lis}</ul>"
            f"</div>"
        )

    # Literature grounded section (if available)
    lit_html = ""
    if include_literature_context and isinstance(review, GroundedReview):
        if review.research_trajectory_section:
            lit_html += f"<h2>Research Trajectory</h2><p>{h(review.research_trajectory_section)}</p>"
        if review.novelty_adjusted_score is not None:
            base_score = review.base_score if review.base_score is not None else review.overall_score
            adj = review.novelty_adjusted_score - (base_score or 0.0)
            lit_html += (
                f"<h2>Novelty Assessment</h2>"
                f"<p>Base Score: {base_score:.1f} · Novelty Adjustment: {adj:+.1f} · "
                f"<strong>Novelty-Adjusted Score: {review.novelty_adjusted_score:.1f}</strong></p>"
            )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  @page {{
    size: A4;
    margin: 36pt;
  }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: #1e293b;
    font-size: 8.5pt;
    line-height: 1.4;
  }}
  .header {{
    border-bottom: 2pt solid #2563eb;
    padding-bottom: 5pt;
    margin-bottom: 8pt;
  }}
  .brand {{
    font-size: 7.5pt;
    font-weight: 700;
    letter-spacing: 1.5pt;
    color: #2563eb;
    text-transform: uppercase;
  }}
  .report-title {{
    font-size: 15pt;
    font-weight: 800;
    color: #0f172a;
    margin: 2pt 0;
  }}
  .meta-bar {{
    font-size: 7.5pt;
    color: #64748b;
    margin-bottom: 6pt;
  }}
  table.kpi {{
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 8pt;
  }}
  table.kpi td {{
    border: 1pt solid #cbd5e1;
    padding: 5pt 7pt;
    background: #f8fafc;
    vertical-align: top;
  }}
  .kpi-val {{
    font-size: 13pt;
    font-weight: 800;
    color: #0f172a;
  }}
  .kpi-lbl {{
    font-size: 6.5pt;
    font-weight: 600;
    color: #64748b;
    text-transform: uppercase;
  }}
  .badge {{
    display: inline-block;
    padding: 1.5pt 4pt;
    border-radius: 2pt;
    font-weight: 700;
    font-size: 7pt;
    text-transform: uppercase;
  }}
  .badge-complete {{ background: #dcfce7; color: #166534; }}
  .badge-manual {{ background: #fef3c7; color: #92400e; }}
  .badge-failed {{ background: #fee2e2; color: #991b1b; }}
  .badge-verified {{ background: #dcfce7; color: #166534; }}
  .badge-unverified {{ background: #fee2e2; color: #991b1b; }}
  h2 {{
    font-size: 10pt;
    font-weight: 700;
    color: #1e3a8a;
    border-bottom: 1pt solid #e2e8f0;
    padding-bottom: 2pt;
    margin: 9pt 0 4pt 0;
  }}
  h3 {{
    font-size: 8.5pt;
    font-weight: 700;
    color: #334155;
    margin: 6pt 0 2pt 0;
  }}
  .rationale-card {{
    background: #eff6ff;
    border: 1pt solid #bfdbfe;
    padding: 5pt 7pt;
    border-radius: 3pt;
    margin-bottom: 8pt;
    font-size: 8pt;
  }}
  .warning-box {{
    background: #fffbeb;
    border: 1pt solid #fde68a;
    padding: 5pt 7pt;
    border-radius: 3pt;
    margin-bottom: 8pt;
    font-size: 8pt;
  }}
  blockquote {{
    border-left: 2.5pt solid #2563eb;
    margin: 3pt 0 4pt 6pt;
    padding: 3pt 6pt;
    background: #f8fafc;
    font-style: italic;
    color: #334155;
  }}
  table.data-table {{
    width: 100%;
    border-collapse: collapse;
    margin: 4pt 0 8pt 0;
    font-size: 8pt;
  }}
  table.data-table th, table.data-table td {{
    border: 1pt solid #cbd5e1;
    padding: 3pt 5pt;
    text-align: left;
  }}
  table.data-table th {{
    background: #f1f5f9;
    font-weight: 700;
    color: #1e293b;
  }}
  .evidence-card {{
    border: 0.5pt solid #e2e8f0;
    border-radius: 3pt;
    padding: 4pt 6pt;
    margin-bottom: 5pt;
    background: #ffffff;
  }}
  .disclaimer {{
    margin-top: 14pt;
    border-top: 1pt solid #cbd5e1;
    padding-top: 4pt;
    font-size: 6.5pt;
    color: #64748b;
    text-align: center;
  }}
</style>
</head>
<body>
  <div class="header">
    <div class="brand">Research Intelligence Studio · Academic Review Audit</div>
    <div class="report-title">{h(review.paper_title)}</div>
    <div class="meta-bar">
      Paper ID: <code>{h(review.paper_id)}</code> · Filename: {h(review.paper_filename)} ·
      Evaluation Date: {review.synthesis_timestamp.strftime('%Y-%m-%d %H:%M')}
    </div>
  </div>

  <table class="kpi">
    <tr>
      <td width="25%">
        <div class="kpi-lbl">{score_sub}</div>
        <div class="kpi-val">{score_val}</div>
      </td>
      <td width="25%">
        <div class="kpi-lbl">Review Status</div>
        <div style="margin-top: 2pt;"><span class="badge {status_class}">{status_text}</span></div>
      </td>
      <td width="25%">
        <div class="kpi-lbl">Provisional Rec</div>
        <div style="font-weight: 700; color: #1e3a8a; margin-top: 2pt;">{h(prov_rec)}</div>
      </td>
      <td width="25%">
        <div class="kpi-lbl">Final Recommendation</div>
        <div style="font-weight: 700; color: #92400e; margin-top: 2pt;">{h(final_rec)}</div>
      </td>
    </tr>
    <tr>
      <td>
        <div class="kpi-lbl">Criteria Coverage</div>
        <div style="font-weight: 600; margin-top: 1pt;">{review.successful_criteria} / {review.total_criteria} Successful</div>
      </td>
      <td>
        <div class="kpi-lbl">Evidence Quotations</div>
        <div style="font-weight: 600; margin-top: 1pt;">{review.verified_evidence_count} Verified · {review.unverified_evidence_count} Unverified</div>
      </td>
      <td>
        <div class="kpi-lbl">Reliability Confidence</div>
        <div style="font-weight: 600; margin-top: 1pt;">{review.decision_confidence:.1%} (Heuristic)</div>
      </td>
      <td>
        <div class="kpi-lbl">Evaluator Models</div>
        <div style="font-size: 7pt; color: #475569; margin-top: 1pt;">{h(review.extractor_model_used)}</div>
      </td>
    </tr>
  </table>

  <div class="rationale-card">
    <strong>Recommendation Rationale:</strong> {h(review.recommendation_rationale)}
    <div style="font-size: 7.5pt; color: #475569; margin-top: 2pt;">
      <em>Note: This system output is advisory and does not replace an editor's academic decision.</em>
    </div>
  </div>

  {warnings_html}

  <h2>Executive Summary</h2>
  <p>{h(review.executive_summary)}</p>

  {lit_html}

  <h2>Detailed Assessment</h2>
  <h3 style="color: #166534;">Major Strengths</h3>
  {strengths_html}

  <h3 style="color: #991b1b;">Major Concerns</h3>
  {concerns_html}

  <h3 style="color: #475569;">Minor Issues</h3>
  {minor_html}

  <h2>Revision Suggestions</h2>
  {suggestions_html}

  <h2>Criterion Score Breakdown</h2>
  <table class="data-table">
    <thead>
      <tr>
        <th>Criterion</th>
        <th>Weight</th>
        <th>Score</th>
        <th>Weighted Score</th>
      </tr>
    </thead>
    <tbody>
      {breakdown_table_html}
    </tbody>
  </table>

  <h2>Criterion-by-Criterion Deep Dive</h2>
  {"".join(criterion_narratives_html)}

  <h2>Source Evidence Audit Trail & Direct Quotations</h2>
  <p style="font-size: 7.5pt; color: #64748b; margin-top: 0;">
    Exact quotation matching verifies text presence in the source manuscript but does not establish semantic relevance to the evaluated criteria; human review is required to confirm rubric alignment.
  </p>
  {"".join(evidence_html)}

  <div class="disclaimer">
    Research Intelligence Studio v1.0.0 · Comprehensive Academic Evaluation Audit Report · Generated on {review.synthesis_timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}
  </div>
</body>
</html>"""


def save_review_pdf(
    review: Review,
    output_path: str,
    paper: Optional[Paper] = None,
    config: Optional[Config] = None,
    include_literature_context: bool = False
) -> str:
    """Generate and save a publication-quality PDF report directly from validated review data."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    html_content = generate_review_pdf_html(
        review=review,
        paper=paper,
        config=config,
        include_literature_context=include_literature_context
    )
    story = pymupdf.Story(html=html_content)
    writer = pymupdf.DocumentWriter(output_path)
    page_rect = pymupdf.Rect(0, 0, 595.3, 841.9)
    margin = 36.0
    content_rect = pymupdf.Rect(margin, margin, page_rect.width - margin, page_rect.height - margin)
    more = 1
    while more:
        device = writer.begin_page(page_rect)
        more, _ = story.place(content_rect)
        story.draw(device)
        writer.end_page()
    writer.close()
    return output_path


def save_consolidated_csv(
    reviews: List[Review],
    arg2: Any,
    include_literature_metrics: bool = False
):
    """
    Save a consolidated CSV of all reviews.

    Supports both legacy and modern calling conventions for backward compatibility.

    Args:
        reviews: List of reviews (Review or GroundedReview)
        arg2: output_path (modern) or output_dir (legacy)
        include_literature_metrics: Whether to include literature grounding columns

    Legacy calling convention: save_consolidated_csv(reviews, output_dir)
    Modern calling convention: save_consolidated_csv(reviews, output_path)
    """
    # Handle backward compatibility - detect if arg2 is a directory or file path
    output_path = arg2
    if isinstance(arg2, str) and os.path.isdir(arg2):
        # Legacy call: arg2 is a directory, construct file path
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_path = os.path.join(arg2, f"consolidated_reviews_{timestamp}.csv")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    report_data = []
    for review in reviews:
        paper_base_name = os.path.splitext(review.paper_filename)[0]
        title = review.paper_title
        if title.strip() == "No Title Found":
            title = paper_base_name

        data = {
            "paper_filename": paper_base_name,
            "title": title,
            "overall_score": round(review.overall_score, 1) if review.overall_score is not None else "",
            "recommendation": review.recommendation,
            "provisional_recommendation": review.provisional_recommendation or "",
            "review_status": review.review_status,
            "summary_status": getattr(review, "section_statuses", {}).get("executive_summary", ""),
            "strengths_status": getattr(review, "section_statuses", {}).get("major_strengths", ""),
            "concerns_status": getattr(review, "section_statuses", {}).get("major_concerns", ""),
            "suggestions_status": getattr(review, "section_statuses", {}).get("revision_suggestions", ""),
            "total_criteria": review.total_criteria,
            "successful_criteria": review.successful_criteria,
            "failed_criteria": review.failed_criteria,
            "verified_evidence_count": review.verified_evidence_count,
            "unverified_evidence_count": review.unverified_evidence_count,
            "confidence_method": review.confidence_method,
            "confidence": review.decision_confidence,
            "total_cost_usd": review.total_cost,
            "extractor_model_used": review.extractor_model_used,
            "synthesizer_model_used": review.synthesizer_model_used,
            "timestamp": review.synthesis_timestamp.strftime('%Y-%m-%d %H:%M:%S')
        }

        # Add criterion scores
        for crit_id, breakdown in review.weighted_breakdown.items():
            data[f"{crit_id}_score"] = breakdown.score

        # Add literature metrics if available
        if include_literature_metrics and isinstance(review, GroundedReview):
            if review.novelty_adjusted_score is not None:
                data["novelty_adjusted_score"] = review.novelty_adjusted_score
                base_score = (
                    review.base_score
                    if review.base_score is not None
                    else review.overall_score
                )
                data["novelty_adjustment"] = review.novelty_adjusted_score - base_score

            if review.literature_context:
                ctx = review.literature_context
                data["literature_api_calls"] = ctx.total_api_calls
                data["fact_checks_performed"] = len(ctx.fact_checks)

                disputed = sum(1 for fc in ctx.fact_checks if fc.verification_status == "disputed")
                data["fact_checks_disputed"] = disputed

                if ctx.baseline_reference:
                    data["baseline_papers_count"] = len(ctx.baseline_reference.baseline_papers)

        report_data.append(data)

    df = pd.DataFrame(report_data)

    # Organize columns
    all_cols = list(df.columns)
    core_cols = [
        "paper_filename", "title", "extractor_model_used", "synthesizer_model_used",
        "overall_score", "recommendation", "provisional_recommendation", "review_status",
        "summary_status", "strengths_status", "concerns_status", "suggestions_status",
        "confidence", "total_cost_usd", "timestamp"
    ]

    if include_literature_metrics:
        literature_cols = [
            "novelty_adjusted_score", "novelty_adjustment",
            "baseline_papers_count", "literature_api_calls",
            "fact_checks_performed", "fact_checks_disputed"
        ]
        core_cols.extend(literature_cols)

    score_cols = [col for col in all_cols if col.endswith("_score") and col != "overall_score"]
    other_cols = [c for c in all_cols if c not in core_cols and c not in score_cols]

    final_cols = [c for c in (core_cols + score_cols + other_cols) if c in df.columns]
    df = df[final_cols]

    df.to_csv(output_path, index=False, encoding='utf-8')
    print(f"[Output] Saved consolidated CSV to {output_path}")
