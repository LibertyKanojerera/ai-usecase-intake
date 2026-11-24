# AI Use Case Intake & Risk Check

> Work in progress. A full README, the evaluation results and a live demo link are coming.

Staff describe a task they want AI to help with. The tool checks it against a written rubric (`rubric.yaml`), estimates the hours it could save, and sends it to a person who decides. Every step is logged in an insert-only audit table.

**All data is synthetic and the organization is fictional.** This is an unpaid personal portfolio project.

## Design principles

1. The model can raise a risk tier, never lower it.
2. A person decides every request.
3. Sensitive text is screened (Microsoft Presidio, with a regex fallback) before any model call.
4. Policy lives in configuration (`rubric.yaml`).
5. Every step is logged and nothing is overwritten.
6. It still works without the model (rules-only mode).

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py          # rules-only mode with no key
pytest                        # runs without a key or network
```

To use the model, copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and add an Anthropic API key, or set `ANTHROPIC_API_KEY` in your environment.
