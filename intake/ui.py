"""Shared Streamlit setup: banners, database, secrets, sample seeding, call cap."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import streamlit as st

from intake import db, redact
from intake.config import load_rubric
from intake.llm import Assessment, api_key_available
from intake.pipeline import run_intake
from intake.samples import load_samples

PRECOMPUTED_PATH = Path(__file__).resolve().parent.parent / "data" / "precomputed.json"
MAX_LIVE_CALLS_PER_SESSION = 3

TIER_COLORS = {"low": "green", "medium": "orange", "high": "red", "prohibited": "violet"}
TIER_LABELS = {"low": "Low", "medium": "Medium", "high": "High", "prohibited": "Prohibited"}
DIMENSION_LABELS = {k: v.label for k, v in load_rubric().dimensions.items()}

EU_UK_COUNTRIES = {
    "Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czechia", "Denmark", "Estonia",
    "Finland", "France", "Germany", "Greece", "Hungary", "Ireland", "Italy", "Latvia",
    "Lithuania", "Luxembourg", "Malta", "Netherlands", "Poland", "Portugal", "Romania",
    "Slovakia", "Slovenia", "Spain", "Sweden", "United Kingdom",
}
OTHER_COUNTRIES = {
    "United States", "Kenya", "Ethiopia", "Malawi", "Zambia", "Mozambique", "Uganda",
    "Tanzania", "Nigeria", "Ghana", "Zimbabwe", "South Africa", "Rwanda", "Haiti",
    "Democratic Republic of the Congo", "India", "Bangladesh", "Pakistan", "Philippines",
    "Guatemala", "Switzerland",
}
COUNTRIES = sorted(EU_UK_COUNTRIES | OTHER_COUNTRIES)


def tier_badge(tier: str) -> str:
    return f":{TIER_COLORS[tier]}-background[**{TIER_LABELS[tier]}**]"


# ----------------------------------------------------------------- secrets ---
def load_api_key() -> None:
    """Copy the key from Streamlit secrets into the environment, if present."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    try:
        key = st.secrets.get("ANTHROPIC_API_KEY")
    except Exception:  # no secrets file
        key = None
    if key:
        os.environ["ANTHROPIC_API_KEY"] = key


def banners() -> None:
    """Call at the top of every page."""
    load_api_key()
    st.warning("**Demo only.** Do not enter real data. The organization is fictional and all "
               "requests are synthetic. Describe your data; never paste it.", icon="⚠️")
    if not api_key_available():
        st.info("**Rules-only mode.** No model API key is configured, so requests are scored by the "
                "written rules alone. Tiers are still floors set by policy; the model's per-dimension "
                "reasons, tool path and questions are not available.", icon="🧭")


# -------------------------------------------------------------- resources ---
@st.cache_resource(show_spinner="Loading the personal-data masker…")
def presidio_ready() -> bool:
    return redact.presidio_available()


def _load_precomputed() -> dict[int, Assessment]:
    if not PRECOMPUTED_PATH.exists():
        return {}
    raw = json.loads(PRECOMPUTED_PATH.read_text(encoding="utf-8"))
    return {int(k): Assessment.model_validate(v) for k, v in raw.items()}


@st.cache_resource(show_spinner="Setting up the demo database…")
def database():
    """One shared in-memory database, seeded with the 11 samples on startup."""
    presidio_ready()
    conn = db.connect(":memory:")
    lock = threading.Lock()
    precomputed = _load_precomputed()
    with lock:
        for s in load_samples():
            pre = precomputed.get(s.id)
            run_intake(conn, s.form, submitted_by="sample-data", source="sample", sample_id=s.id,
                       precomputed=pre, use_model=False, skip_reason="not_run")
    return conn, lock


# ------------------------------------------------------------- call cap ---
def live_calls_left() -> int:
    return MAX_LIVE_CALLS_PER_SESSION - st.session_state.get("live_calls", 0)


def count_live_call() -> None:
    st.session_state["live_calls"] = st.session_state.get("live_calls", 0) + 1
