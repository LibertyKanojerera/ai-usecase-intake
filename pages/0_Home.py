"""Home page: what the tool does, queue counts and the design principles."""
import streamlit as st

from intake import ui
from intake.config import load_rubric

ui.banners()
conn, lock = ui.database()
rubric = load_rubric()

st.title("AI Use Case Intake & Risk Check")
st.markdown(
    "Staff describe a task they want AI to help with. The tool checks it against a written "
    "rubric, estimates the hours it could save, and sends it to a person who decides. "
    "Every step is logged."
)

with lock:
    q = ui.db.queue(conn)
c1, c2, c3 = st.columns(3)
c1.metric("Requests in the queue", len(q))
c2.metric("Awaiting review", sum(r["status"] == "pending_review" for r in q))
c3.metric("Estimated hours saved per year", f"{sum(r['hours_saved'] for r in q):,.0f}")

st.subheader("How a request moves")
st.markdown(
    """
1. **Submit**: twelve questions, mostly dropdowns. Personal data in the description is masked first.
2. **Rules**: the written policy in `rubric.yaml` sets a minimum tier from the form answers.
3. **Model** (advisory): scores six dimensions with reasons, flags banned practices, suggests a tool path.
4. **Merge**: the final tier is the higher of the two. The model can raise a tier, never lower it.
5. **Review**: a person decides every request. Disagreements with the model are logged with a reason.
"""
)

st.subheader("Design principles")
st.markdown(
    """
- The model can raise a risk tier, never lower it.
- A person decides every request.
- Sensitive text is screened before any model call.
- Policy lives in configuration, not code.
- Every step is logged and nothing is overwritten.
- It still works without the model.
"""
)

with st.expander("Value assumptions (from rubric.yaml)"):
    v = rubric.value
    st.markdown(
        f"- Hours saved per year = hours per person per week × people × share saved × "
        f"**{v.working_weeks_per_year:g}** working weeks\n"
        f"- Dollar value uses a loaded rate of **${v.loaded_hourly_rate_usd:,.0f}/hour**\n"
        f"- **{v.high_value_hours_cutoff:,.0f}** hours a year or more counts as high value\n\n"
        "These are assumptions. The share of time saved is self-reported by the requester."
    )

st.caption(f"Rubric version {rubric.version}, effective {rubric.effective_date}. "
           "Mapped to NIST AI RMF 1.0 and the Generative AI Profile (NIST AI 600-1).")
