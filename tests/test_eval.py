from eval.run_eval import run, to_markdown
from tests.fakes import FakeClient, make_assessment, response


def test_metrics_with_a_model_that_always_says_low():
    """A careless model: rules floor must carry everything above Low."""
    client = FakeClient([response(make_assessment()) for _ in range(11)])
    res = run(runs=1, use_model=True, model="fake", client=client)
    s = res["summary"]
    assert s["model_alone"]["match"] == 2          # only requests 1 and 2 are Low
    assert s["final"] == {"match": 10, "under": 1, "over": 0, "no_answer": 0}
    assert s["floor_saves"] == 8                   # 3-9 and 11 rescued by the floor


def test_stability_counts_tier_changes():
    answers = []
    for _ in range(11):
        answers += [response(make_assessment({"output_integrity": "high"})),
                    response(make_assessment({"output_integrity": "medium"}))]
    res = run(runs=2, use_model=True, model="fake", client=FakeClient(answers))
    assert res["summary"]["stability"]["requests_with_model_tier_change"] == 11
    assert "Stability" in to_markdown(res, 1.0, 5.0)


def test_prohibited_flag_brings_request_10_to_agreement():
    answers = [response(make_assessment({"bias_fairness": "high"}, prohibited=(i == 9)))
               for i in range(11)]
    res = run(runs=1, use_model=True, model="fake", client=FakeClient(answers))
    row10 = next(r for r in res["rows"] if r["id"] == 10)
    assert row10["final"] == "prohibited" and row10["verdict_final"] == "match"
