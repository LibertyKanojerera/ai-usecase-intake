"""Submit page: the twelve-field intake form and the assessment result."""
import json

import streamlit as st
from pydantic import ValidationError

from intake import db, ui
from intake.config import IntakeForm, load_rubric
from intake.llm import api_key_available
from intake.pipeline import IntakeResult, run_intake

ui.banners()
conn, lock = ui.database()
rubric = load_rubric()
opts = rubric.form_options
v = rubric.value

TOOL_PATH_LABELS = {
    "prompt_template": "Prompt library template (self-serve)",
    "custom_gpt_or_agent": "Configured assistant or agent",
    "workflow_automation": "Workflow automation",
    "custom_build": "Custom build",
    "not_an_ai_problem": "Not an AI problem",
}
QUADRANT_LABELS = {k: q.label for k, q in rubric.priority.quadrants.items()}
STATUS_TEXT = {
    "ok": "Model assessment received.",
    "no_key": "No API key: scored by rules alone.",
    "failed": "The model call failed twice: scored by rules alone. The failure is in the audit log.",
    "skipped_cap": "Live model calls for this session are used up: scored by rules alone.",
    "precomputed": "Cached model assessment.",
    "not_run": "Model not run: scored by rules alone.",
}


def code_select(label, field, key, help=None, index=0):
    codes = list(opts[field])
    return st.selectbox(label, codes, index=index, key=key, help=help,
                        format_func=lambda c: opts[field][c])


st.title("Submit an AI use case")
st.caption("Describe what you want AI to help with. The rules can score most of this without a model, "
           "so please answer every question.")

# ------------------------------------------------------------------ form ---
with st.form("intake", clear_on_submit=False):
    submitter = st.text_input("Your name (demo only; use a made-up name)", value="demo-submitter")
    title = st.text_input("1. Title", placeholder="e.g. Summarize monthly stock reports")
    team = code_select("2. Requesting team", "team", "team")
    task = st.text_area(
        "3. What task do you want AI to help with?",
        height=140,
        help="Describe the task and the data in general terms. Do not paste the data itself.",
    )
    data_types = st.multiselect("4. Data the tool would touch", list(opts["data_types"]),
                                format_func=lambda c: opts["data_types"][c])
    c1, c2 = st.columns(2)
    with c1:
        destination = code_select("5. Where would the data go?", "destination", "destination")
        audience = code_select("6. Who sees the output?", "audience", "audience")
        human_review = code_select("8. Does a person review every output before use?",
                                   "human_review", "human_review")
    with c2:
        decision = st.radio("7. Does the output inform a decision about a person?", ["No", "Yes"],
                            horizontal=True)
        decision_type = code_select("7a. If yes, which kind of decision?", "decision_type",
                                    "decision_type",
                                    help="Only used when the answer to question 7 is Yes.")
        countries = st.multiselect("9. Countries involved", ui.COUNTRIES)
        eu_uk = st.checkbox("EU or UK people involved (staff, applicants, participants or users)")
    st.markdown("**Value estimate** (self-reported)")
    c3, c4, c5 = st.columns(3)
    hours = c3.number_input("10. Hours per person per week on this task now", 0.0, 60.0, 2.0, 0.5)
    people = c4.number_input("11. People who do this task", 1, 10000, 1, 1)
    share = c5.slider("12. Expected share of time saved", int(v.share_saved_min * 100),
                      int(v.share_saved_max * 100), 30, 5, format="%d%%")
    submitted = st.form_submit_button("Submit for assessment", type="primary")


# ---------------------------------------------------------------- result ---
def show_result(r: IntakeResult) -> None:
    m = r.merged
    st.divider()
    st.subheader(f"Request #{r.request_id}: {ui.tier_badge(m.final_tier)} risk")
    cols = st.columns(4)
    cols[0].metric("Final tier", ui.TIER_LABELS[m.final_tier])
    cols[1].metric("Rules floor", ui.TIER_LABELS[m.rule_tier])
    cols[2].metric("Model suggested", ui.TIER_LABELS[m.model_tier] if m.model_tier else "—")
    cols[3].metric("Status", "Pending")

    if m.model_raised:
        st.info("The model raised this request above the rules floor.")
    if m.model_below_floor:
        st.warning("The model suggested a lower tier than the written rules allow. "
                   "The rules floor stands, and this is logged for the reviewer.")
    st.caption(STATUS_TEXT.get(r.model_status, r.model_status))

    a = r.model.assessment if r.model else None
    if a and a.prohibited_practice:
        st.error(f"**Possible prohibited practice (EU AI Act Article 5):** {a.prohibited_reason} "
                 "The reviewer can only decline it.")
    if a:
        st.markdown(f"**Summary:** {a.summary}")

    # Per-dimension table: rules and model side by side.
    model_dims = {d.name: d for d in a.dimensions} if a else {}
    rows = []
    for name, dres in r.rules.dimensions.items():
        md = model_dims.get(name)
        rows.append({
            "Dimension": ui.DIMENSION_LABELS[name],
            "Rules": dres.level.title(),
            "Rule that fired": ", ".join(dres.triggered) or "—",
            "Model": md.level.title() if md else "—",
            "Model's reason": md.reason if md else "—",
        })
    st.dataframe(rows, hide_index=True, width="stretch")
    with st.expander("What each fired rule means"):
        for name, dres in r.rules.dimensions.items():
            for reason in dres.reasons:
                if dres.triggered:
                    st.markdown(f"- **{ui.DIMENSION_LABELS[name]}**: {reason}")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Required before it goes ahead**")
        st.markdown("\n".join(f"- {c}" for c in r.controls) or "- None: decline")
        st.markdown(f"**Review route:** {r.review}")
        if a:
            st.markdown(f"**Suggested tool path:** {TOOL_PATH_LABELS[a.tool_path]}")
            if a.questions_for_requester:
                st.markdown("**Questions for you**")
                st.markdown("\n".join(f"- {q}" for q in a.questions_for_requester))
    with c2:
        st.markdown("**Value estimate**")
        st.markdown(
            f"- About **{r.hours_saved:,.0f} hours a year**, worth about **${r.dollar_value:,.0f}**\n"
            f"- Priority: **{QUADRANT_LABELS.get(r.quadrant, 'Not placed (prohibited)')}**"
        )
        st.caption(f"Assumes {v.working_weeks_per_year:g} working weeks and "
                   f"${v.loaded_hourly_rate_usd:,.0f}/hour; the time saved is self-reported.")
        red = r.redaction
        engine = "Presidio" if red.engine == "presidio" else "regex fallback (names not detected)"
        st.markdown(f"**Masking:** {red.total} item(s) masked before any model call, using {engine}.")
        if red.counts:
            st.caption(", ".join(f"{k}: {n}" for k, n in sorted(red.counts.items())))

    with st.expander("Text that left the app (masked)"):
        st.code(red.text, language=None)
    with st.expander("Audit trail for this request"):
        with lock:
            trail = db.audit_trail(conn, r.request_id)
        st.dataframe([{"Time (UTC)": e["ts"], "Actor": e["actor"], "Event": e["event"],
                       "Detail": e["detail"]} for e in trail],
                     hide_index=True, width="stretch")


if submitted:
    # A selected EU or UK country always counts, even if the box was missed.
    eu_auto = bool(set(countries) & ui.EU_UK_COUNTRIES) and not eu_uk
    try:
        form = IntakeForm(
            title=title.strip(), team=team, task_description=task.strip(), data_types=data_types,
            destination=destination, audience=audience,
            decision_about_person=(decision == "Yes"),
            decision_type=decision_type if decision == "Yes" else None,
            human_review=human_review, countries=countries, eu_uk_involved=eu_uk or eu_auto,
            hours_per_week=hours, people=int(people), share_saved=share / 100,
        )
    except ValidationError as exc:
        for err in exc.errors():
            field = ".".join(str(p) for p in err["loc"]) or "form"
            st.error(f"{field}: {err['msg']}")
        st.stop()

    if not submitter.strip():
        st.error("Please enter a (made-up) name.")
        st.stop()
    if eu_auto:
        st.info("An EU or UK country was selected, so 'EU or UK people involved' was set to yes.")

    use_model = api_key_available() and ui.live_calls_left() > 0
    with st.spinner("Masking, scoring and assessing…"):
        with lock:
            result = run_intake(conn, form, submitted_by=submitter.strip(), use_model=use_model,
                                skip_reason="skipped_cap" if api_key_available() else "no_key")
    if result.model is not None and result.model.attempts:
        ui.count_live_call()
    st.session_state["last_result"] = result

if "last_result" in st.session_state:
    show_result(st.session_state["last_result"])
    if api_key_available():
        st.caption(f"Live model calls left this session: {max(ui.live_calls_left(), 0)}")
