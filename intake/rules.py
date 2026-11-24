"""Deterministic scoring from the intake form.

Rules set the floor. They read only the coded form answers, never the free
text, so nothing written in the description can change the rule tier.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from intake.config import DIMENSIONS, IntakeForm, Rubric, Trigger, load_rubric

LEVEL_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class DimensionResult:
    name: str
    level: str
    triggered: tuple[str, ...]  # trigger ids that fired at the winning level
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class RuleResult:
    dimensions: dict[str, DimensionResult]
    tier: str
    rubric_version: str
    all_triggered: tuple[str, ...] = field(default_factory=tuple)

    @property
    def driving_dimensions(self) -> list[str]:
        return [d.name for d in self.dimensions.values() if d.level == self.tier]


def _field_values(form: IntakeForm, name: str) -> list:
    value = getattr(form, name)
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def trigger_fires(trigger: Trigger, form: IntakeForm) -> bool:
    if trigger.scored_by != "rules":
        return False
    for cond in trigger.when:
        if cond.is_true is not None:
            if bool(getattr(form, cond.field)) is not cond.is_true:
                return False
        else:
            if not set(_field_values(form, cond.field)) & set(cond.any_of or []):
                return False
    return True


def score(form: IntakeForm, rubric: Rubric | None = None) -> RuleResult:
    """Score each dimension; the worst dimension sets the tier (no averaging)."""
    rubric = rubric or load_rubric()
    form.validate_against(rubric)

    dims: dict[str, DimensionResult] = {}
    fired_all: list[str] = []
    for name in DIMENSIONS:
        fired = [t for t in rubric.dimensions[name].triggers if trigger_fires(t, form)]
        fired_all.extend(t.id for t in fired)
        if not fired:
            dims[name] = DimensionResult(name, "low", (), ("No rule triggered",))
            continue
        top = max(LEVEL_RANK[t.level] for t in fired)
        winners = [t for t in fired if LEVEL_RANK[t.level] == top]
        dims[name] = DimensionResult(
            name=name,
            level=winners[0].level,
            triggered=tuple(t.id for t in winners),
            reasons=tuple(f"{t.id}: {t.description}" for t in winners),
        )

    worst = max(dims.values(), key=lambda d: LEVEL_RANK[d.level]).level
    return RuleResult(
        dimensions=dims,
        tier=worst,
        rubric_version=rubric.version,
        all_triggered=tuple(fired_all),
    )


# ------------------------------------------------------------------ value ---
def hours_saved_per_year(form: IntakeForm, rubric: Rubric | None = None) -> float:
    rubric = rubric or load_rubric()
    v = rubric.value
    share = min(max(form.share_saved, v.share_saved_min), v.share_saved_max)
    return round(form.hours_per_week * form.people * share * v.working_weeks_per_year, 1)


def dollar_value_per_year(form: IntakeForm, rubric: Rubric | None = None) -> float:
    rubric = rubric or load_rubric()
    return round(hours_saved_per_year(form, rubric) * rubric.value.loaded_hourly_rate_usd, 2)


Quadrant = Literal["fast_track", "govern_then_build", "self_serve", "park"]


def priority_quadrant(hours_saved: float, tier: str, rubric: Rubric | None = None) -> Quadrant | None:
    """Place a request on the value vs risk grid. Prohibited never reaches it."""
    rubric = rubric or load_rubric()
    if tier == "prohibited":
        return None
    high_value = hours_saved >= rubric.value.high_value_hours_cutoff
    higher_risk = tier in rubric.priority.higher_risk_tiers
    for key, q in rubric.priority.quadrants.items():
        if (q.value == "high") == high_value and (q.risk == "higher") == higher_risk:
            return key  # type: ignore[return-value]
    raise ValueError("priority quadrants in rubric.yaml do not cover every case")
