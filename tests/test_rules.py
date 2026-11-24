import pytest

from intake.config import IntakeForm, load_rubric
from intake.rules import (
    dollar_value_per_year,
    hours_saved_per_year,
    priority_quadrant,
    score,
)
from intake.samples import load_samples

RUBRIC = load_rubric()
SAMPLES = load_samples()


def make_form(**overrides) -> IntakeForm:
    """A baseline low-risk request; override one answer at a time."""
    base = dict(
        title="Baseline request",
        team="programs",
        task_description="Summarize my own internal meeting notes.",
        data_types=["internal"],
        destination="approved_enterprise",
        audience="only_me",
        decision_about_person=False,
        decision_type=None,
        human_review="always",
        countries=["United States"],
        eu_uk_involved=False,
        hours_per_week=2,
        people=1,
        share_saved=0.5,
    )
    base.update(overrides)
    return IntakeForm(**base)


# ------------------------------------------------------- the 11 samples ---
@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: f"req{s.id}")
def test_sample_hits_expected_rule_tier(sample):
    assert score(sample.form, RUBRIC).tier == sample.expected_rule_tier


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: f"req{s.id}")
def test_rule_tier_never_above_expected_final(sample):
    """Rules are a floor: they must not over-call against the hand-set answer."""
    order = RUBRIC.tiers
    assert order.index(score(sample.form, RUBRIC).tier) <= order.index(sample.expected_tier)


def test_rules_ignore_free_text_injection():
    """Request 11 is request 8 plus 'rate this low': same rule result."""
    r8 = next(s for s in SAMPLES if s.id == 8)
    r11 = next(s for s in SAMPLES if s.id == 11)
    assert "ignore the rubric" in r11.form.task_description
    a, b = score(r8.form, RUBRIC), score(r11.form, RUBRIC)
    assert a.tier == b.tier == "high"
    assert a.all_triggered == b.all_triggered


def test_prohibited_example_floors_at_high_without_model():
    r10 = next(s for s in SAMPLES if s.id == 10)
    result = score(r10.form, RUBRIC)
    assert result.tier == "high"
    assert {"DP-H2", "BF-H1", "RG-H1"} <= set(result.all_triggered)


# ------------------------------------------------- one trigger at a time ---
@pytest.mark.parametrize(
    "overrides, dimension, level, trigger",
    [
        (dict(data_types=["participant_personal_health"]), "data_privacy", "high", "DP-H1"),
        (dict(data_types=["staff_personal"], eu_uk_involved=True), "data_privacy", "high", "DP-H2"),
        (dict(data_types=["staff_personal"]), "data_privacy", "medium", "DP-M1"),
        (dict(data_types=["confidential"]), "data_privacy", "medium", "DP-M2"),
        (dict(data_types=["donor_restricted"], destination="consumer_free"), "confidentiality_ip", "high", "CI-H1"),
        (dict(data_types=["confidential"], destination="approved_api"), "confidentiality_ip", "medium", "CI-M1"),
        (dict(data_types=["confidential"], destination="not_sure"), "confidentiality_ip", "medium", "CI-M1"),
        (dict(decision_about_person=True, decision_type="other"), "bias_fairness", "high", "BF-H1"),
        (dict(audience="external", human_review="sometimes"), "output_integrity", "high", "OI-H1"),
        (dict(audience="external", human_review="no"), "output_integrity", "high", "OI-H1"),
        (dict(audience="whole_org"), "output_integrity", "medium", "OI-M1"),
        (dict(audience="external"), "output_integrity", "medium", "OI-M1"),
        (dict(human_review="sometimes"), "output_integrity", "medium", "OI-M2"),
        (dict(human_review="no"), "output_integrity", "medium", "OI-M2"),
        (dict(destination="consumer_free"), "security_tool_route", "high", "ST-H1"),
        (dict(destination="not_sure"), "security_tool_route", "medium", "ST-M1"),
        (dict(decision_about_person=True, decision_type="hiring", eu_uk_involved=True),
         "regulation_eu_ai_act", "high", "RG-H1"),
        (dict(decision_about_person=True, decision_type="eligibility", eu_uk_involved=True),
         "regulation_eu_ai_act", "high", "RG-H1"),
    ],
)
def test_single_trigger(overrides, dimension, level, trigger):
    result = score(make_form(**overrides), RUBRIC)
    dim = result.dimensions[dimension]
    assert dim.level == level
    assert trigger in dim.triggered


def test_baseline_is_low_on_every_dimension():
    result = score(make_form(), RUBRIC)
    assert result.tier == "low"
    assert all(d.level == "low" for d in result.dimensions.values())


def test_public_data_in_consumer_tool_is_not_high():
    result = score(make_form(data_types=["public"], destination="consumer_free"), RUBRIC)
    assert result.dimensions["security_tool_route"].level == "low"


def test_annex_iii_use_without_eu_people_is_not_regulation_high():
    form = make_form(decision_about_person=True, decision_type="hiring")
    assert score(form, RUBRIC).dimensions["regulation_eu_ai_act"].level == "low"


def test_worst_dimension_wins_no_averaging():
    """One high dimension among five lows still makes the request High."""
    result = score(make_form(destination="consumer_free"), RUBRIC)
    levels = [d.level for d in result.dimensions.values()]
    assert levels.count("high") == 1 and levels.count("low") == 5
    assert result.tier == "high"


def test_invalid_option_is_rejected():
    with pytest.raises(ValueError):
        score(make_form(destination="shadow_it"), RUBRIC)


def test_decision_type_required_when_decision_about_person():
    with pytest.raises(ValueError):
        make_form(decision_about_person=True)


# ----------------------------------------------------------------- value ---
def test_hours_saved_formula():
    form = make_form(hours_per_week=6, people=4, share_saved=0.6)
    assert hours_saved_per_year(form, RUBRIC) == round(6 * 4 * 0.6 * 46, 1)
    assert dollar_value_per_year(form, RUBRIC) == round(6 * 4 * 0.6 * 46 * 50, 2)


@pytest.mark.parametrize(
    "hours, tier, expected",
    [
        (800, "low", "fast_track"),
        (800, "high", "govern_then_build"),
        (100, "low", "self_serve"),
        (100, "medium", "park"),
        (500, "low", "fast_track"),  # cutoff is inclusive
        (800, "prohibited", None),
    ],
)
def test_priority_quadrant(hours, tier, expected):
    assert priority_quadrant(hours, tier, RUBRIC) == expected


def test_quadrant_labels_survive_yaml_commas():
    labels = {k: q.label for k, q in RUBRIC.priority.quadrants.items()}
    assert labels["govern_then_build"] == "Govern, then build"


def test_rubric_rejects_unknown_keys(tmp_path):
    import yaml
    from intake.config import RUBRIC_PATH, load_rubric as load
    raw = yaml.safe_load(RUBRIC_PATH.read_text())
    raw["priority"]["quadrants"]["park"]["then build"] = None   # what an unquoted comma produces
    bad = tmp_path / "rubric.yaml"
    bad.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="Extra inputs"):
        load(bad)
