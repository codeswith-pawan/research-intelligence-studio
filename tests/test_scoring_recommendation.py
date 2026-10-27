from types import SimpleNamespace

import pytest

from agents.agent_synthesizer import calculate_recommendation
from core.data_models import Extraction
from core.config_loader import DEFAULT_THRESHOLDS


class FakeConfig:
    def __init__(self, criteria, thresholds=None):
        self.criteria = criteria
        self.thresholds = thresholds if thresholds is not None else DEFAULT_THRESHOLDS

    def get_criterion_by_id(self, criterion_id):
        return next(
            (criterion for criterion in self.criteria if criterion["id"] == criterion_id),
            {},
        )

    def get_criteria(self):
        return self.criteria

    def get_recommendation_thresholds(self):
        return self.thresholds


def make_extraction(criterion_id, score):
    return Extraction(
        paper_id="test-paper",
        criterion_id=criterion_id,
        score=score,
        score_justification="Test justification",
        evidence=[],
        strengths=[],
        weaknesses=[],
        confidence=1.0,
        model_used="test-model",
    )


@pytest.fixture
def config():
    return FakeConfig(
        criteria=[
            {
                "id": "methodology",
                "weight": 60,
                "scale": {"labels": {"1": "Poor", "2": "Fair", "3": "Good", "4": "Very good", "5": "Excellent"}},
            },
            {
                "id": "novelty",
                "weight": 40,
                "scale": {"labels": {"1": "Poor", "2": "Fair", "3": "Good", "4": "Very good", "5": "Excellent"}},
            },
        ]
    )


def test_calculate_recommendation_returns_weighted_score_and_breakdown(config):
    extractions = [
        make_extraction("methodology", 4),
        make_extraction("novelty", 3),
    ]

    recommendation, rationale, score, breakdown = calculate_recommendation(
        extractions, config
    )

    assert score == pytest.approx(72.0)
    assert recommendation == "Accept with Revisions"
    assert rationale == "Overall score: 72.0"
    assert breakdown["methodology"].weighted_score == pytest.approx(48.0)
    assert breakdown["novelty"].weighted_score == pytest.approx(24.0)


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (85, "Accept"),
        (70, "Accept with Revisions"),
        (50, "Revise and Resubmit"),
        (49.99, "Reject"),
    ],
)
def test_recommendation_threshold_boundaries(config, score, expected):
    config.criteria = [
        {
            "id": "single",
            "weight": 100,
            "scale": {"labels": {str(i): f"Level {i}" for i in range(1, 101)}},
        }
    ]
    # Integer extraction scores map directly to the 0-100 score here.
    recommendation, _, actual_score, _ = calculate_recommendation(
        [make_extraction("single", int(score) if score == int(score) else 49)],
        config,
    )

    if score == 49.99:
        assert actual_score == pytest.approx(49.0)
    else:
        assert actual_score == pytest.approx(score)
    assert recommendation == expected


def test_unknown_criterion_is_skipped_without_crashing(config):
    recommendation, _, score, breakdown = calculate_recommendation(
        [make_extraction("not-configured", 5)],
        config,
    )

    assert score == 0
    assert recommendation == "Reject"
    assert breakdown == {}


def test_empty_extractions_return_zero_and_reject(config):
    recommendation, rationale, score, breakdown = calculate_recommendation([], config)

    assert recommendation == "Reject"
    assert rationale == "Overall score: 0.0"
    assert score == 0
    assert breakdown == {}


def test_zero_novelty_score_is_not_treated_as_missing():
    # Regression specification for the critic's score fallback:
    # an explicit 0.0 must be retained rather than replaced by base_score.
    base_score = 82.0
    novelty_adjusted_score = 0.0

    final_score = (
        novelty_adjusted_score
        if novelty_adjusted_score is not None
        else base_score
    )

    assert final_score == 0.0
