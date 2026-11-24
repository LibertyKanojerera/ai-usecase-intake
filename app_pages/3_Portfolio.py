"""Portfolio page: counts by tier and status, value, and the value vs risk grid."""
import streamlit as st

from intake import db, ui
from intake.config import load_rubric

ui.banners()
conn, lock = ui.database()
rubric = load_rubric()
v = rubric.value

st.title("Portfolio")
st.caption("Where the requested AI uses sit by risk and value. Prohibited requests are declined "
           "before the grid and never appear in it.")

with lock:
    q = db.queue(conn)
if not q:
    st.info("No requests yet.")
    st.stop()

# --------------------------------------------------------------- headline ---
hours = sum(r["hours_saved"] for r in q if r["final_tier"] != "prohibited")
dollars = sum(r["dollar_value"] for r in q if r["final_tier"] != "prohibited")
k1, k2, k3, k4 = st.columns(4)
k1.metric("Requests", len(q))
k2.metric("Awaiting review", sum(r["status"] == "pending_review" for r in q))
k3.metric("Estimated hours saved per year", f"{hours:,.0f}")
k4.metric("Estimated value per year", f"${dollars:,.0f}")
st.caption(f"Excludes prohibited requests. Assumes {v.working_weeks_per_year:g} working weeks and "
           f"${v.loaded_hourly_rate_usd:,.0f}/hour; time saved is self-reported.")

st.subheader("By risk tier")
cols = st.columns(len(rubric.tiers))
for col, tier in zip(cols, rubric.tiers):
    col.metric(ui.TIER_LABELS[tier], sum(r["final_tier"] == tier for r in q))

# ------------------------------------------------------------------- grid ---
st.subheader("Value vs risk")
st.caption(f"High value means {v.high_value_hours_cutoff:,.0f} hours a year or more. "
           f"Higher risk means a final tier of "
           f"{' or '.join(ui.TIER_LABELS[t] for t in rubric.priority.higher_risk_tiers)}.")

NEXT_STEP = {
    "fast_track": "Approve with standard controls and build first.",
    "govern_then_build": "Worth doing: run the tier's controls, then build.",
    "self_serve": "Point the requester to a prompt library template.",
    "park": "Little value for the risk: revisit if the use case changes.",
}
quads = rubric.priority.quadrants


def cell(key: str) -> None:
    members = [r for r in q if r["quadrant"] == key]
    with st.container(border=True):
        st.markdown(f"**{quads[key].label}** · {len(members)} request(s)")
        st.caption(NEXT_STEP[key])
        for r in sorted(members, key=lambda r: -r["hours_saved"]):
            st.markdown(f"- #{r['id']} {r['title']}: {ui.tier_badge(r['final_tier'])}, "
                        f"{r['hours_saved']:,.0f} h/yr")


st.markdown("**Higher value**")
top = st.columns(2)
with top[0]:
    cell("fast_track")
with top[1]:
    cell("govern_then_build")
st.markdown("**Lower value**")
bottom = st.columns(2)
with bottom[0]:
    cell("self_serve")
with bottom[1]:
    cell("park")
st.caption("Left column: lower risk (Low). Right column: higher risk (Medium or High).")

declined = [r for r in q if r["final_tier"] == "prohibited"]
if declined:
    st.markdown("**Outside the grid (prohibited)**: " +
                ", ".join(f"#{r['id']} {r['title']}" for r in declined))

# ----------------------------------------------------------------- status ---
st.subheader("By status")
statuses = ["pending_review", *db.STATUS_FOR_DECISION.values()]
st.dataframe(
    [{"Status": s.replace("_", " ").capitalize(),
      **{ui.TIER_LABELS[t]: sum(r["status"] == s and r["final_tier"] == t for r in q)
         for t in rubric.tiers}} for s in statuses],
    hide_index=True, width="stretch",
)
