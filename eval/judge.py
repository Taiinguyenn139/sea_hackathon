"""Optional independent quality judge; explicit skipped state without configuration."""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

RUBRIC = {
    "grounded": "Claims are supported and uncertainty is explicit.",
    "continuity": "Uses customer context without redundant questions.",
    "clarity": "Vietnamese reply is clear and respectful.",
    "handoff": "Appropriate next step and human escalation.",
}


def agreement(scores: list[int], labels: list[int]) -> dict[str, float | int | None]:
    if len(scores) != len(labels):
        raise ValueError("Judge scores and human labels must align")
    if any(value not in range(1, 6) for value in scores + labels):
        raise ValueError("Scores must be integers 1–5")
    return {
        "n": len(scores),
        "exact_agreement": sum(a == b for a, b in zip(scores, labels)) / len(scores) if scores else None,
        "mean_absolute_error": sum(abs(a - b) for a, b in zip(scores, labels)) / len(scores) if scores else None,
    }


def judge(turns: list[dict], agent_model: str | None = None) -> dict[str, Any]:
    endpoint, model, key = (os.environ.get(name) for name in ("JUDGE_API_URL", "JUDGE_MODEL", "JUDGE_API_KEY"))
    if not endpoint or not model or not key:
        return {"status": "skipped", "reason": "Judge endpoint/model/key not configured"}
    if model == agent_model:
        raise ValueError("Judge model must differ from agent model")
    request = urllib.request.Request(
        endpoint,
        json.dumps(
            {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": "Evaluate fictional bank replies. Return only JSON {scores: {grounded:1..5, continuity:1..5, clarity:1..5, handoff:1..5}}. Rubric: "
                        + json.dumps(RUBRIC),
                    },
                    {"role": "user", "content": json.dumps(turns, ensure_ascii=False)},
                ],
                "temperature": 0,
            }
        ).encode(),
        {"Content-Type": "application/json", "Authorization": "Bearer " + str(key)},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        output = json.load(response)
    scores = json.loads(output["choices"][0]["message"]["content"])["scores"]
    if set(scores) != set(RUBRIC) or any(type(v) is not int or not 1 <= v <= 5 for v in scores.values()):
        raise ValueError("Judge returned invalid rubric scores")
    return {"status": "completed", "model": model, "rubric": RUBRIC, "scores": scores}
