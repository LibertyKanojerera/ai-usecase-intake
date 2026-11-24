"""A stand-in for the Anthropic client, so model-path tests need no key or network."""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from intake.config import DIMENSIONS
from intake.llm import Assessment, Dimension


def make_assessment(levels: dict[str, str] | None = None, tier: str | None = None,
                    prohibited: bool = False, **overrides) -> Assessment:
    levels = {d: "low" for d in DIMENSIONS} | (levels or {})
    order = ["low", "medium", "high", "prohibited"]
    worst = max(levels.values(), key=order.index)
    tier = tier or ("prohibited" if prohibited else worst)
    data = dict(
        dimensions=[Dimension(name=n, level=lv, reason=f"{n} is {lv}") for n, lv in levels.items()],
        prohibited_practice=prohibited,
        prohibited_reason="Emotion recognition in the workplace (Article 5)" if prohibited else "",
        suggested_tier=tier,
        tool_path="prompt_template",
        required_controls=["Use an approved tool"],
        questions_for_requester=[],
        summary="Test assessment.",
    )
    data.update(overrides)
    return Assessment.model_validate(data)


def response(parsed, stop_reason: str = "end_turn", input_tokens: int = 1000, output_tokens: int = 300):
    return SimpleNamespace(
        parsed_output=parsed,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


@dataclass
class FakeClient:
    """Returns queued responses in order; an Exception in the queue is raised."""

    queue: list = field(default_factory=list)
    calls: list = field(default_factory=list)

    def __post_init__(self):
        self.messages = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
