"""One intake run: mask -> rules -> model -> merge -> value, logged step by step.

Order matters: masking happens before the model is called, and only the
masked text is stored or sent. Each step appends an audit row.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from intake import db
from intake.config import IntakeForm, Rubric, load_rubric
from intake.llm import Assessment, AssessOutcome, assess, model_tier
from intake.merge import MergeResult, merge
from intake.redact import RedactionResult, redact
from intake.rules import (
    RuleResult,
    dollar_value_per_year,
    hours_saved_per_year,
    priority_quadrant,
    score,
)


@dataclass
class IntakeResult:
    request_id: int
    assessment_id: int
    redaction: RedactionResult
    rules: RuleResult
    model: AssessOutcome | None
    model_status: str
    merged: MergeResult
    hours_saved: float
    dollar_value: float
    quadrant: str | None
    controls: list[str]
    review: str


def rule_json(r: RuleResult) -> dict:
    return {
        "tier": r.tier,
        "rubric_version": r.rubric_version,
        "triggered": list(r.all_triggered),
        "dimensions": {k: {"level": v.level, "triggered": list(v.triggered), "reasons": list(v.reasons)}
                       for k, v in r.dimensions.items()},
    }


def run_intake(conn, form: IntakeForm, *, submitted_by: str, source: str = "live",
               sample_id: int | None = None, client: Any = None, use_model: bool = True,
               precomputed: Assessment | None = None, rubric: Rubric | None = None,
               force_redaction_fallback: bool = False,
               skip_reason: str = "skipped_cap") -> IntakeResult:
    rubric = rubric or load_rubric()
    form.validate_against(rubric)

    # 1. Mask before anything is stored or sent.
    red = redact(form.task_description, force_fallback=force_redaction_fallback)
    request_id = db.insert_request(
        conn, submitted_by=submitted_by, source=source, sample_id=sample_id,
        form=form.model_dump(), masked_description=red.text,
        redaction_engine=red.engine, redaction_counts=red.counts,
    )
    db.log_event(conn, request_id, submitted_by, "submitted", {"title": form.title, "source": source})
    db.log_event(conn, request_id, "system", "redacted",
                 {"engine": red.engine, "counts": red.counts, "total": red.total})

    # 2. Rules floor from coded answers only.
    rules = score(form, rubric)
    db.log_event(conn, request_id, "system", "rules_scored",
                 {"tier": rules.tier, "triggered": list(rules.all_triggered),
                  "rubric_version": rules.rubric_version})

    # 3. Model (advisory). Precomputed results stand in for the public demo samples.
    outcome: AssessOutcome | None = None
    if precomputed is not None:
        status, m_tier, m_json = "precomputed", model_tier(precomputed), precomputed.model_dump()
        model_name = "precomputed"
    elif use_model:
        outcome = assess(form, red.text, client=client, rubric=rubric)
        status = outcome.status
        m_tier = outcome.tier
        m_json = outcome.assessment.model_dump() if outcome.assessment else None
        model_name = outcome.model
    else:
        status, m_tier, m_json, model_name = skip_reason, None, None, None

    db.log_event(conn, request_id, "model" if m_json else "system", "model_assessed", {
        "status": status, "model": model_name, "tier": m_tier,
        "attempts": outcome.attempts if outcome else None,
        "errors": outcome.errors if outcome else [],
        "output": m_json,
    })

    # 4. Merge: the model can raise, never lower.
    merged = merge(rules.tier, m_tier)
    db.log_event(conn, request_id, "system", "tier_final", {
        "rule_tier": merged.rule_tier, "model_tier": merged.model_tier,
        "final_tier": merged.final_tier, "source": merged.source,
        "model_below_floor": merged.model_below_floor,
    })

    # 5. Value and priority.
    hours = hours_saved_per_year(form, rubric)
    dollars = dollar_value_per_year(form, rubric)
    quad = priority_quadrant(hours, merged.final_tier, rubric)

    assessment_id = db.insert_assessment(
        conn, request_id,
        rubric_version=rubric.version, rule_tier=rules.tier, rule_json=rule_json(rules),
        model_status=status, model_name=model_name, model_json=m_json, model_tier=m_tier,
        final_tier=merged.final_tier, merge_source=merged.source,
        hours_saved=hours, dollar_value=dollars, quadrant=quad,
        input_tokens=outcome.input_tokens if outcome else None,
        output_tokens=outcome.output_tokens if outcome else None,
    )
    controls = rubric.controls[merged.final_tier]
    db.log_event(conn, request_id, "system", "routed_for_review", {
        "assessment_id": assessment_id, "final_tier": merged.final_tier,
        "review": controls.review, "hours_saved": hours, "dollar_value": dollars, "quadrant": quad,
    })

    return IntakeResult(
        request_id=request_id, assessment_id=assessment_id, redaction=red, rules=rules,
        model=outcome, model_status=status, merged=merged, hours_saved=hours,
        dollar_value=dollars, quadrant=quad, controls=list(controls.required), review=controls.review,
    )
