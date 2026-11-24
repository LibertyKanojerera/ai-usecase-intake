import pytest

from intake import db
from intake.pipeline import run_intake
from intake.records import decision_record_markdown
from intake.samples import load_samples
from tests.fakes import FakeClient, make_assessment, response

SAMPLES = {s.id: s for s in load_samples()}


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def test_record_carries_tier_controls_decision_and_trail(conn):
    client = FakeClient([response(make_assessment({"confidentiality_ip": "high"}))])
    run_intake(conn, SAMPLES[8].form, submitted_by="amina", client=client,
               force_redaction_fallback=True)
    db.record_decision(conn, 1, reviewer="rev", decision="approve_with_conditions",
                       agrees_with_model=False, disagreement_reason="Also a security route issue",
                       conditions="Use the enterprise tool instead",
                       infosec_signoff="J. Okoro", legal_signoff="P. Mensah")
    md = decision_record_markdown(conn, 1)
    for expected in ("# Decision record: request #1", "**Final tier: High**",
                     "Data protection review with InfoSec and Legal", "Approved with conditions",
                     "Disagreed with the model: Also a security route issue",
                     "InfoSec sign-off: J. Okoro", "| system | tier_final |", "| rev | decision |"):
        assert expected in md


def test_record_for_rules_only_request(conn):
    run_intake(conn, SAMPLES[1].form, submitted_by="amina", use_model=False,
               force_redaction_fallback=True)
    md = decision_record_markdown(conn, 1)
    assert "No decision yet." in md and "Model: — (skipped_cap)" in md
