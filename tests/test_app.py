"""Smoke tests that run the real Streamlit pages headlessly (rules-only mode)."""
import pytest
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent

TIMEOUT = 120


@pytest.fixture(autouse=True)
def no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def all_text(at) -> str:
    parts = [e.value for e in at.markdown] + [e.value for e in at.warning] + \
            [e.value for e in at.info] + [e.value for e in at.error] + [e.value for e in at.caption]
    return "\n".join(str(p) for p in parts)


def test_home_page_loads_with_banners_and_seeded_samples():
    at = AppTest.from_file(str(ROOT / "app_pages" / "0_Home.py"), default_timeout=TIMEOUT).run()
    assert not at.exception
    text = all_text(at)
    assert "Demo only" in text and "Rules-only mode" in text
    assert at.metric[0].value == "11"


def test_submit_page_scores_a_high_request():
    at = AppTest.from_file(str(ROOT / "app_pages" / "1_Submit.py"), default_timeout=TIMEOUT).run()
    assert not at.exception
    at.text_input[1].input("Fix formulas in a donor budget")
    at.text_area[0].input("Paste a donor-restricted budget into a free chatbot to fix formulas. "
                          "Questions to maria.fernandes@example.org.")
    at.multiselect[0].select("donor_restricted")
    at.selectbox[1].select("consumer_free")
    at.button[0].click().run()
    assert not at.exception
    text = all_text(at)
    assert "Rules-only" in text
    assert at.metric[0].value == "High"
    assert "Data protection review with InfoSec and Legal" in text
    # The masked text is shown; the raw email is not.
    codes = "\n".join(c.value for c in at.code)
    assert "maria.fernandes@example.org" not in codes and "<EMAIL_ADDRESS>" in codes


def test_eu_country_sets_the_eu_flag():
    at = AppTest.from_file(str(ROOT / "app_pages" / "1_Submit.py"), default_timeout=TIMEOUT).run()
    at.text_input[1].input("Rank applicant CVs")
    at.text_area[0].input("Score and rank CVs for a program officer vacancy.")
    at.multiselect[0].select("staff_personal")
    at.radio[0].set_value("Yes")
    at.selectbox[4].select("hiring")
    at.multiselect[1].select("Belgium")
    at.button[0].click().run()
    assert not at.exception
    assert "set to yes" in all_text(at)
    assert at.metric[0].value == "High"


def test_missing_title_shows_an_error():
    at = AppTest.from_file(str(ROOT / "app_pages" / "1_Submit.py"), default_timeout=TIMEOUT).run()
    at.text_input[1].input("")
    at.button[0].click().run()
    assert not at.exception
    assert at.error


def test_entry_point_routes_to_home():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=TIMEOUT).run()
    assert not at.exception
    assert "Demo only" in all_text(at)


def test_review_page_records_a_decision():
    at = AppTest.from_file(str(ROOT / "app_pages" / "2_Review.py"), default_timeout=TIMEOUT).run()
    assert not at.exception
    at.selectbox[0].select(1)          # sample 1 is Low: one-click approve
    at.run()
    at.button[0].click().run()         # "Record decision" with defaults (approve, agree)
    assert not at.exception
    assert not at.error, [e.value for e in at.error]
    assert at.metric[3].value == "Approved"


def test_review_page_blocks_high_approval_without_signoff():
    at = AppTest.from_file(str(ROOT / "app_pages" / "2_Review.py"), default_timeout=TIMEOUT).run()
    at.selectbox[0].select(8)          # sample 8 is High
    at.run()
    at.text_input[2].input("")         # InfoSec sign-off left empty
    at.button[0].click().run()
    assert any("InfoSec" in e.value for e in at.error)


def test_review_passcode_gate(monkeypatch):
    monkeypatch.setenv("REVIEWER_PASSCODE", "letmein")
    at = AppTest.from_file(str(ROOT / "app_pages" / "2_Review.py"), default_timeout=TIMEOUT).run()
    assert not at.dataframe            # queue hidden until the passcode is entered
    at.text_input[0].input("wrong").run()
    assert at.error
    at.text_input[0].input("letmein").run()
    assert at.dataframe


def test_portfolio_page_loads():
    at = AppTest.from_file(str(ROOT / "app_pages" / "3_Portfolio.py"), default_timeout=TIMEOUT).run()
    assert not at.exception
    text = all_text(at)
    assert "Fast track" in text and "Park" in text
