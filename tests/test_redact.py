import pytest

from intake import redact as redact_mod
from intake.redact import redact

# Synthetic values only.
SAMPLE = (
    "Please follow up with Maria Fernandes about the stock report. "
    "Her email is maria.fernandes@example.org and her phone is +1 202 555 0147."
)


@pytest.fixture(scope="module")
def presidio_result():
    if not redact_mod.presidio_available():
        pytest.skip("Presidio or en_core_web_md not installed")
    return redact(SAMPLE)


def test_presidio_masks_name_email_and_phone(presidio_result):
    r = presidio_result
    assert r.engine == "presidio"
    for secret in ("Maria", "Fernandes", "maria.fernandes@example.org", "555 0147"):
        assert secret not in r.text
    assert "<PERSON>" in r.text
    assert "<EMAIL_ADDRESS>" in r.text
    assert "<PHONE_NUMBER>" in r.text
    assert r.counts.get("PERSON", 0) >= 1
    assert r.counts.get("EMAIL_ADDRESS") == 1
    assert r.counts.get("PHONE_NUMBER") == 1


def test_presidio_keeps_non_personal_text(presidio_result):
    assert "stock report" in presidio_result.text


def test_presidio_masks_id_like_numbers():
    if not redact_mod.presidio_available():
        pytest.skip("Presidio not installed")
    r = redact("The client record number is 48213977.")
    assert "48213977" not in r.text
    assert r.counts.get("ID_NUMBER") == 1


def test_fallback_runs_when_presidio_fails_to_load(monkeypatch):
    def broken():
        raise OSError("Can't find model 'en_core_web_md'")

    monkeypatch.setattr(redact_mod, "_load_presidio", broken)
    r = redact(SAMPLE)
    assert r.engine == "regex_fallback"
    assert "maria.fernandes@example.org" not in r.text
    assert "555 0147" not in r.text
    assert r.counts == {"EMAIL_ADDRESS": 1, "PHONE_NUMBER": 1}


@pytest.mark.parametrize(
    "text, entity",
    [
        ("Call 0977 123 456 today", "PHONE_NUMBER"),
        ("Call (202) 555-0147 today", "PHONE_NUMBER"),
        ("Call +254 712 345 678 today", "PHONE_NUMBER"),
        ("Write to j.doe+test@ngo-example.co.uk now", "EMAIL_ADDRESS"),
        ("Patient ID 12345678 attended", "ID_NUMBER"),
        ("Passport AB1234567 on file", "ID_NUMBER"),
    ],
)
def test_fallback_patterns(text, entity):
    r = redact(text, force_fallback=True)
    assert f"<{entity}>" in r.text


@pytest.mark.parametrize(
    "text",
    [
        "Stock fell 12% in 2025 across 4 districts.",
        "Budget line 3.2 covers 46 weeks.",
        "Report dated 2026-11-24 for Q3.",
        "Between 10 15 and 20 sites.",
    ],
)
def test_fallback_leaves_ordinary_numbers(text):
    r = redact(text, force_fallback=True)
    assert r.text == text and r.total == 0


def test_fallback_does_not_mask_names_and_says_so():
    """Documents the limit: regex cannot find names, and the engine is recorded."""
    r = redact("Maria Fernandes", force_fallback=True)
    assert r.text == "Maria Fernandes" and r.engine == "regex_fallback"


def test_empty_text():
    assert redact("").text == ""
