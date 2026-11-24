"""SQLite storage. Every table is insert-only: nothing is updated or deleted.

Four tables:
  requests     one row per submission (masked description only; raw text is never stored)
  assessments  one row per assessment run (rules, model, merge, value)
  decisions    one row per reviewer decision; the latest one sets the status
  audit_log    one row per step, in order, for every request

Database triggers reject UPDATE and DELETE on all four, so the guarantee
holds even if application code tries to change history.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from intake.config import load_rubric

TABLES = ("requests", "assessments", "decisions", "audit_log")

DECISIONS = ("approve", "approve_with_conditions", "request_info", "decline")
STATUS_FOR_DECISION = {
    "approve": "approved",
    "approve_with_conditions": "approved_with_conditions",
    "request_info": "info_requested",
    "decline": "declined",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at          TEXT NOT NULL,
    submitted_by        TEXT NOT NULL,
    source              TEXT NOT NULL CHECK (source IN ('sample', 'live')),
    sample_id           INTEGER,
    title               TEXT NOT NULL,
    team                TEXT NOT NULL,
    form_json           TEXT NOT NULL,       -- coded answers, description excluded
    masked_description  TEXT NOT NULL,
    redaction_engine    TEXT NOT NULL,
    redaction_counts    TEXT NOT NULL        -- JSON {entity: count}
);

CREATE TABLE IF NOT EXISTS assessments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id      INTEGER NOT NULL REFERENCES requests(id),
    created_at      TEXT NOT NULL,
    rubric_version  TEXT NOT NULL,
    rule_tier       TEXT NOT NULL,
    rule_json       TEXT NOT NULL,
    model_status    TEXT NOT NULL,           -- ok, no_key, failed, precomputed, skipped_cap, not_run
    model_name      TEXT,
    model_json      TEXT,
    model_tier      TEXT,
    final_tier      TEXT NOT NULL,
    merge_source    TEXT NOT NULL,
    hours_saved     REAL NOT NULL,
    dollar_value    REAL NOT NULL,
    quadrant        TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER
);

CREATE TABLE IF NOT EXISTS decisions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id           INTEGER NOT NULL REFERENCES requests(id),
    assessment_id        INTEGER NOT NULL REFERENCES assessments(id),
    created_at           TEXT NOT NULL,
    reviewer             TEXT NOT NULL,
    decision             TEXT NOT NULL CHECK (decision IN ('approve','approve_with_conditions','request_info','decline')),
    conditions           TEXT,
    agrees_with_model    INTEGER NOT NULL,
    disagreement_reason  TEXT,
    infosec_signoff      TEXT,
    legal_signoff        TEXT,
    note                 TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    request_id  INTEGER REFERENCES requests(id),
    actor       TEXT NOT NULL,
    event       TEXT NOT NULL,
    detail      TEXT NOT NULL               -- JSON
);
"""


def _insert_only_triggers() -> str:
    parts = []
    for t in TABLES:
        for op in ("UPDATE", "DELETE"):
            parts.append(
                f"CREATE TRIGGER IF NOT EXISTS {t}_no_{op.lower()} BEFORE {op} ON {t} "
                f"BEGIN SELECT RAISE(ABORT, '{t} is insert-only'); END;"
            )
    return "\n".join(parts)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _j(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA + _insert_only_triggers())
    return conn


# ----------------------------------------------------------------- writes ---
def log_event(conn, request_id: int | None, actor: str, event: str, detail: dict | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO audit_log (ts, request_id, actor, event, detail) VALUES (?,?,?,?,?)",
        (now(), request_id, actor, event, _j(detail or {})),
    )
    conn.commit()
    return cur.lastrowid


def insert_request(conn, *, submitted_by: str, source: str, form: dict, masked_description: str,
                   redaction_engine: str, redaction_counts: dict, sample_id: int | None = None) -> int:
    coded = {k: v for k, v in form.items() if k != "task_description"}
    cur = conn.execute(
        """INSERT INTO requests (created_at, submitted_by, source, sample_id, title, team, form_json,
                                 masked_description, redaction_engine, redaction_counts)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (now(), submitted_by, source, sample_id, form["title"], form["team"], _j(coded),
         masked_description, redaction_engine, _j(redaction_counts)),
    )
    conn.commit()
    return cur.lastrowid


def insert_assessment(conn, request_id: int, **f) -> int:
    cur = conn.execute(
        """INSERT INTO assessments (request_id, created_at, rubric_version, rule_tier, rule_json,
               model_status, model_name, model_json, model_tier, final_tier, merge_source,
               hours_saved, dollar_value, quadrant, input_tokens, output_tokens)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (request_id, now(), f["rubric_version"], f["rule_tier"], _j(f["rule_json"]),
         f["model_status"], f.get("model_name"),
         _j(f["model_json"]) if f.get("model_json") is not None else None,
         f.get("model_tier"), f["final_tier"], f["merge_source"], f["hours_saved"],
         f["dollar_value"], f.get("quadrant"), f.get("input_tokens"), f.get("output_tokens")),
    )
    conn.commit()
    return cur.lastrowid


class DecisionError(ValueError):
    pass


def record_decision(conn, request_id: int, *, reviewer: str, decision: str, agrees_with_model: bool,
                    conditions: str = "", disagreement_reason: str = "", infosec_signoff: str = "",
                    legal_signoff: str = "", note: str = "") -> int:
    """Validate a reviewer decision against the tier's rules, then append it."""
    req = get_request(conn, request_id)
    if req is None:
        raise DecisionError(f"no request {request_id}")
    assessment = latest_assessment(conn, request_id)
    if assessment is None:
        raise DecisionError("request has no assessment yet")
    if reviewer == req["submitted_by"]:
        raise DecisionError("the submitter cannot review their own request")

    tier = assessment["final_tier"]
    controls = load_rubric().controls[tier]
    if decision not in controls.reviewer_actions:
        raise DecisionError(f"'{decision}' is not allowed for a {tier} request; allowed: {controls.reviewer_actions}")
    if decision == "approve_with_conditions" and not conditions.strip():
        raise DecisionError("conditions are required")
    if not agrees_with_model and not disagreement_reason.strip():
        raise DecisionError("a reason is required when the reviewer disagrees with the model")
    if decision.startswith("approve") and "infosec" in controls.signoffs_required and not infosec_signoff.strip():
        raise DecisionError("High tier approval needs a recorded InfoSec sign-off")
    if decision.startswith("approve") and "legal" in controls.signoffs_required and not legal_signoff.strip():
        raise DecisionError("High tier approval needs a recorded Legal sign-off")
    if decision in ("decline", "request_info") and not (note.strip() or disagreement_reason.strip()):
        raise DecisionError("a note is required to decline or ask for more information")

    cur = conn.execute(
        """INSERT INTO decisions (request_id, assessment_id, created_at, reviewer, decision, conditions,
               agrees_with_model, disagreement_reason, infosec_signoff, legal_signoff, note)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (request_id, assessment["id"], now(), reviewer, decision, conditions or None,
         int(agrees_with_model), disagreement_reason or None, infosec_signoff or None,
         legal_signoff or None, note or None),
    )
    conn.commit()
    decision_id = cur.lastrowid
    log_event(conn, request_id, reviewer, "decision", {
        "decision_id": decision_id, "decision": decision, "final_tier": tier,
        "agrees_with_model": agrees_with_model, "disagreement_reason": disagreement_reason or None,
        "conditions": conditions or None,
    })
    return decision_id


# ------------------------------------------------------------------ reads ---
def get_request(conn, request_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()


def latest_assessment(conn, request_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM assessments WHERE request_id = ? ORDER BY id DESC LIMIT 1", (request_id,)
    ).fetchone()


def latest_decision(conn, request_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM decisions WHERE request_id = ? ORDER BY id DESC LIMIT 1", (request_id,)
    ).fetchone()


def status(conn, request_id: int) -> str:
    d = latest_decision(conn, request_id)
    return STATUS_FOR_DECISION[d["decision"]] if d else "pending_review"


def audit_trail(conn, request_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM audit_log WHERE request_id = ? ORDER BY id", (request_id,)
    ).fetchall()


def queue(conn) -> list[dict]:
    """Every request with its latest assessment and current status."""
    rows = conn.execute(
        """SELECT r.id, r.created_at, r.title, r.team, r.submitted_by, r.source,
                  a.final_tier, a.rule_tier, a.model_tier, a.model_status,
                  a.hours_saved, a.dollar_value, a.quadrant
           FROM requests r
           JOIN assessments a ON a.id = (SELECT MAX(id) FROM assessments WHERE request_id = r.id)
           ORDER BY r.id"""
    ).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        d["status"] = status(conn, row["id"])
        out.append(d)
    return out
