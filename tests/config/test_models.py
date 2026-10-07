import pytest
from pydantic import ValidationError

from tuppence.config.models import AgentManifest

_B = {"max_llm_calls": 1, "max_tokens": 1, "max_gbp": 0, "max_seconds": 1}


def test_manifest_forbids_unknown_keys():
    with pytest.raises(ValidationError):
        AgentManifest.model_validate(
            {"name": "x", "description": "d", "budgets": _B, "colour": "red"}
        )


def test_budgets_must_be_non_negative():
    with pytest.raises(ValidationError):
        AgentManifest.model_validate(
            {"name": "x", "description": "d", "budgets": {**_B, "max_llm_calls": -1}}
        )


@pytest.mark.parametrize("bad", [float("inf"), float("nan")])
def test_budgets_reject_non_finite_numbers(bad):
    with pytest.raises(ValidationError):
        AgentManifest.model_validate(
            {"name": "x", "description": "d", "budgets": {**_B, "max_gbp": bad}}
        )
    with pytest.raises(ValidationError):
        AgentManifest.model_validate(
            {"name": "x", "description": "d", "budgets": {**_B, "max_seconds": bad}}
        )


def test_thresholds_reject_nan():
    with pytest.raises(ValidationError):
        AgentManifest.model_validate(
            {"name": "x", "description": "d", "budgets": _B, "thresholds": {"t": float("nan")}}
        )
