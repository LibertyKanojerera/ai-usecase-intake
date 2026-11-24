"""Review page: the queue, one request in detail, and the reviewer's decision."""
import hmac
import json

import streamlit as st

from intake import db, ui
from intake.config import load_rubric
from intake.records import DECISION_LABELS, decision_record_markdown

ui.banners()
conn, lock = ui.database()
rubric = load_rubric()

st.title("Review queue")
st.caption("A person decides every request. Reviewers are separate from submitters, and every "
           "decision, including any disagreement with the model, is added to the audit log.")

# ------------------------------------------------------------- passcode ---
passcode = ui.secret("REVIEWER_PASSCODE")
if passcode and not st.session_state.get("reviewer_ok"):
    entered = st.text_input("Reviewer passcode", type="password")
    if entered and hmac.compare_digest(entered, passcode):
        st.session_state["reviewer_ok"] = True
        st.rerun()
    elif entered:
        st.error("Wrong passcode.")
    st.stop()
if not passcode:
    st.caption("Demo mode: the Review page is open to everyone. A real deployment would sign "
               "reviewers in with single sign-on.")

reviewer = st.text_input("Reviewer name (made up)", value="demo-reviewer")

# ---------------------------------------------------------------- queue ---
with lock:
    queue = db.queue(conn)

STATUS_LABELS = {"pending_review": "Pending review", **{v: v.replace("_", " ").capitalize()
                 for v in db.STATUS_FOR_DECISION.values()}}
f1, f2 = st.columns(2)
tier_filter = f1.multiselect("Tier", rubric.tiers, format_func=ui.TIER_LABELS.get)
status_filter = f2.multiselect("Status", list(STATUS_LABELS), format_func=STATUS_LABELS.get)
rows = [r for r in queue
        if (not tier_filter or r["final_tier"] in tier_filter)
        and (not status_filter or r["status"] in status_filter)]

st.dataframe(
    [{"#": r["id"], "Title": r["title"], "Team": rubric.form_options["team"][r["team"]],
      "Final tier": ui.TIER_LABELS[r["final_tier"]], "Status": STATUS_LABELS[r["status"]],
      "Rules": ui.TIER_LABELS[r["rule_tier"]], "Model": ui.TIER_LABELS.get(r["model_tier"], "—"),
      "Hours/yr": round(r["hours_saved"]), "Source": r["source"]} for r in rows],
    hide_index=True, width="stretch",
)
if not rows:
    st.stop()

ids = [r["id"] for r in rows]
default = st.session_state.get("review_id")
selected = st.selectbox("Open a request", ids, index=ids.index(default) if default in ids else 0,
                        format_func=lambda i: f"#{i}: {next(r['title'] for r in rows if r['id'] == i)}")
st.session_state["review_id"] = selected

with lock:
    req = db.get_request(conn, selected)
    a = db.latest_assessment(conn, selected)
    current = db.status(conn, selected)
form = json.loads(req["form_json"])
rules = json.loads(a["rule_json"])
model = json.loads(a["model_json"]) if a["model_json"] else None
tier = a["final_tier"]
controls = rubric.controls[tier]

# --------------------------------------------------------------- detail ---
st.divider()
st.subheader(f"#{req['id']} {req['title']}: {ui.tier_badge(tier)}")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Final tier", ui.TIER_LABELS[tier])
m2.metric("Rules floor", ui.TIER_LABELS[a["rule_tier"]])
m3.metric("Model", ui.TIER_LABELS.get(a["model_tier"], "—"))
m4.metric("Status", STATUS_LABELS[current].split(" (")[0])

if a["model_tier"] and ui.rank(a["model_tier"]) < ui.rank(a["rule_tier"]):
    st.warning("The model suggested a lower tier than the rules allow. The rules floor stands.")
if model and model["prohibited_practice"]:
    st.error(f"**Possible prohibited practice:** {model['prohibited_reason']} This can only be declined.")

st.markdown("**Request (masked)**")
st.code(req["masked_description"], language=None)
if model:
    st.markdown(f"**Model summary:** {model['summary']}")

model_dims = {d["name"]: d for d in model["dimensions"]} if model else {}
st.dataframe(
    [{"Dimension": ui.DIMENSION_LABELS[n], "Rules": d["level"].title(),
      "Rule fired": ", ".join(d["triggered"]) or "—",
      "Model": model_dims[n]["level"].title() if n in model_dims else "—",
      "Model's reason": model_dims[n]["reason"] if n in model_dims else "—"}
     for n, d in ((n, rules["dimensions"][n]) for n in rubric.dimensions)],
    hide_index=True, width="stretch",
)

c1, c2 = st.columns(2)
with c1:
    st.markdown("**Required controls**")
    st.markdown("\n".join(f"- {c}" for c in controls.required) or "- None: decline")
    st.markdown(f"**Review route:** {controls.review}")
with c2:
    st.markdown("**Value**")
    q = a["quadrant"]
    st.markdown(f"- {a['hours_saved']:,.0f} hours a year, about ${a['dollar_value']:,.0f}\n"
                f"- Priority: {rubric.priority.quadrants[q].label if q else 'not placed (prohibited)'}")
    if model and model["questions_for_requester"]:
        st.markdown("**Model's questions for the requester**")
        st.markdown("\n".join(f"- {x}" for x in model["questions_for_requester"]))

# ------------------------------------------------------------- decision ---
st.divider()
st.subheader("Decision")
actions = controls.reviewer_actions
with st.form(f"decide_{selected}", clear_on_submit=True):
    decision = st.radio("Decision", actions, format_func=DECISION_LABELS.get, horizontal=True)
    conditions = st.text_area("Conditions (required to approve with conditions)",
                              value="\n".join(controls.required) if "approve_with_conditions" in actions else "")
    agrees = st.radio("Do you agree with the assessed tier and reasons?", ["Yes", "No"],
                      horizontal=True) == "Yes"
    reason = st.text_input("If not, why? (logged)")
    infosec = legal = ""
    if controls.signoffs_required:
        s1, s2 = st.columns(2)
        infosec = s1.text_input("InfoSec sign-off (name and date)")
        legal = s2.text_input("Legal sign-off (name and date)")
    note = st.text_input("Note to the requester (required to decline or ask for information)")
    go = st.form_submit_button("Record decision", type="primary")

if go:
    try:
        with lock:
            db.record_decision(conn, selected, reviewer=reviewer.strip(), decision=decision,
                               agrees_with_model=agrees, conditions=conditions.strip(),
                               disagreement_reason=reason.strip(), infosec_signoff=infosec.strip(),
                               legal_signoff=legal.strip(), note=note.strip())
        st.success(f"Recorded: {DECISION_LABELS[decision]}.")
        st.rerun()
    except db.DecisionError as exc:
        st.error(str(exc))

with lock:
    record = decision_record_markdown(conn, selected)
    trail = db.audit_trail(conn, selected)
st.download_button("Download decision record (Markdown)", record,
                   file_name=f"decision-record-{selected}.md", mime="text/markdown")
with st.expander("Audit trail"):
    st.dataframe([{"Time (UTC)": e["ts"], "Actor": e["actor"], "Event": e["event"],
                   "Detail": e["detail"]} for e in trail], hide_index=True, width="stretch")
