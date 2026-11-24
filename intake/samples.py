"""Load the synthetic sample requests from data/sample_requests.csv."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from intake.config import IntakeForm

SAMPLES_PATH = Path(__file__).resolve().parent.parent / "data" / "sample_requests.csv"


@dataclass(frozen=True)
class Sample:
    id: int
    form: IntakeForm
    expected_rule_tier: str
    expected_tier: str
    notes: str


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split(";") if v.strip()]


def _bool(value: str) -> bool:
    v = value.strip().lower()
    if v not in {"true", "false"}:
        raise ValueError(f"expected true/false, got {value!r}")
    return v == "true"


def row_to_form(row: dict[str, str]) -> IntakeForm:
    return IntakeForm(
        title=row["title"],
        team=row["team"],
        task_description=row["task_description"],
        data_types=_split(row["data_types"]),
        destination=row["destination"],
        audience=row["audience"],
        decision_about_person=_bool(row["decision_about_person"]),
        decision_type=row["decision_type"] or None,
        human_review=row["human_review"],
        countries=_split(row["countries"]),
        eu_uk_involved=_bool(row["eu_uk_involved"]),
        hours_per_week=float(row["hours_per_week"]),
        people=int(row["people"]),
        share_saved=float(row["share_saved"]),
    )


def load_samples(path: str | Path = SAMPLES_PATH) -> list[Sample]:
    with open(path, newline="", encoding="utf-8") as fh:
        return [
            Sample(
                id=int(row["id"]),
                form=row_to_form(row),
                expected_rule_tier=row["expected_rule_tier"],
                expected_tier=row["expected_tier"],
                notes=row["notes"],
            )
            for row in csv.DictReader(fh)
        ]
