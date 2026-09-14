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


def _log_comb(n: int, k: int) -> float:
    """``log C(n, k)``, computed in log space so large counts stay finite."""
    if k < 0 or k > n:
        return -math.inf
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def fisher_exact_two_sided(passed_a: int, total_a: int, passed_b: int, total_b: int) -> float:
    """Two-sided Fisher exact p-value for two pass counts.

    The exact test rather than a comparison of confidence intervals, because
    overlapping intervals do **not** mean "no difference". Overlap is only a
    conservative screen in one direction: disjoint intervals do prove a
    difference, but overlapping ones prove nothing at all, and reading them as
    "cannot distinguish" hides real effects.

    Measured here: 13/20 versus 19/20 has intervals that overlap, and a Fisher
    p of 0.022 -- the difference is real, and an interval-overlap rule reported
    the opposite.
    """
    if total_a <= 0 or total_b <= 0:
        return 1.0
    total = total_a + total_b
    total_passed = passed_a + passed_b

    def probability(passed_in_a: int) -> float:
        return math.exp(
            _log_comb(total_passed, passed_in_a)
            + _log_comb(total - total_passed, total_a - passed_in_a)
            - _log_comb(total, total_a)
        )

    observed = probability(passed_a)
    # Every table with these margins whose probability is no greater than the
    # observed one is at least as extreme.
    low = max(0, total_a - (total - total_passed))
    high = min(total_a, total_passed)
    p_value = 0.0
    for candidate in range(low, high + 1):
        value = probability(candidate)
        if value <= observed * (1 + 1e-9):
            p_value += value
    return min(1.0, p_value)


def discrimination(cells: list[dict[str, Any]], *, alpha: float = 0.05) -> dict[str, Any]:
    """Whether the attempts actually separate the cells.

    Decided by Fisher's exact test on each pair of pass counts, with a
    Bonferroni correction across the pairs being compared. A matrix asks many
    questions at once -- four cells is six pairs -- so testing each at 0.05 would
    manufacture a "finding" from noise; the correction keeps the stated
    confidence honest about how many comparisons produced it.

    Reported rather than assumed, and never silently upgraded: a pair that fails
    the test is reported as indistinguishable with its p-value, whatever the pass
    rates look like.
    """
    comparable = [cell for cell in cells if cell.get("measured")]
    if len(comparable) < 2:
        return {
            "separated": None,
            "reason": "至少需要两个有可测结果的单元格才能比较",
            "overlapping": [],
            "comparisons": [],
        }

    pairs = [
        (left, right)
        for index, left in enumerate(comparable)
        for right in comparable[index + 1 :]
    ]
    threshold = alpha / len(pairs)
    comparisons: list[dict[str, Any]] = []
    indistinguishable: list[list[str]] = []
    for left, right in pairs:
        p_value = fisher_exact_two_sided(
            int(left["passed"]),
            int(left["measured"]),
            int(right["passed"]),
            int(right["measured"]),
        )
        entry = {
            "left": left.get("label", ""),
            "right": right.get("label", ""),
            "p_value": round(p_value, 5),
            "significant": p_value < threshold,
        }
        # Kept for display: an interval is still the clearest way to show how
        # much is unknown about each cell on its own.
        if intervals_overlap(
            left.get("pass_rate_ci95") or [0.0, 1.0],
            right.get("pass_rate_ci95") or [0.0, 1.0],
        ):
            entry["intervals_overlap"] = True
        comparisons.append(entry)
        if not entry["significant"]:
            indistinguishable.append([left.get("label", ""), right.get("label", "")])

    if indistinguishable:
        smallest = min(cell["measured"] for cell in comparable)
        return {
            "separated": False,
            "reason": (
                f"按 Fisher 精确检验（Bonferroni 校正后阈值 p<{threshold:.4f}），"
                f"有 {len(indistinguishable)} 组单元格无法区分；"
                f"当前样本量（最少 {smallest} 次可测）不足以判定。增加重复次数才能得出结论。"
            ),
            "overlapping": indistinguishable,
            "comparisons": comparisons,
            "alpha": alpha,
            "bonferroni_threshold": round(threshold, 5),
        }
    return {
        "separated": True,
        "reason": (
            f"所有成对比较均通过 Fisher 精确检验（Bonferroni 校正后阈值 p<{threshold:.4f}）。"
        ),
        "overlapping": [],
        "comparisons": comparisons,
        "alpha": alpha,
        "bonferroni_threshold": round(threshold, 5),
    }
