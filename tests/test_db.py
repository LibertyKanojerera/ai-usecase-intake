import sqlite3

import pytest

from intake import db
from intake.pipeline import run_intake
from intake.samples import load_samples
from tests.fakes import FakeClient, make_assessment, response

SAMPLES = {s.id: s for s in load_samples()}


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def submit(conn, sample_id, **kw):
    kw.setdefault("use_model", False)
    return run_intake(conn, SAMPLES[sample_id].form, submitted_by="amina", source="sample",
                      sample_id=sample_id, force_redaction_fallback=True, **kw)


# ------------------------------------------------------------ insert-only ---
@pytest.mark.parametrize("table", db.TABLES)
def test_every_table_rejects_update_and_delete(conn, table):
    submit(conn, 1)
    db.record_decision(conn, 1, reviewer="rev", decision="approve", agrees_with_model=True)
    with pytest.raises(sqlite3.IntegrityError, match="insert-only"):
        conn.execute(f"UPDATE {table} SET id = id WHERE 1")
    with pytest.raises(sqlite3.IntegrityError, match="insert-only"):
        conn.execute(f"DELETE FROM {table}")


def test_raw_description_is_never_stored(conn):
    form = SAMPLES[1].form.model_copy(
        update={"task_description": "Notes for maria.fernandes@example.org, phone +1 202 555 0147."})
    run_intake(conn, form, submitted_by="amina", use_model=False, force_redaction_fallback=True)
    dump = "\n".join(conn.iterdump())
    assert "maria.fernandes@example.org" not in dump
    assert "555 0147" not in dump


# --------------------------------------------------------------- decisions ---
def test_status_follows_latest_decision_without_overwriting(conn):
    submit(conn, 3)  # medium
    assert db.status(conn, 1) == "pending_review"
    db.record_decision(conn, 1, reviewer="rev", decision="request_info", agrees_with_model=True,
                       note="Which languages and who checks them?")
    db.record_decision(conn, 1, reviewer="rev", decision="approve_with_conditions",
                       agrees_with_model=True, conditions="Bilingual staff check every slide")
    assert db.status(conn, 1) == "approved_with_conditions"
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 2


def test_submitter_cannot_review_own_request(conn):
    submit(conn, 1)
    with pytest.raises(db.DecisionError, match="own request"):
        db.record_decision(conn, 1, reviewer="amina", decision="approve", agrees_with_model=True)


def test_one_click_approve_only_for_low(conn):
    submit(conn, 3)  # medium
    with pytest.raises(db.DecisionError, match="not allowed"):
        db.record_decision(conn, 1, reviewer="rev", decision="approve", agrees_with_model=True)


def test_medium_needs_named_conditions(conn):
    submit(conn, 5)
    with pytest.raises(db.DecisionError, match="conditions"):
        db.record_decision(conn, 1, reviewer="rev", decision="approve_with_conditions",
                           agrees_with_model=True)


def test_high_needs_infosec_and_legal_signoff(conn):
    submit(conn, 8)
    with pytest.raises(db.DecisionError, match="InfoSec"):
        db.record_decision(conn, 1, reviewer="rev", decision="approve_with_conditions",
                           agrees_with_model=True, conditions="Use the enterprise tool instead")
    db.record_decision(conn, 1, reviewer="rev", decision="approve_with_conditions",
                       agrees_with_model=True, conditions="Use the enterprise tool instead",
                       infosec_signoff="J. Okoro, 2026-11-24", legal_signoff="P. Mensah, 2026-11-24")
    assert db.status(conn, 1) == "approved_with_conditions"


def test_prohibited_can_only_be_declined(conn):
    client = FakeClient([response(make_assessment(
        {"data_privacy": "high", "bias_fairness": "high"}, prohibited=True))])
    submit(conn, 10, use_model=True, client=client)
    assert db.latest_assessment(conn, 1)["final_tier"] == "prohibited"
    with pytest.raises(db.DecisionError, match="not allowed"):
        db.record_decision(conn, 1, reviewer="rev", decision="approve_with_conditions",
                           agrees_with_model=True, conditions="x", infosec_signoff="x", legal_signoff="x")
    db.record_decision(conn, 1, reviewer="rev", decision="decline", agrees_with_model=True,
                       note="Emotion recognition at work is banned under Article 5.")
    assert db.status(conn, 1) == "declined"


def test_disagreement_needs_a_reason_and_is_logged(conn):
    submit(conn, 1)
    with pytest.raises(db.DecisionError, match="reason"):
        db.record_decision(conn, 1, reviewer="rev", decision="approve", agrees_with_model=False)
    db.record_decision(conn, 1, reviewer="rev", decision="approve", agrees_with_model=False,
                       disagreement_reason="Model under-weighted the shared drive location")
    last = db.audit_trail(conn, 1)[-1]
    assert last["event"] == "decision" and "under-weighted" in last["detail"]


def test_queue_lists_requests_with_status(conn):
    for sid in (1, 5, 8):
        submit(conn, sid)
    q = db.queue(conn)
    assert [r["final_tier"] for r in q] == ["low", "medium", "high"]
    assert all(r["status"] == "pending_review" for r in q)
