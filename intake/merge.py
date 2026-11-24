"""Combine the rule tier with the model's suggestion.

The model can raise a tier, never lower it:
    final = max(rule_tier, model_tier)   on  low < medium < high < prohibited
With no model result (no key, refusal, parse failure), final = rule_tier.
"""
from __future__ import annotations

from dataclasses import dataclass

TIER_ORDER = ("low", "medium", "high", "prohibited")
RULE_TIERS = ("low", "medium", "high")  # rules alone can never reach prohibited


def rank(tier: str) -> int:
    try:
        return TIER_ORDER.index(tier)
    except ValueError:
        raise ValueError(f"unknown tier: {tier!r}") from None


@dataclass(frozen=True)
class MergeResult:
    rule_tier: str
    model_tier: str | None
    final_tier: str
    source: str  # "rules", "model" (model raised it) or "agree"
    model_used: bool

    @property
    def model_raised(self) -> bool:
        return self.model_tier is not None and rank(self.model_tier) > rank(self.rule_tier)

    @property
    def model_below_floor(self) -> bool:
        """The model suggested a lower tier and was overruled. Worth logging."""
        return self.model_tier is not None and rank(self.model_tier) < rank(self.rule_tier)


def merge(rule_tier: str, model_tier: str | None = None) -> MergeResult:
    if rule_tier not in RULE_TIERS:
        raise ValueError(f"rule tier must be one of {RULE_TIERS}, got {rule_tier!r}")
    if model_tier is None:
        return MergeResult(rule_tier, None, rule_tier, "rules", model_used=False)

    r, m = rank(rule_tier), rank(model_tier)
    final = TIER_ORDER[max(r, m)]
    source = "agree" if r == m else ("model" if m > r else "rules")
    return MergeResult(rule_tier, model_tier, final, source, model_used=True)
