from types import SimpleNamespace

from agents.agent_critic import _create_fallback_review
from core.data_models import (
    DetailedAssessment,
    Extraction,
    Paper,
    PaperMetadata,
    WeightedBreakdown,
)


class FakeConfig:
    def get_llm_config(self):
        return {
            "synthesizer_provider": "test-provider",
            "synthesizer_model": "test-model",
        }

    def get_criteria(self):
        return []


def make_paper():
    return Paper(
        id="paper-test",
        filename="test-paper.pdf",
        original_path="/tmp/test-paper.pdf",
        content_markdown="Test content",
        token_count=2,
        metadata=PaperMetadata(title="Test Paper", abstract="Test abstract"),
    )


def test_fallback_review_preserves_base_and_zero_novelty_score():
    review = _create_fallback_review(
        paper=make_paper(),
        extractions=[],
        config=FakeConfig(),
        base_score=82.0,
        breakdown={
            "methodology": WeightedBreakdown(
                score=4,
                weight=100,
                weighted_score=82.0,
            )
        },
        recommendation="Accept",
        rationale="Base recommendation",
        novelty_adjusted_score=0.0,
    )

    assert review.base_score == 82.0
    assert review.novelty_adjusted_score == 0.0
    assert review.overall_score == 0.0
    assert review.llm_fallback_used is True


def test_fallback_review_without_novelty_uses_base_score():
    review = _create_fallback_review(
        paper=make_paper(),
        extractions=[],
        config=FakeConfig(),
        base_score=82.0,
        breakdown={},
        recommendation="Accept",
        rationale="Base recommendation",
        novelty_adjusted_score=None,
    )

    assert review.base_score == 82.0
    assert review.novelty_adjusted_score is None
    assert review.overall_score == 82.0


def test_normal_review_preserves_base_and_zero_novelty_score(monkeypatch):
    import json
    import agents.agent_critic as critic
    from core.data_models import BaselineReference, Extraction

    class NormalConfig(FakeConfig):
        def get_prompt(self, name):
            return "{paper_title} {paper_abstract} {json_dump_of_extractions} {weights_table} {calculated_score} {calculated_recommendation}"

        def get_agent_config(self):
            return {"critic_max_json_retries": 1}

        def get_criteria(self):
            return []

        def get_recommendation_thresholds(self):
            return [
                {"threshold": 85, "label": "Accept"},
                {"threshold": 70, "label": "Accept with Revisions"},
                {"threshold": 50, "label": "Revise and Resubmit"},
                {"threshold": 0, "label": "Reject"},
            ]

        def get_criterion_by_id(self, criterion_id):
            return {}

    config = NormalConfig()
    config.get_llm_config = lambda: {
        "synthesizer_provider": "test",
        "synthesizer_model": "mock",
        "temperature": 0,
        "max_retries": 1,
    }

    extraction = Extraction(
        paper_id="paper-test",
        criterion_id="methodology",
        score=4,
        score_justification="Test",
        evidence=[],
        strengths=[],
        weaknesses=[],
        confidence=0.9,
        model_used="mock-extractor",
    )

    response_data = {
        "executive_summary": "Mock summary",
        "detailed_assessment": {
            "major_strengths": [],
            "major_concerns": [],
            "minor_issues": [],
        },
        "criterion_narrative": {"methodology": "Mock criterion narrative"},
        "recommendation": "Accept with Revisions",
        "revision_suggestions": [],
        "decision_confidence": 0.9,
        "recommendation_rationale": "Mock rationale",
    }

    monkeypatch.setattr(
        critic,
        "calculate_recommendation",
        lambda extractions, config: (
            "Accept with Revisions",
            "Base rationale",
            82.0,
            {},
        ),
    )
    monkeypatch.setattr(
        critic,
        "_calculate_novelty_adjusted_score",
        lambda base_score, extractions, config: 0.0,
    )
    monkeypatch.setattr(
        critic,
        "_generate_research_trajectory",
        lambda *args, **kwargs: "",
    )
    monkeypatch.setattr(
        critic,
        "call_llm",
        lambda **kwargs: {
            "success": True,
            "content": json.dumps(response_data),
            "cost": 0.0,
        },
    )
    monkeypatch.setattr(critic, "_repair_schema_mismatch", lambda **kwargs: kwargs["review_data"])
    monkeypatch.setattr(critic, "get_weights_table", lambda criteria: "")

    paper = make_paper()
    review = critic.synthesize_grounded_review(
        paper=paper,
        extractions=[extraction],
        config=config,
        baseline=BaselineReference(
            sub_topic="test topic",
            query_keywords=[],
            baseline_papers=[],
            key_findings_summary="Test baseline",
        ),
    )

    assert review is not None
    assert review.base_score == 82.0
    assert review.novelty_adjusted_score == 0.0
    assert review.overall_score == 0.0
    assert review.recommendation == "Needs Manual Review"
    assert review.provisional_recommendation == "Reject"
    assert review.review_status == "incomplete"
    assert review.llm_fallback_used is False


def test_normal_review_without_baseline_preserves_base_score(monkeypatch):
    import json
    import agents.agent_critic as critic
    from core.data_models import Extraction

    class NormalConfig(FakeConfig):
        def get_prompt(self, name):
            return "{paper_title} {paper_abstract} {json_dump_of_extractions} {weights_table} {calculated_score} {calculated_recommendation}"

        def get_agent_config(self):
            return {"critic_max_json_retries": 1}

        def get_criteria(self):
            return []

        def get_recommendation_thresholds(self):
            return [
                {"threshold": 85, "label": "Accept"},
                {"threshold": 70, "label": "Accept with Revisions"},
                {"threshold": 50, "label": "Revise and Resubmit"},
                {"threshold": 0, "label": "Reject"},
            ]

        def get_criterion_by_id(self, criterion_id):
            return {}

    config = NormalConfig()
    config.get_llm_config = lambda: {
        "synthesizer_provider": "test",
        "synthesizer_model": "mock",
        "temperature": 0,
        "max_retries": 1,
    }

    extraction = Extraction(
        paper_id="paper-test",
        criterion_id="methodology",
        score=4,
        score_justification="Test",
        evidence=[],
        strengths=[],
        weaknesses=[],
        confidence=0.9,
        model_used="mock-extractor",
    )

    response_data = {
        "executive_summary": "Mock summary",
        "detailed_assessment": {
            "major_strengths": [],
            "major_concerns": [],
            "minor_issues": [],
        },
        "criterion_narrative": {"methodology": "Mock criterion narrative"},
        "recommendation": "Accept with Revisions",
        "revision_suggestions": [],
        "decision_confidence": 0.9,
        "recommendation_rationale": "Mock rationale",
    }

    monkeypatch.setattr(
        critic,
        "calculate_recommendation",
        lambda extractions, config: (
            "Accept with Revisions",
            "Base rationale",
            82.0,
            {},
        ),
    )
    monkeypatch.setattr(
        critic,
        "call_llm",
        lambda **kwargs: {
            "success": True,
            "content": json.dumps(response_data),
            "cost": 0.0,
        },
    )
    monkeypatch.setattr(critic, "_repair_schema_mismatch", lambda **kwargs: kwargs["review_data"])
    monkeypatch.setattr(critic, "get_weights_table", lambda criteria: "")

    review = critic.synthesize_grounded_review(
        paper=make_paper(),
        extractions=[extraction],
        config=config,
        baseline=None,
    )

    assert review is not None
    assert review.base_score == 82.0
    assert review.novelty_adjusted_score is None
    assert review.overall_score == 82.0
    assert review.recommendation == "Needs Manual Review"
    assert review.provisional_recommendation == "Accept with Revisions"
    assert review.llm_fallback_used is False


def test_end_to_end_mocked_review_preserves_zero_novelty_in_export(
    monkeypatch, tmp_path
):
    import json

    import agents.agent_critic as critic
    from core.data_models import BaselineReference, Extraction
    from utilities.output_generator import save_review_markdown

    class IntegrationConfig(FakeConfig):
        def get_prompt(self, name):
            return (
                "{paper_title} {paper_abstract} "
                "{json_dump_of_extractions} {weights_table} "
                "{calculated_score} {calculated_recommendation}"
            )

        def get_agent_config(self):
            return {"critic_max_json_retries": 1}

        def get_criteria(self):
            return []

        def get_recommendation_thresholds(self):
            return [
                {"threshold": 85, "label": "Accept"},
                {"threshold": 70, "label": "Accept with Revisions"},
                {"threshold": 50, "label": "Revise and Resubmit"},
                {"threshold": 0, "label": "Reject"},
            ]

        def get_criterion_by_id(self, criterion_id):
            return {}

    config = IntegrationConfig()
    config.get_llm_config = lambda: {
        "synthesizer_provider": "test",
        "synthesizer_model": "mock",
        "temperature": 0,
        "max_retries": 1,
    }

    extraction = Extraction(
        paper_id="paper-test",
        criterion_id="methodology",
        score=4,
        score_justification="Mocked test evidence",
        evidence=[],
        strengths=[],
        weaknesses=[],
        confidence=0.9,
        model_used="mock-extractor",
    )

    response_data = {
        "executive_summary": "Isolated integration test review.",
        "detailed_assessment": {
            "major_strengths": ["Test strength"],
            "major_concerns": ["Test concern"],
            "minor_issues": [],
        },
        "criterion_narrative": {"methodology": "Mock criterion narrative"},
        "recommendation": "Accept with Revisions",
        "revision_suggestions": ["Add further validation."],
        "decision_confidence": 0.9,
        "recommendation_rationale": "Mocked integration rationale.",
    }

    monkeypatch.setattr(
        critic,
        "calculate_recommendation",
        lambda extractions, config: (
            "Accept with Revisions",
            "Base recommendation",
            82.0,
            {},
        ),
    )
    monkeypatch.setattr(
        critic,
        "_calculate_novelty_adjusted_score",
        lambda base_score, extractions, config: 0.0,
    )
    monkeypatch.setattr(
        critic,
        "_generate_research_trajectory",
        lambda *args, **kwargs: "",
    )
    monkeypatch.setattr(
        critic,
        "call_llm",
        lambda **kwargs: {
            "success": True,
            "content": json.dumps(response_data),
            "cost": 0.0,
        },
    )
    monkeypatch.setattr(
        critic,
        "_repair_schema_mismatch",
        lambda **kwargs: kwargs["review_data"],
    )
    monkeypatch.setattr(critic, "get_weights_table", lambda criteria: "")

    review = critic.synthesize_grounded_review(
        paper=make_paper(),
        extractions=[extraction],
        config=config,
        baseline=BaselineReference(
            sub_topic="test topic",
            query_keywords=[],
            baseline_papers=[],
            key_findings_summary="Mock baseline",
        ),
    )

    assert review is not None
    assert review.base_score == 82.0
    assert review.novelty_adjusted_score == 0.0
    assert review.overall_score == 0.0
    assert review.recommendation == "Needs Manual Review"
    assert review.provisional_recommendation == "Reject"
    assert review.llm_fallback_used is False
    assert review.review_status == "incomplete"
    assert review.detailed_assessment.major_strengths == []
    assert review.detailed_assessment.major_concerns == []
    assert review.revision_suggestions == []
    assert "Test concern" not in review.executive_summary
    assert any("synthesis statement(s)" in warning for warning in review.warnings)

    output_path = tmp_path / "integration-review.md"
    save_review_markdown(
        review,
        str(output_path),
        include_literature_context=True,
    )

    exported = output_path.read_text(encoding="utf-8")
    assert "**Overall Score:** **0.0 / 100**" in exported
    assert "Base Score: 82.0" in exported
    assert "**Novelty-Adjusted Score: 0.0**" in exported
    assert "Novelty Adjustment: -82.0" in exported
    assert "**Recommendation:** **NEEDS MANUAL REVIEW**" in exported
    assert "**Provisional Recommendation:** Reject" in exported
    assert "Test concern" not in exported
    assert "Add further validation." not in exported
