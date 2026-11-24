"""Evaluate the risk check on the 11 synthetic requests.

Expected tiers were set by hand (data/sample_requests.csv) before any code
existed. Every number in the README comes from this script, never typed by hand.

    python -m eval.run_eval                       # rules only if no key
    python -m eval.run_eval --runs 3 --precompute # model x3, save demo cache
    python -m eval.run_eval --update-readme       # write the table into README.md

Measures
  agreement      final tier == expected tier, out of 11
  under-calls    final tier below expected (the dangerous miss; target 0)
  over-calls     final tier above expected (costs reviewer time, not safety)
  model alone    the same three measures for the model's tier on its own
  floor saves    model alone under-called, but the final tier did not
  stability      model tier changes across repeated runs of the same request
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from intake.config import load_rubric  # noqa: E402
from intake.llm import DEFAULT_MODEL, api_key_available, assess  # noqa: E402
from intake.merge import merge, rank  # noqa: E402
from intake.redact import redact  # noqa: E402
from intake.rules import score  # noqa: E402
from intake.samples import load_samples  # noqa: E402

RESULTS_JSON = ROOT / "eval" / "results.json"
RESULTS_MD = ROOT / "eval" / "results.md"
PRECOMPUTED = ROOT / "data" / "precomputed.json"
README = ROOT / "README.md"
MARK_START, MARK_END = "<!-- EVAL:START -->", "<!-- EVAL:END -->"
T = {"low": "Low", "medium": "Medium", "high": "High", "prohibited": "Prohibited", None: "—"}


def compare(got: str | None, expected: str) -> str:
    if got is None:
        return "no_answer"
    d = rank(got) - rank(expected)
    return "match" if d == 0 else ("under" if d < 0 else "over")


def tally(verdicts: list[str]) -> dict:
    return {k: verdicts.count(k) for k in ("match", "under", "over", "no_answer")}


def run(runs: int, use_model: bool, model: str, client=None) -> dict:
    rubric = load_rubric()
    samples = load_samples()
    rows, usage = [], {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    cache: dict[str, dict] = {}

    for s in samples:
        red = redact(s.form.task_description)
        rules = score(s.form, rubric)
        model_tiers: list[str | None] = []
        first_ok = None
        if use_model:
            for i in range(runs):
                out = assess(s.form, red.text, client=client, model=model, rubric=rubric)
                usage["input_tokens"] += out.input_tokens
                usage["output_tokens"] += out.output_tokens
                usage["calls"] += out.attempts
                model_tiers.append(out.tier)
                if out.assessment and first_ok is None:
                    first_ok = out.assessment
                    cache[str(s.id)] = out.assessment.model_dump()
                print(f"  request {s.id:>2} run {i + 1}: model={out.tier} ({out.status})", flush=True)
        m1 = model_tiers[0] if model_tiers else None
        final = merge(rules.tier, m1).final_tier
        changes = len({t for t in model_tiers}) - 1 if model_tiers else 0
        finals = [merge(rules.tier, t).final_tier for t in model_tiers] or [final]
        rows.append({
            "id": s.id, "title": s.form.title, "expected": s.expected_tier,
            "rules": rules.tier, "rules_triggered": list(rules.all_triggered),
            "model_runs": model_tiers, "model": m1, "final": final,
            "final_runs": finals,
            "verdict_final": compare(final, s.expected_tier),
            "verdict_model": compare(m1, s.expected_tier) if use_model else None,
            "verdict_rules": compare(rules.tier, s.expected_tier),
            "model_tier_changes": changes,
            "final_tier_changes": len(set(finals)) - 1,
            "redaction": {"engine": red.engine, "counts": red.counts},
        })

    n = len(rows)
    summary = {
        "n": n,
        "final": tally([r["verdict_final"] for r in rows]),
        "rules_alone": tally([r["verdict_rules"] for r in rows]),
        "model_alone": tally([r["verdict_model"] for r in rows]) if use_model else None,
        "floor_saves": sum(r["verdict_model"] in ("under", "no_answer") and r["verdict_final"] != "under"
                           for r in rows) if use_model else None,
        "stability": {
            "runs_per_request": runs if use_model else 0,
            "requests_with_model_tier_change": sum(r["model_tier_changes"] > 0 for r in rows),
            "requests_with_final_tier_change": sum(r["final_tier_changes"] > 0 for r in rows),
        } if use_model else None,
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rubric_version": rubric.version,
        "mode": "rules+model" if use_model else "rules_only",
        "model": model if use_model else None,
        "usage": usage if use_model else None,
        "summary": summary,
        "rows": rows,
        "_cache": cache,
    }


def to_markdown(res: dict, price_in: float | None, price_out: float | None) -> str:
    s, n = res["summary"], res["summary"]["n"]
    lines = [f"_Generated by `eval/run_eval.py` on {res['generated_at']} · rubric {res['rubric_version']} · "
             f"mode: {res['mode']}{' · model ' + res['model'] if res['model'] else ''}._", ""]
    head = "| Measure | Rules alone |" + (" Model alone | Final (rules + model) |" if res["model"] else "")
    lines += [head, "|---|---|" + ("---|---|" if res["model"] else "")]

    def row(label, key):
        cells = [f"{s['rules_alone'][key]} / {n}"]
        if res["model"]:
            cells += [f"{s['model_alone'][key]} / {n}", f"**{s['final'][key]} / {n}**"]
        return f"| {label} | " + " | ".join(cells) + " |"

    lines += [row("Agreement with expected tier", "match"),
              row("Under-calls (target 0)", "under"),
              row("Over-calls", "over")]
    if res["model"]:
        lines.append(f"| Model gave no usable answer | — | {s['model_alone']['no_answer']} / {n} | — |")
        st = s["stability"]
        lines += [
            "",
            f"- **Rules floor saves:** {s['floor_saves']} request(s) where the model alone under-called "
            "or failed, but the final tier did not.",
            f"- **Stability:** {st['runs_per_request']} runs per request. The model's tier changed between "
            f"runs on {st['requests_with_model_tier_change']} of {n} requests; the final tier changed on "
            f"{st['requests_with_final_tier_change']} of {n}.",
        ]
        u = res["usage"]
        if u and u["calls"]:
            per_in, per_out = u["input_tokens"] / u["calls"], u["output_tokens"] / u["calls"]
            cost = ""
            if price_in is not None and price_out is not None:
                c = (per_in * price_in + per_out * price_out) / 1e6
                cost = f", about ${c:.4f} per assessment at ${price_in}/${price_out} per million tokens"
            lines.append(f"- **Usage:** {u['calls']} calls, about {per_in:,.0f} input and "
                         f"{per_out:,.0f} output tokens per assessment{cost}.")
    lines += ["", "| # | Request | Expected | Rules | Model (runs) | Final | Result |",
              "|---|---|---|---|---|---|---|"]
    for r in res["rows"]:
        runs = ", ".join(T[t] for t in r["model_runs"]) or "—"
        verdict = {"match": "✓ match", "under": "✗ under-call", "over": "over-call"}[r["verdict_final"]]
        lines.append(f"| {r['id']} | {r['title']} | {T[r['expected']]} | {T[r['rules']]} | {runs} | "
                     f"{T[r['final']]} | {verdict} |")
    misses = [r for r in res["rows"] if r["verdict_final"] != "match"]
    if misses:
        lines += ["", "**Misses** (explained in the README):"]
        lines += [f"- #{r['id']} {r['title']}: expected {T[r['expected']]}, got {T[r['final']]} "
                  f"({r['verdict_final']})." for r in misses]
    return "\n".join(lines) + "\n"


def update_readme(md: str) -> None:
    text = README.read_text(encoding="utf-8")
    if MARK_START not in text:
        raise SystemExit(f"README.md has no {MARK_START} marker")
    block = f"{MARK_START}\n{md}{MARK_END}"
    README.write_text(re.sub(re.escape(MARK_START) + r".*?" + re.escape(MARK_END), lambda _: block,
                             text, flags=re.S), encoding="utf-8")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--runs", type=int, default=3, help="model runs per request (stability)")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--rules-only", action="store_true", help="skip the model even if a key is set")
    p.add_argument("--precompute", action="store_true", help="save run-1 assessments to data/precomputed.json")
    p.add_argument("--update-readme", action="store_true", help="write the results table into README.md")
    p.add_argument("--price-in", type=float, help="USD per million input tokens, for the cost line")
    p.add_argument("--price-out", type=float, help="USD per million output tokens, for the cost line")
    a = p.parse_args(argv)

    use_model = not a.rules_only and api_key_available()
    if not a.rules_only and not use_model:
        print("No ANTHROPIC_API_KEY set: running rules only.", file=sys.stderr)
    t0 = time.time()
    res = run(a.runs, use_model, a.model)
    cache = res.pop("_cache")

    RESULTS_JSON.write_text(json.dumps(res, indent=2), encoding="utf-8")
    md = to_markdown(res, a.price_in, a.price_out)
    RESULTS_MD.write_text(md, encoding="utf-8")
    if a.precompute:
        if not use_model:
            print("--precompute needs the model; nothing written.", file=sys.stderr)
        else:
            PRECOMPUTED.write_text(json.dumps(cache, indent=2), encoding="utf-8")
            print(f"Saved {len(cache)} assessments to {PRECOMPUTED.relative_to(ROOT)}")
    if a.update_readme:
        update_readme(md)
        print("README.md updated.")
    print(md)
    print(f"Done in {time.time() - t0:.0f}s.")
    return 1 if res["summary"]["final"]["under"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
