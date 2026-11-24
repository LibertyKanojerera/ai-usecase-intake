"""Mask personal data before any text leaves the app.

Primary engine: Microsoft Presidio with spaCy's medium English model
(en_core_web_md, smaller than Presidio's default en_core_web_lg so it fits
on Streamlit Community Cloud). If Presidio or the model fails to load, a
regex fallback masks emails, phone numbers and ID-like numbers. The regex
cannot find names, so the result records which engine ran.

Known limits: Presidio misses some personal data and does not recognise
health details written as prose. A real deployment would add the
organisation's DLP tooling (for example Microsoft Purview).
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache

log = logging.getLogger(__name__)

SPACY_MODEL = "en_core_web_md"
MIN_PHONE_DIGITS = 7
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

PRESIDIO_ENTITIES = [
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "LOCATION",
    "US_SSN",
    "CREDIT_CARD",
    "IBAN_CODE",
    "IP_ADDRESS",
    "ID_NUMBER",  # custom recognizer below
]

# Fallback patterns. Order matters: emails before phones so the digits in an
# email local part are not mistaken for a phone number.
FALLBACK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL_ADDRESS", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    # Broad shape; _regex_redact keeps only matches with at least 7 digits,
    # so dates and short figures are left alone.
    ("PHONE_NUMBER", re.compile(r"(?<![\w])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{1,4}\)[\s.-]?)?\d{2,4}(?:[\s.-]\d{2,4}){1,4}(?![\w])")),
    ("ID_NUMBER", re.compile(r"\b(?:[A-Z]{1,3}[-\s]?)?\d{6,}\b")),
]


@dataclass(frozen=True)
class RedactionResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)
    engine: str = "presidio"  # "presidio" or "regex_fallback"

    @property
    def total(self) -> int:
        return sum(self.counts.values())


# ----------------------------------------------------------- presidio ---
@lru_cache(maxsize=1)
def _load_presidio():
    """Build the analyzer and anonymizer once. Raises if anything is missing."""
    from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    from presidio_anonymizer import AnonymizerEngine

    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": SPACY_MODEL}],
        }
    )
    analyzer = AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=["en"])
    analyzer.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="ID_NUMBER",
            patterns=[Pattern("id_like_number", r"\b(?:[A-Z]{1,3}[-\s]?)?\d{6,}\b", 0.5)],
        )
    )
    return analyzer, AnonymizerEngine()


def _presidio_redact(text: str) -> RedactionResult:
    from presidio_anonymizer.entities import OperatorConfig

    analyzer, anonymizer = _load_presidio()
    findings = analyzer.analyze(text=text, entities=PRESIDIO_ENTITIES, language="en")
    operators = {
        f.entity_type: OperatorConfig("replace", {"new_value": f"<{f.entity_type}>"})
        for f in findings
    }
    out = anonymizer.anonymize(text=text, analyzer_results=findings, operators=operators)
    # Count what was actually replaced (overlaps are resolved by the anonymizer).
    counts = Counter(item.entity_type for item in out.items)
    return RedactionResult(out.text, dict(counts), "presidio")


# -------------------------------------------------------------- regex ---
def _regex_redact(text: str) -> RedactionResult:
    counts: Counter[str] = Counter()
    for entity, pattern in FALLBACK_PATTERNS:

        def _sub(m: re.Match[str], entity: str = entity) -> str:
            if entity == "PHONE_NUMBER" and (
                sum(c.isdigit() for c in m.group()) < MIN_PHONE_DIGITS or ISO_DATE.fullmatch(m.group())
            ):
                return m.group()
            counts[entity] += 1
            return f"<{entity}>"

        text = pattern.sub(_sub, text)
    return RedactionResult(text, dict(counts), "regex_fallback")


# ------------------------------------------------------------- public ---
def redact(text: str, *, force_fallback: bool = False) -> RedactionResult:
    """Mask personal data. Never raises: falls back to regex on any Presidio error."""
    if not text:
        return RedactionResult("", {}, "presidio" if not force_fallback else "regex_fallback")
    if not force_fallback:
        try:
            return _presidio_redact(text)
        except Exception as exc:  # model missing, import error, runtime failure
            log.warning("Presidio unavailable, using regex fallback: %s", exc)
    return _regex_redact(text)


def presidio_available() -> bool:
    try:
        _load_presidio()
        return True
    except Exception:
        return False
