"""Load and validate rubric.yaml, and define the intake form schema.

The rubric is validated on load so a policy edit that breaks the structure
fails loudly at startup instead of silently scoring requests wrong.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

RUBRIC_PATH = Path(__file__).resolve().parent.parent / "rubric.yaml"

Tier = Literal["low", "medium", "high", "prohibited"]
Level = Literal["low", "medium", "high"]
DimensionName = Literal[
    "data_privacy",
    "confidentiality_ip",
    "bias_fairness",
    "output_integrity",
    "security_tool_route",
    "regulation_eu_ai_act",
]
DIMENSIONS: tuple[str, ...] = DimensionName.__args__  # type: ignore[attr-defined]

# Form fields a rule may test, and whether they hold a list or a scalar.
LIST_FIELDS = {"data_types", "countries"}
BOOL_FIELDS = {"decision_about_person", "eu_uk_involved"}
SCALAR_FIELDS = {"team", "destination", "audience", "decision_type", "human_review"}
RULE_FIELDS = LIST_FIELDS | BOOL_FIELDS | SCALAR_FIELDS


# ---------------------------------------------------------------- rubric ---
class Condition(BaseModel):
    field: str
    any_of: list[str] | None = None
    is_true: bool | None = None

    @model_validator(mode="after")
    def _one_operator(self) -> "Condition":
        if (self.any_of is None) == (self.is_true is None):
            raise ValueError(f"condition on '{self.field}' needs exactly one of any_of / is_true")
        if self.field not in RULE_FIELDS:
            raise ValueError(f"unknown form field in rule: '{self.field}'")
        if self.is_true is not None and self.field not in BOOL_FIELDS:
            raise ValueError(f"is_true used on non-boolean field '{self.field}'")
        return self


class Trigger(BaseModel):
    id: str
    level: Level
    description: str
    scored_by: Literal["rules", "model"] = "rules"
    when: list[Condition] = Field(default_factory=list)

    @model_validator(mode="after")
    def _rules_need_conditions(self) -> "Trigger":
        if self.scored_by == "rules" and not self.when:
            raise ValueError(f"rule trigger {self.id} has no conditions")
        if self.scored_by == "model" and self.when:
            raise ValueError(f"model trigger {self.id} must not have conditions")
        return self


class Dimension(BaseModel):
    label: str
    nist_genai_risk: list[str]
    triggers: list[Trigger]


class TierControls(BaseModel):
    required: list[str]
    review: str
    reviewer_actions: list[str]
    signoffs_required: list[str] = Field(default_factory=list)


class ValueAssumptions(BaseModel):
    working_weeks_per_year: float = Field(gt=0)
    loaded_hourly_rate_usd: float = Field(ge=0)
    high_value_hours_cutoff: float = Field(ge=0)
    share_saved_min: float = Field(ge=0, le=1)
    share_saved_max: float = Field(ge=0, le=1)


class Quadrant(BaseModel):
    value: Literal["high", "low"]
    risk: Literal["higher", "lower"]
    label: str


class Priority(BaseModel):
    higher_risk_tiers: list[Tier]
    quadrants: dict[str, Quadrant]


class Rubric(BaseModel):
    version: str
    effective_date: str
    owner: str
    tiers: list[Tier]
    form_options: dict[str, dict[str, str]]
    dimensions: dict[DimensionName, Dimension]
    prohibited: dict
    human_review_nist_genai_risk: list[str]
    controls: dict[Tier, TierControls]
    value: ValueAssumptions
    priority: Priority
    nist_ai_rmf: dict

    @model_validator(mode="after")
    def _check(self) -> "Rubric":
        if self.tiers != ["low", "medium", "high", "prohibited"]:
            raise ValueError("tiers must be exactly [low, medium, high, prohibited] in that order")
        missing = set(DIMENSIONS) - set(self.dimensions)
        if missing:
            raise ValueError(f"rubric is missing dimensions: {sorted(missing)}")
        missing_tiers = set(self.tiers) - set(self.controls)
        if missing_tiers:
            raise ValueError(f"controls missing for tiers: {sorted(missing_tiers)}")
        # Every value a rule tests must be a real form option.
        seen_ids: set[str] = set()
        for dim_name, dim in self.dimensions.items():
            for trig in dim.triggers:
                if trig.id in seen_ids:
                    raise ValueError(f"duplicate trigger id {trig.id}")
                seen_ids.add(trig.id)
                for cond in trig.when:
                    if cond.any_of is None:
                        continue
                    options = self.form_options.get(cond.field)
                    if options is None:
                        raise ValueError(f"{trig.id}: no form_options for field '{cond.field}'")
                    unknown = set(cond.any_of) - set(options)
                    if unknown:
                        raise ValueError(f"{trig.id}: unknown values {sorted(unknown)} for '{cond.field}'")
        return self

    def tier_rank(self, tier: str) -> int:
        return self.tiers.index(tier)


@lru_cache(maxsize=4)
def load_rubric(path: str | Path = RUBRIC_PATH) -> Rubric:
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return Rubric.model_validate(raw)


# ------------------------------------------------------------ intake form ---
class IntakeForm(BaseModel):
    """The twelve intake fields. Codes match rubric.yaml form_options."""

    title: str = Field(min_length=3, max_length=120)
    team: str
    task_description: str = Field(min_length=10, max_length=4000)
    data_types: list[str] = Field(min_length=1)
    destination: str
    audience: str
    decision_about_person: bool = False
    decision_type: str | None = None
    human_review: str
    countries: list[str] = Field(default_factory=list)
    eu_uk_involved: bool = False
    hours_per_week: float = Field(ge=0, le=60)
    people: int = Field(ge=1, le=10000)
    share_saved: float = Field(ge=0.10, le=0.80)

    @model_validator(mode="after")
    def _check(self) -> "IntakeForm":
        if self.decision_about_person and not self.decision_type:
            raise ValueError("decision_type is required when decision_about_person is true")
        if not self.decision_about_person and self.decision_type:
            raise ValueError("decision_type given but decision_about_person is false")
        return self

    def validate_against(self, rubric: Rubric) -> None:
        """Check every coded answer is a real option in the rubric."""
        opts = rubric.form_options
        checks = {
            "team": [self.team],
            "data_types": self.data_types,
            "destination": [self.destination],
            "audience": [self.audience],
            "human_review": [self.human_review],
            "decision_type": [self.decision_type] if self.decision_type else [],
        }
        for field, values in checks.items():
            bad = [v for v in values if v not in opts[field]]
            if bad:
                raise ValueError(f"invalid {field}: {bad}")
