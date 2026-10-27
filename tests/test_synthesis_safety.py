import json

from agents.agent_synthesizer import synthesize_review
from core.data_models import EvidenceItem, Extraction, Paper, PaperMetadata


class FakeConfig:
    domain = "theoretical machine learning"
    extraction_failures = {}

    def __init__(self, criteria):
        self.criteria = criteria
        self.extraction_failures = {}

    def get_llm_config(self):
        return {
            "extractor_provider": "ollama",
            "extractor_model": "llama3.2:3b",
            "synthesizer_provider": "ollama",
            "synthesizer_model": "llama3.2:3b",
            "temperature": 0,
            "max_retries": 0,
        }

    def get_criteria(self):
        return self.criteria

    def get_criterion_by_id(self, criterion_id):
        return next((item for item in self.criteria if item["id"] == criterion_id), {})

    def get_recommendation_thresholds(self):
        return [
            {"threshold": 85, "label": "Accept"},
            {"threshold": 70, "label": "Accept with Revisions"},
            {"threshold": 50, "label": "Revise and Resubmit"},
            {"threshold": 0, "label": "Reject"},
        ]

    def get_prompt(self, name):
        if name == "synthesizer_user":
            return (
                "{paper_title} {paper_abstract} {json_dump_of_extractions} "
                "{weights_table} {calculated_score} {calculated_recommendation}"
            )
        return "Synthesize evidence for {domain}."


def make_paper():
    return Paper(
        filename="paper.pdf",
        original_path="/does/not/need/to/exist.pdf",
        content_markdown="Paper text.",
        token_count=2,
        metadata=PaperMetadata(title="A theory paper", abstract="A short abstract."),
    )


def make_criterion(criterion_id, weight):
    return {
        "id": criterion_id,
        "name": criterion_id,
        "description": "Assess the configured contribution.",
        "weight": weight,
        "scale": {"range": [1, 5], "labels": {str(i): str(i) for i in range(1, 6)}},
    }


def make_extraction(criterion_id, score=5, quote_count=2):
    return Extraction(
        paper_id="paper",
        criterion_id=criterion_id,
        score=score,
        score_justification="The extraction gives a criterion-specific explanation.",
        evidence=[
            EvidenceItem(
                quote=f"Source quote {index} for {criterion_id}.",
                page_reference=f"PDF Page {index}",
                relevance="Supports the criterion assessment.",
                verification_status="Verified",
                pdf_page_numbers=[index],
            )
            for index in range(1, quote_count + 1)
        ],
        strengths=[],
        weaknesses=[],
        confidence=0.8,
        model_used="ollama/llama3.2:3b",
    )


def synthesis_response(criterion_ids, **overrides):
    data = {
        "executive_summary": (
            "The cited passages support the criterion assessments "
            f"[Evidence: {', '.join(f'{criterion_id}:Q1' for criterion_id in criterion_ids)}]."
        ),
        "detailed_assessment": {
            "major_strengths": [
                f"The supplied passage supports this assessment [Evidence: {criterion_ids[0]}:Q1]."
            ],
            "major_concerns": [],
            "minor_issues": [],
        },
        "criterion_narrative": {
            criterion_id: f"The verified passage supports this criterion [Evidence: {criterion_id}:Q1]."
            for criterion_id in criterion_ids
        },
        # Deliberately conflicts with the configured score thresholds.
        "recommendation": "Reject",
        "recommendation_rationale": "The model-provided recommendation is ignored.",
        "revision_suggestions": [
            f"Clarify the point described in the passage [Evidence: {criterion_ids[0]}:Q1]."
        ],
        "decision_confidence": 0.99,
    }
    data.update(overrides)
    return json.dumps(data)


def patch_synthesis(monkeypatch, response):
    monkeypatch.setattr(
        "agents.agent_synthesizer.call_llm",
        lambda **kwargs: {"success": True, "content": response, "cost": 0.0},
    )


def test_complete_review_uses_configured_score_thresholds_and_heuristic_confidence(monkeypatch):
    config = FakeConfig([make_criterion("theory", 100)])
    extraction = make_extraction("theory", score=5, quote_count=2)
    patch_synthesis(monkeypatch, synthesis_response(["theory"]))

    review = synthesize_review(make_paper(), [extraction], config)

    assert review.review_status == "complete"
    assert review.overall_score == 100
    assert review.provisional_recommendation == "Accept"
    assert review.recommendation == "Accept"
    assert review.decision_confidence == 0.8
    assert review.confidence_method.endswith("not statistically calibrated")
    assert review.criterion_evidence["theory"][0].quote == "Source quote 1 for theory."


def test_missing_criteria_keep_diagnostic_score_but_withhold_final_recommendation(monkeypatch):
    config = FakeConfig([
        make_criterion("theory", 50),
        make_criterion("method", 50),
    ])
    config.extraction_failures = {"method": "score: invalid type"}
    extraction = make_extraction("theory", score=5, quote_count=2)
    patch_synthesis(monkeypatch, synthesis_response(["theory"]))

    review = synthesize_review(make_paper(), [extraction], config)

    assert review.overall_score == 50
    assert review.review_status == "incomplete"
    assert review.successful_criteria == 1
    assert review.failed_criteria == 1
    assert review.provisional_recommendation == "Revise and Resubmit"
    assert review.recommendation == "Needs Manual Review"
    assert review.decision_confidence == 0.1
    assert review.criterion_errors == {"method": "score: invalid type"}


def test_insufficient_verified_evidence_requires_manual_review(monkeypatch):
    config = FakeConfig([make_criterion("theory", 100)])
    extraction = make_extraction("theory", quote_count=1)
    patch_synthesis(monkeypatch, synthesis_response(["theory"]))

    review = synthesize_review(make_paper(), [extraction], config)

    assert review.review_status == "manual_review"
    assert review.criteria_with_insufficient_evidence == ["theory"]
    assert review.provisional_recommendation == "Accept"
    assert review.recommendation == "Needs Manual Review"
    assert review.decision_confidence == 0.4


def test_invalid_synthesis_schema_produces_explicit_diagnostic_without_fabricated_suggestions(monkeypatch):
    config = FakeConfig([make_criterion("theory", 100)])
    extraction = make_extraction("theory")
    invalid = synthesis_response(["theory"])
    invalid = json.loads(invalid)
    invalid.pop("revision_suggestions")
    patch_synthesis(monkeypatch, json.dumps(invalid))

    review = synthesize_review(make_paper(), [extraction], config)

    assert review.overall_score == 100
    assert review.review_status == "failed"
    assert review.recommendation == "Needs Manual Review"
    assert review.decision_confidence == 0
    assert review.revision_suggestions == []
    assert "No validated synthesized narrative" in review.executive_summary


def test_unsupported_and_unverified_synthesis_claims_are_omitted(monkeypatch):
    config = FakeConfig([make_criterion("theory", 100)])
    extraction = make_extraction("theory", quote_count=2)
    extraction.evidence[1] = EvidenceItem(
        quote="Unverified quotation claiming the paper has no experiments.",
        page_reference="N/A",
        relevance="Would support an unsupported criticism.",
        verification_status="Unverified",
        verification_note="No exact source match.",
    )
    response = synthesis_response(
        ["theory"],
        executive_summary=(
            "The paper lacks formal proofs [Evidence: theory:Q2]. "
            "The verified passage supports a limited assessment [Evidence: theory:Q1]."
        ),
        detailed_assessment={
            "major_strengths": ["The paper has strong results without a citation."],
            "major_concerns": ["The paper has no experiments [Evidence: theory:Q2]."],
            "minor_issues": ["There are figure formatting defects [Evidence: theory:Q9]."],
        },
        criterion_narrative={
            "theory": (
                "The paper lacks proofs [Evidence: theory:Q2]. "
                "The verified passage supports this criterion [Evidence: theory:Q1]."
            )
        },
        revision_suggestions=["Add experiments because none are present [Evidence: theory:Q2]."],
    )

    captured = {}

    def fake_call_llm(**kwargs):
        captured.update(kwargs)
        return {"success": True, "content": response, "cost": 0.0}

    monkeypatch.setattr("agents.agent_synthesizer.call_llm", fake_call_llm)
    review = synthesize_review(make_paper(), [extraction], config)

    assert "Unverified quotation claiming" not in captured["prompt"]
    assert "Unverified quotation claiming" not in captured["system_prompt"]
    assert "score_justification" not in captured["prompt"]
    assert '"evidence_id": "theory:Q1"' in captured["prompt"]
    assert "factual sentence" in captured["system_prompt"]
    assert "lacks formal proofs" not in review.executive_summary
    assert "no experiments" not in " ".join(review.detailed_assessment.major_concerns)
    assert review.detailed_assessment.major_strengths == []
    assert review.detailed_assessment.minor_issues == []
    assert review.revision_suggestions == []
    assert review.criterion_narrative["theory"] == (
        "The verified passage supports this criterion [Evidence: theory:Q1]."
    )
    assert review.verified_evidence_count == 1
    assert review.unverified_evidence_count == 1
    assert review.provisional_recommendation == "Accept"
    assert review.recommendation == "Needs Manual Review"
    assert any("synthesis statement(s)" in warning for warning in review.warnings)


def test_synthesis_cannot_cite_an_unverified_or_unknown_evidence_id(monkeypatch):
    config = FakeConfig([make_criterion("theory", 100)])
    extraction = make_extraction("theory", quote_count=3)
    extraction.evidence[1].verification_status = "Unverified"
    extraction.evidence[2].verification_status = "Unverified"
    response = synthesis_response(
        ["theory"],
        detailed_assessment={
            "major_strengths": [],
            "major_concerns": ["The paper is flawed [Evidence: theory:Q2]."],
            "minor_issues": [],
        },
        revision_suggestions=["Fix the flaw [Evidence: theory:Q99]."],
    )
    patch_synthesis(monkeypatch, response)

    review = synthesize_review(make_paper(), [extraction], config)

    assert review.detailed_assessment.major_concerns == []
    assert review.revision_suggestions == []
    assert review.review_status == "manual_review"
    assert review.recommendation == "Needs Manual Review"


def test_mixed_review_with_zero_evidence_criterion_empty_narrative_is_safe(monkeypatch):
    """When a criterion has zero verified evidence and model outputs an empty narrative for it,
    synthesis should succeed safely: other criteria retain grounded narrative, the zero-evidence
    criterion is set to NO_GROUNDED_CRITERION, and final status remains Needs Manual Review."""
    from agents.evidence_grounding import NO_GROUNDED_CRITERION

    config = FakeConfig([
        make_criterion("theory", 50),
        make_criterion("identification", 50),
    ])
    ext_theory = make_extraction("theory", score=5, quote_count=2)
    ext_ident = make_extraction("identification", score=4, quote_count=1)
    ext_ident.evidence[0].verification_status = "Unverified"
    ext_ident.evidence[0].page_reference = "N/A"
    ext_ident.evidence[0].verification_note = "No match"

    response = {
        "executive_summary": "The theory is established [Evidence: theory:Q1].",
        "decision_confidence": 0.85,
        "recommendation": "Accept",
        "recommendation_rationale": "High score on theory.",
        "detailed_assessment": {
            "major_strengths": ["Strong theoretical foundation [Evidence: theory:Q1]."],
            "major_concerns": [],
            "minor_issues": [],
        },
        "revision_suggestions": ["Clarify theory [Evidence: theory:Q1]."],
        "criterion_narrative": {
            "theory": "The theoretical formulation is sound [Evidence: theory:Q1].",
            "identification": "",  # Empty explanation because zero verified evidence
        },
    }
    patch_synthesis(monkeypatch, json.dumps(response))

    review = synthesize_review(make_paper(), [ext_theory, ext_ident], config)

    # Synthesis must succeed without 'failed' status
    assert review.review_status == "manual_review"
    assert review.overall_score == 90.0
    assert review.provisional_recommendation == "Accept"
    assert review.recommendation == "Needs Manual Review"
    # Grounded criterion is preserved
    assert review.criterion_narrative["theory"] == "The theoretical formulation is sound [Evidence: theory:Q1]."
    # Zero-evidence criterion gets standard disclaimer, NOT fabricated text
    assert review.criterion_narrative["identification"] == NO_GROUNDED_CRITERION
    # Evidence counts and warnings are accurate
    assert review.verified_evidence_count == 2
    assert review.unverified_evidence_count == 1
    assert "identification" in review.criteria_with_insufficient_evidence
    assert any("identification" in w for w in review.warnings)


def test_mixed_review_with_zero_evidence_criterion_omitted_narrative_is_safe(monkeypatch):
    """When a model completely omits a zero-evidence criterion from criterion_narrative,
    synthesis should succeed safely by populating NO_GROUNDED_CRITERION."""
    from agents.evidence_grounding import NO_GROUNDED_CRITERION

    config = FakeConfig([
        make_criterion("theory", 50),
        make_criterion("identification", 50),
    ])
    ext_theory = make_extraction("theory", score=5, quote_count=2)
    ext_ident = make_extraction("identification", score=4, quote_count=1)
    ext_ident.evidence[0].verification_status = "Unverified"

    # identification is omitted from criterion_narrative
    response = {
        "executive_summary": "The theory is established [Evidence: theory:Q1].",
        "decision_confidence": 0.85,
        "recommendation": "Accept",
        "recommendation_rationale": "High score on theory.",
        "detailed_assessment": {
            "major_strengths": ["Strong theoretical foundation [Evidence: theory:Q1]."],
            "major_concerns": [],
            "minor_issues": [],
        },
        "revision_suggestions": [],
        "criterion_narrative": {
            "theory": "The theoretical formulation is sound [Evidence: theory:Q1].",
        },
    }
    patch_synthesis(monkeypatch, json.dumps(response))

    review = synthesize_review(make_paper(), [ext_theory, ext_ident], config)

    assert review.review_status == "manual_review"
    assert review.recommendation == "Needs Manual Review"
    assert review.criterion_narrative["theory"] == "The theoretical formulation is sound [Evidence: theory:Q1]."
    assert review.criterion_narrative["identification"] == NO_GROUNDED_CRITERION


def test_empty_narrative_for_criterion_with_verified_evidence_uses_grounded_fallback(monkeypatch):
    """If a criterion HAS verified evidence but the model provides an empty explanation,
    it must provide a non-empty evidence-grounded explanation without crashing synthesis."""
    config = FakeConfig([
        make_criterion("theory", 100),
    ])
    ext_theory = make_extraction("theory", score=5, quote_count=2)

    response = {
        "executive_summary": "Theory is good [Evidence: theory:Q1].",
        "decision_confidence": 0.85,
        "recommendation": "Accept",
        "recommendation_rationale": "High score.",
        "detailed_assessment": {
            "major_strengths": ["Strong theoretical foundation [Evidence: theory:Q1]."],
            "major_concerns": [],
            "minor_issues": [],
        },
        "revision_suggestions": [],
        "criterion_narrative": {
            "theory": "",  # Empty even though verified quotes exist!
        },
    }
    patch_synthesis(monkeypatch, json.dumps(response))

    review = synthesize_review(make_paper(), [ext_theory], config)

    assert review.review_status == "complete"
    assert review.overall_score == 100.0
    assert "theory" in review.criterion_narrative
    assert review.criterion_narrative["theory"].strip() != ""
    assert "[Evidence: theory:Q1]" in review.criterion_narrative["theory"]


def test_grounded_narratives_with_trailing_period_and_abbreviated_ids(monkeypatch):
    """Ensure that tags placed after periods or with abbreviated secondary IDs are safely parsed and retained."""
    config = FakeConfig([
        make_criterion("theory", 50),
        make_criterion("data", 50),
    ])
    ext_theory = make_extraction("theory", score=5, quote_count=2)
    ext_data = make_extraction("data", score=4, quote_count=2)

    response = {
        "executive_summary": (
            "The model presents an attention architecture. [Evidence: theory:Q1] "
            "Data was collected from standard benchmarks [Evidence: data:Q1, Q2]."
        ),
        "decision_confidence": 0.85,
        "recommendation": "Accept",
        "recommendation_rationale": "Strong performance across both criteria.",
        "detailed_assessment": {
            "major_strengths": ["Fast parallelized training. [Evidence: theory:Q1]"],
            "major_concerns": [],
            "minor_issues": [],
        },
        "revision_suggestions": [],
        "criterion_narrative": {
            "theory": "The architecture introduces multi-head self-attention. [Evidence: theory:Q1]",
            "data": "The model uses English-German and English-French sets [Evidence: data:Q1, Q2].",
        },
    }
    patch_synthesis(monkeypatch, json.dumps(response))

    review = synthesize_review(make_paper(), [ext_theory, ext_data], config)

    assert review.review_status == "complete"
    assert "The model presents an attention architecture" in review.executive_summary
    assert "Data was collected from standard benchmarks" in review.executive_summary
    assert review.criterion_narrative["theory"] == "The architecture introduces multi-head self-attention [Evidence: theory:Q1]."
    assert review.criterion_narrative["data"] == "The model uses English-German and English-French sets [Evidence: data:Q1, Q2]."
    assert any("Exact quotation matching verifies text presence" in w for w in review.warnings)


def test_incorrect_criterion_id_is_strictly_rejected(monkeypatch):
    """When a criterion narrative cites an evidence ID from a DIFFERENT criterion, it must be rejected."""
    from agents.evidence_grounding import NO_GROUNDED_CRITERION

    config = FakeConfig([
        make_criterion("theory", 50),
        make_criterion("data", 50),
    ])
    ext_theory = make_extraction("theory", score=5, quote_count=2)
    ext_data = make_extraction("data", score=4, quote_count=2)

    response = {
        "executive_summary": "Overall valid summary [Evidence: theory:Q1].",
        "decision_confidence": 0.85,
        "recommendation": "Accept",
        "recommendation_rationale": "High score.",
        "detailed_assessment": {
            "major_strengths": [],
            "major_concerns": [],
            "minor_issues": [],
        },
        "revision_suggestions": [],
        "criterion_narrative": {
            # 'theory' erroneously cross-cites 'data:Q1'!
            "theory": "The theory is proven by dataset size [Evidence: data:Q1].",
            "data": "Standard datasets used [Evidence: data:Q1].",
        },
    }
    patch_synthesis(monkeypatch, json.dumps(response))

    review = synthesize_review(make_paper(), [ext_theory, ext_data], config)

    # The mismatched claim under theory must be omitted and replaced with fallback
    assert review.criterion_narrative["theory"] == NO_GROUNDED_CRITERION
    # Valid criterion narrative under data is retained
    assert review.criterion_narrative["data"] == "Standard datasets used [Evidence: data:Q1]."
    # Warning about omitted synthesis statements is logged
    assert any("synthesis statement(s)" in w for w in review.warnings)
