"""Regression tests for the _rescue_misplaced_fields helper.

llama3.2:3b and other small models sometimes place 'decision_confidence'
(and occasionally 'recommendation' / 'recommendation_rationale') inside the
'criterion_narrative' dict rather than at the JSON top level.  The rescue
function must move those fields out before schema validation so that a
structurally valid LLM response is not falsely rejected.
"""

import json
import pytest

from agents.agent_synthesizer import (
    _rescue_misplaced_fields,
    synthesize_review,
)
from core.data_models import EvidenceItem, Extraction, Paper, PaperMetadata


# ---------------------------------------------------------------------------
# Unit tests for _rescue_misplaced_fields
# ---------------------------------------------------------------------------

def test_rescue_moves_decision_confidence_out_of_criterion_narrative():
    raw = {
        "executive_summary": "A fine paper.",
        "detailed_assessment": {
            "major_strengths": [],
            "major_concerns": [],
            "minor_issues": [],
        },
        "recommendation": "Accept",
        "recommendation_rationale": "Strong results.",
        "revision_suggestions": [],
        "criterion_narrative": {
            "theory": "Narrative text.",
            "decision_confidence": 0.82,   # misplaced — should be top-level float
        },
    }
    rescued = _rescue_misplaced_fields(raw)

    assert rescued["decision_confidence"] == 0.82
    assert "decision_confidence" not in rescued["criterion_narrative"]
    assert rescued["criterion_narrative"] == {"theory": "Narrative text."}


def test_rescue_moves_recommendation_out_of_criterion_narrative():
    raw = {
        "executive_summary": "A fine paper.",
        "detailed_assessment": {
            "major_strengths": [],
            "major_concerns": [],
            "minor_issues": [],
        },
        "recommendation_rationale": "Strong results.",
        "revision_suggestions": [],
        "decision_confidence": 0.7,
        "criterion_narrative": {
            "theory": "Narrative text.",
            "recommendation": "Accept",   # misplaced
        },
    }
    rescued = _rescue_misplaced_fields(raw)

    assert rescued["recommendation"] == "Accept"
    assert "recommendation" not in rescued["criterion_narrative"]


def test_rescue_does_not_overwrite_existing_top_level_field():
    """If decision_confidence is already at the top level, the nested one is ignored."""
    raw = {
        "decision_confidence": 0.5,   # already correct
        "criterion_narrative": {
            "theory": "Narrative.",
            "decision_confidence": 0.9,  # misplaced but won't overwrite
        },
    }
    rescued = _rescue_misplaced_fields(raw)

    assert rescued["decision_confidence"] == 0.5  # top-level value preserved
    assert "decision_confidence" not in rescued["criterion_narrative"]


def test_rescue_leaves_legitimate_criterion_narrative_strings():
    """Criterion IDs whose names happen to share a name with rescuable fields
    but have string values should not be silently discarded."""
    raw = {
        "decision_confidence": 0.6,
        "recommendation": "Reject",
        "recommendation_rationale": "Low quality.",
        "revision_suggestions": [],
        "executive_summary": "Summary.",
        "detailed_assessment": {
            "major_strengths": [],
            "major_concerns": [],
            "minor_issues": [],
        },
        "criterion_narrative": {
            "theory": "Narrative for theory.",
            "method": "Narrative for method.",
        },
    }
    rescued = _rescue_misplaced_fields(raw)

    assert rescued["criterion_narrative"] == {
        "theory": "Narrative for theory.",
        "method": "Narrative for method.",
    }


def test_rescue_is_idempotent_on_clean_payload():
    """A well-formed payload is returned unchanged."""
    raw = {
        "executive_summary": "Good.",
        "decision_confidence": 0.7,
        "recommendation": "Accept",
        "recommendation_rationale": "Fine.",
        "revision_suggestions": [],
        "detailed_assessment": {
            "major_strengths": [],
            "major_concerns": [],
            "minor_issues": [],
        },
        "criterion_narrative": {"theory": "Narrative."},
    }
    rescued = _rescue_misplaced_fields(raw)
    assert rescued == raw


def test_rescue_handles_non_dict_input_gracefully():
    assert _rescue_misplaced_fields(None) is None
    assert _rescue_misplaced_fields("not a dict") == "not a dict"
    assert _rescue_misplaced_fields([1, 2]) == [1, 2]


# ---------------------------------------------------------------------------
# Integration test: synthesis succeeds when decision_confidence is misplaced
# ---------------------------------------------------------------------------

class _FakeConfig:
    extraction_failures = {}

    def __init__(self, criteria):
        self.criteria = criteria

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
        return next((c for c in self.criteria if c["id"] == criterion_id), {})

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
        return "Synthesize."


def _make_paper():
    return Paper(
        filename="paper.pdf",
        original_path="/does/not/need/to/exist.pdf",
        content_markdown="Paper text.",
        token_count=2,
        metadata=PaperMetadata(title="A theory paper", abstract="A short abstract."),
    )


def _make_criterion(criterion_id, weight=100):
    return {
        "id": criterion_id,
        "name": criterion_id,
        "description": "Assess the configured contribution.",
        "weight": weight,
        "scale": {"range": [1, 5], "labels": {str(i): str(i) for i in range(1, 6)}},
    }


def _make_extraction(criterion_id, score=5, quote_count=2):
    return Extraction(
        paper_id="paper",
        criterion_id=criterion_id,
        score=score,
        score_justification="The extraction gives a criterion-specific explanation.",
        evidence=[
            EvidenceItem(
                quote=f"Source quote {i} for {criterion_id}.",
                page_reference=f"PDF Page {i}",
                relevance="Supports the criterion.",
                verification_status="Verified",
                pdf_page_numbers=[i],
            )
            for i in range(1, quote_count + 1)
        ],
        strengths=[],
        weaknesses=[],
        confidence=0.8,
        model_used="ollama/llama3.2:3b",
    )


def test_synthesis_succeeds_when_decision_confidence_misplaced(monkeypatch):
    """End-to-end: misplaced decision_confidence inside criterion_narrative is rescued,
    schema validation passes, and the review is marked complete (not failed)."""
    config = _FakeConfig([_make_criterion("theory", 100)])
    extraction = _make_extraction("theory", score=5, quote_count=2)

    # Simulate the small-model mistake: decision_confidence nested inside criterion_narrative
    bad_response = json.dumps({
        "executive_summary": (
            "The cited passages support the criterion assessments "
            "[Evidence: theory:Q1]."
        ),
        "detailed_assessment": {
            "major_strengths": [
                "The supplied passage supports this assessment [Evidence: theory:Q1]."
            ],
            "major_concerns": [],
            "minor_issues": [],
        },
        "recommendation": "Accept",
        "recommendation_rationale": "Strong evidence supports the criterion.",
        "revision_suggestions": [
            "Clarify the point described in the passage [Evidence: theory:Q1]."
        ],
        "criterion_narrative": {
            "theory": "The verified passage supports this criterion [Evidence: theory:Q1].",
            "decision_confidence": 0.87,  # ← the bug: this belongs at top level
        },
    })

    monkeypatch.setattr(
        "agents.agent_synthesizer.call_llm",
        lambda **kwargs: {"success": True, "content": bad_response, "cost": 0.0},
    )

    review = synthesize_review(_make_paper(), [extraction], config)

    # Rescue should have moved decision_confidence out, so synthesis passes.
    assert review.review_status != "failed", (
        f"Expected synthesis to recover from misplaced decision_confidence, "
        f"got review_status={review.review_status!r}; "
        f"warnings={review.warnings}"
    )
    # The criterion_narrative must not contain non-string keys.
    for key, value in review.criterion_narrative.items():
        assert isinstance(key, str), f"criterion_narrative key is not str: {key!r}"
        assert isinstance(value, str), f"criterion_narrative value is not str: {value!r}"
