import itertools

import pytest

from intake.merge import RULE_TIERS, TIER_ORDER, merge, rank
from intake.rules import score
from intake.samples import load_samples

MODEL_OPTIONS = (None, *TIER_ORDER)


@pytest.mark.parametrize("rule_tier, model_tier", list(itertools.product(RULE_TIERS, MODEL_OPTIONS)))
def test_final_never_below_rule_tier(rule_tier, model_tier):
    """Exhaustive: every rule tier x every model answer (including none)."""
    result = merge(rule_tier, model_tier)
    assert rank(result.final_tier) >= rank(rule_tier)


@pytest.mark.parametrize("rule_tier, model_tier", list(itertools.product(RULE_TIERS, TIER_ORDER)))
def test_final_is_the_higher_of_the_two(rule_tier, model_tier):
    result = merge(rule_tier, model_tier)
    assert result.final_tier == max(rule_tier, model_tier, key=rank)


def test_no_model_means_rules_only():
    result = merge("medium", None)
    assert result.final_tier == "medium"
    assert result.source == "rules" and not result.model_used


def test_model_can_raise_to_prohibited():
    result = merge("high", "prohibited")
    assert result.final_tier == "prohibited" and result.model_raised


def test_model_below_floor_is_flagged_for_the_log():
    result = merge("high", "low")
    assert result.final_tier == "high"
    assert result.model_below_floor and result.source == "rules"


def test_injection_cannot_talk_request_11_below_the_floor():
    """Even if the injected text fools the model into 'low', the final tier stays High."""
    r11 = next(s for s in load_samples() if s.id == 11)
    rule_tier = score(r11.form).tier
    assert merge(rule_tier, "low").final_tier == "high"


@pytest.mark.parametrize("bad", ["prohibited", "critical", "", "LOW"])
def test_rule_tier_must_be_a_rule_tier(bad):
    with pytest.raises(ValueError):
        merge(bad, None)


def test_unknown_model_tier_is_rejected():
    with pytest.raises(ValueError):
        merge("low", "severe")
