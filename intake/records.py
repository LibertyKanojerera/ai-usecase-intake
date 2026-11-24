"""Build a Markdown decision record for one request from the stored rows."""
from __future__ import annotations

import json

from intake import db
from intake.config import load_rubric

TIER_LABELS = {"low": "Low", "medium": "Medium", "high": "High", "prohibited": "Prohibited"}
DECISION_LABELS = {
    "approve": "Approved",
    "approve_with_conditions": "Approved with conditions",
    "request_info": "More information requested",
    "decline": "Declined",
}


def _lv(level: str | None) -> str:
    return level.title() if level else "—"


def decision_record_markdown(conn, request_id: int) -> str:
    rubric = load_rubric()
    req = db.get_request(conn, request_id)
    if req is None:
        raise ValueError(f"no request {request_id}")
    a = db.latest_assessment(conn, request_id)
    form = json.loads(req["form_json"])
    rules = json.loads(a["rule_json"])
    model = json.loads(a["model_json"]) if a["model_json"] else None
    model_dims = {d["name"]: d for d in model["dimensions"]} if model else {}
    opts = rubric.form_options
    tier = a["final_tier"]

    lines = [
        f"# Decision record: request #{req['id']}",
        "",
        "_Demo only. Synthetic request, fictional organization._",
        "",
        f"**Title:** {req['title']}  ",
        f"**Team:** {opts['team'].get(req['team'], req['team'])}  ",
        f"**Submitted:** {req['created_at']} by {req['submitted_by']}  ",
        f"**Rubric version:** {a['rubric_version']}  ",
        f"**Status:** {db.status(conn, request_id).replace('_', ' ')}",
        "",
        "## Request (masked)",
        "",
        "> " + req["masked_description"].replace("\n", "\n> "),
        "",
        f"Masking: {req['redaction_engine']}, counts {req['redaction_counts']}",
        "",
        "## Form answers",
        "",
        f"- Data: {', '.join(opts['data_types'].get(d, d) for d in form['data_types'])}",
        f"- Destination: {opts['destination'].get(form['destination'], form['destination'])}",
        f"- Audience: {opts['audience'].get(form['audience'], form['audience'])}",
        f"- Decision about a person: {'Yes, ' + opts['decision_type'].get(form['decision_type'], '') if form['decision_about_person'] else 'No'}",
        f"- Human review of every output: {opts['human_review'].get(form['human_review'], form['human_review'])}",
        f"- Countries: {', '.join(form['countries']) or '—'} (EU or UK people involved: {'yes' if form['eu_uk_involved'] else 'no'})",
        "",
        "## Assessment",
        "",
        f"- **Final tier: {TIER_LABELS[tier]}**",
        f"- Rules floor: {TIER_LABELS[a['rule_tier']]}",
        f"- Model: {TIER_LABELS.get(a['model_tier'], '—')} ({a['model_status']})",
        f"- Merge: {a['merge_source']}",
        "",
        "| Dimension | Rules | Rule fired | Model | Model's reason |",
        "|---|---|---|---|---|",
    ]
    for name in rubric.dimensions:
        d = rules["dimensions"][name]
        md = model_dims.get(name, {})
        reason = (md.get("reason") or "—").replace("|", "/")
        lines.append(f"| {rubric.dimensions[name].label} | {_lv(d['level'])} | "
                     f"{', '.join(d['triggered']) or '—'} | {_lv(md.get('level'))} | {reason} |")
    if model and model.get("prohibited_practice"):
        lines += ["", f"**Possible prohibited practice:** {model['prohibited_reason']}"]

    controls = rubric.controls[tier]
    lines += ["", "## Required controls", ""]
    lines += [f"- {c}" for c in controls.required] or ["- None: decline"]
    lines += ["", f"Review route: {controls.review}", ""]

    q = a["quadrant"]
    lines += [
        "## Value",
        "",
        f"- Estimated hours saved per year: {a['hours_saved']:,.0f} (self-reported inputs)",
        f"- Estimated value per year: ${a['dollar_value']:,.0f} at ${rubric.value.loaded_hourly_rate_usd:,.0f}/hour",
        f"- Priority: {rubric.priority.quadrants[q].label if q else 'not placed (prohibited)'}",
        "",
        "## Decisions",
        "",
    ]
    decisions = conn.execute("SELECT * FROM decisions WHERE request_id = ? ORDER BY id",
                             (request_id,)).fetchall()
    if not decisions:
        lines.append("No decision yet.")
    for d in decisions:
        lines.append(f"- {d['created_at']} · **{DECISION_LABELS[d['decision']]}** by {d['reviewer']}")
        if d["conditions"]:
            lines.append(f"  - Conditions: {d['conditions']}")
        if not d["agrees_with_model"]:
            lines.append(f"  - Disagreed with the model: {d['disagreement_reason']}")
        if d["infosec_signoff"]:
            lines.append(f"  - InfoSec sign-off: {d['infosec_signoff']}")
        if d["legal_signoff"]:
            lines.append(f"  - Legal sign-off: {d['legal_signoff']}")
        if d["note"]:
            lines.append(f"  - Note: {d['note']}")

    lines += ["", "## Audit trail", "", "| Time (UTC) | Actor | Event |", "|---|---|---|"]
    for e in db.audit_trail(conn, request_id):
        lines.append(f"| {e['ts']} | {e['actor']} | {e['event']} |")
    return "\n".join(lines) + "\n"
