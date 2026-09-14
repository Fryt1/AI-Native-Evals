"""Statistics for repeated evaluation attempts.

An Agent evaluation is a sample, not a measurement. The same Task and the same
Agent can pass or fail on different attempts: the model samples, and a container
occasionally fails to start. Everything here exists to keep that fact visible --
to report a rate with its uncertainty, and to refuse to call a difference real
when the sample cannot support it.

Deliberately dependency-free and pure: given the same attempts it returns the
same numbers, which is what makes a persisted experiment reproducible.
"""

from __future__ import annotations

import math
from statistics import median
from typing import Any

#: z for a 95% two-sided interval.
Z_95 = 1.959963984540054

#: Decisions that measure the Agent. `pass` and `fail` are the two answers to
#: "did it do the task"; `review` and `not_evaluable` mean the question went
#: unanswered, which is not the Agent's doing.
DECIDED = frozenset({"pass", "fail"})


def wilson_interval(successes: int, total: int, z: float = Z_95) -> tuple[float, float] | None:
    """A 95% confidence interval for a pass rate, or ``None`` without trials.

    Wilson rather than the normal approximation because these counts are small
    and often extreme: with 5 attempts a normal interval collapses to zero width
    at ``5/5`` -- claiming certainty from five observations -- and can extend
    below zero at ``0/5``. Wilson stays inside [0, 1] and keeps its width at the
    boundaries, which is exactly where a small comparison lives.
    """
    if total <= 0:
        return None
    phat = successes / total
    denominator = 1.0 + z * z / total
    center = (phat + z * z / (2 * total)) / denominator
    margin = (
        z * math.sqrt(phat * (1.0 - phat) / total + z * z / (4 * total * total)) / denominator
    )
    return (max(0.0, center - margin), min(1.0, center + margin))


def score_summary(values: list[float]) -> dict[str, Any] | None:
    """Median and observed range, or ``None`` when nothing was measurable.

    The median is the headline because a single infra failure or a binary
    outcome score drags a mean around; the range is reported beside it so an
    outlying attempt cannot hide behind a tidy central value.
    """
    if not values:
        return None
    return {
        "median": round(median(values), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
        "range": round(max(values) - min(values), 3),
        "n": len(values),
    }


def _numeric(value: Any) -> float | None:
    """One score as a float, rejecting booleans and non-numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def summarize_attempts(
    label: str,
    attempts: list[dict[str, Any]],
    *,
    scores: tuple[str, ...] = ("outcome_score", "quality_score", "process_score"),
) -> dict[str, Any]:
    """Aggregate one cell's attempts into a pass rate, interval and spreads.

    ``attempted`` counts every attempt made. ``measured`` counts those that
    actually answered the question, and only those form the pass-rate
    denominator: an attempt that died because Docker would not start, or that
    ended ``review``, says nothing about the Agent. Counting it as a failure
    would let a flaky machine masquerade as an incompetent Agent.
    """
    measured: list[str] = []
    for attempt in attempts:
        decision = (attempt.get("evaluation") or {}).get("decision")
        if decision in DECIDED:
            measured.append(str(decision))
        else:
            measured.append("")

    passes = sum(1 for decision in measured if decision == "pass")
    total = sum(1 for decision in measured if decision)
    interval = wilson_interval(passes, total)

    summary: dict[str, Any] = {
        "label": label,
        "attempted": len(attempts),
        "measured": total,
        "unmeasured": len(attempts) - total,
        "passed": passes,
        "failed": total - passes,
        "pass_rate": (round(passes / total, 3) if total else None),
        "pass_rate_ci95": ([round(interval[0], 3), round(interval[1], 3)] if interval else None),
    }
    for key in scores:
        label_key = key.removesuffix("_score")
        values = [
            number
            for attempt in attempts
            if (number := _numeric((attempt.get("evaluation") or {}).get(key))) is not None
        ]
        summary[label_key] = score_summary(values)
    return summary


def intervals_overlap(left: list[float], right: list[float]) -> bool:
    """Whether two ``[low, high]`` intervals share any value."""
    return left[0] <= right[1] and right[0] <= left[1]


def discrimination(cells: list[dict[str, Any]]) -> dict[str, Any]:
    """Whether the attempts actually separate the cells.

    Reported rather than assumed, and never silently upgraded: two intervals
    that overlap mean this sample cannot tell them apart, whatever the pass
    rates look like. A comparison that hides this is the reason single-run
    tables mislead in the first place.
    """
    comparable = [cell for cell in cells if cell.get("pass_rate_ci95")]
    if len(comparable) < 2:
        return {
            "separated": None,
            "reason": "至少需要两个有可测结果的单元格才能比较",
            "overlapping": [],
        }
    overlapping: list[list[str]] = []
    for index, left in enumerate(comparable):
        for right in comparable[index + 1 :]:
            if intervals_overlap(left["pass_rate_ci95"], right["pass_rate_ci95"]):
                overlapping.append([left.get("label", ""), right.get("label", "")])
    if overlapping:
        smallest = min(cell["measured"] for cell in comparable)
        return {
            "separated": False,
            "reason": (
                f"置信区间重叠，当前样本量（最少 {smallest} 次可测）不足以区分这些单元格。"
                "增加重复次数才能得出结论。"
            ),
            "overlapping": overlapping,
        }
    return {"separated": True, "reason": "所有单元格的置信区间互不重叠。", "overlapping": []}
