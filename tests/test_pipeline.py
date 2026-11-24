import json

import pytest

from intake import db
from intake.pipeline import run_intake
from intake.samples import load_samples
from tests.fakes import FakeClient, make_assessment, response

SAMPLES = {s.id: s for s in load_samples()}
EXPECTED_EVENTS = ["submitted", "redacted", "rules_scored", "model_assessed", "tier_final",
                   "routed_for_review"]


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def test_every_step_is_logged_in_order(conn):
    client = FakeClient([response(make_assessment())])
    r = run_intake(conn, SAMPLES[1].form, submitted_by="amina", client=client,
                   force_redaction_fallback=True)
    events = [row["event"] for row in db.audit_trail(conn, r.request_id)]
    assert events == EXPECTED_EVENTS


def test_masking_happens_before_the_model_call(conn):
    raw = "Summarize notes from my call with maria.fernandes@example.org on +1 202 555 0147."
    form = SAMPLES[1].form.model_copy(update={"task_description": raw})
    client = FakeClient([response(make_assessment())])
    run_intake(conn, form, submitted_by="amina", client=client, force_redaction_fallback=True)
    sent = json.dumps(client.calls[0], default=str)
    assert "maria.fernandes@example.org" not in sent and "555 0147" not in sent
    assert "<EMAIL_ADDRESS>" in sent and "<PHONE_NUMBER>" in sent


def test_injection_with_model_saying_low_stays_high(conn):
    """Request 11: even if the injected text fools the model into 'low', the floor holds."""
    client = FakeClient([response(make_assessment())])  # all low
    r = run_intake(conn, SAMPLES[11].form, submitted_by="amina", client=client,
                   force_redaction_fallback=True)
    assert r.rules.tier == "high" and r.merged.model_tier == "low"
    assert r.merged.final_tier == "high" and r.merged.model_below_floor
    tier_row = [e for e in db.audit_trail(conn, r.request_id) if e["event"] == "tier_final"][0]
    assert json.loads(tier_row["detail"])["model_below_floor"] is True


def test_model_raises_request_10_to_prohibited(conn):
    client = FakeClient([response(make_assessment({"bias_fairness": "high"}, prohibited=True))])
    r = run_intake(conn, SAMPLES[10].form, submitted_by="amina", client=client,
                   force_redaction_fallback=True)
    assert r.merged.final_tier == "prohibited" and r.quadrant is None
    assert r.controls == [] and "Decline" in r.review


def test_rules_only_when_model_fails(conn):
    client = FakeClient([RuntimeError("down"), RuntimeError("down")])
    r = run_intake(conn, SAMPLES[5].form, submitted_by="amina", client=client,
                   force_redaction_fallback=True)
    assert r.model_status == "failed" and r.merged.final_tier == "medium"
    assert r.merged.source == "rules"


def test_no_key_runs_on_rules(conn, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = run_intake(conn, SAMPLES[8].form, submitted_by="amina", force_redaction_fallback=True)
    assert r.model_status == "no_key" and r.merged.final_tier == "high"


def test_precomputed_result_is_used_without_a_call(conn):
    r = run_intake(conn, SAMPLES[4].form, submitted_by="amina",
                   precomputed=make_assessment({"regulation_eu_ai_act": "medium"}),
                   force_redaction_fallback=True)
    assert r.model_status == "precomputed" and r.merged.final_tier == "medium"


@pytest.mark.parametrize("sample_id", list(SAMPLES))
def test_rules_only_tier_for_each_sample(conn, sample_id):
    s = SAMPLES[sample_id]
    r = run_intake(conn, s.form, submitted_by="amina", use_model=False, force_redaction_fallback=True)
    assert r.merged.final_tier == s.expected_rule_tier


def test_value_is_stored(conn):
    r = run_intake(conn, SAMPLES[2].form, submitted_by="amina", use_model=False,
                   force_redaction_fallback=True)
    row = db.latest_assessment(conn, r.request_id)
    assert row["hours_saved"] == round(6 * 4 * 0.6 * 46, 1)
    assert row["quadrant"] == "fast_track"
