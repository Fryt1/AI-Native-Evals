"""The statistics that keep a comparison honest.

These pin the properties that matter when the sample is small, because small is
the normal case here: five attempts cost five real Docker runs and five real
model calls.
"""

from __future__ import annotations

import pytest

from ai_native_evals.experiments.stats import (
    discrimination,
    fisher_exact_two_sided,
    intervals_overlap,
    score_summary,
    summarize_attempts,
    wilson_interval,
)


def _attempt(decision: str | None, **scores) -> dict:
    return {"evaluation": {"decision": decision, **scores} if decision else {}}


def test_wilson_interval_stays_inside_the_unit_range() -> None:
    """A normal interval would go below zero at 0/5 and above one at 5/5."""
    low, high = wilson_interval(0, 5)
    assert low == 0.0
    assert 0.0 < high < 1.0
    low, high = wilson_interval(5, 5)
    assert high == 1.0
    assert 0.0 < low < 1.0


def test_wilson_interval_keeps_its_width_at_the_boundaries() -> None:
    """The whole point: 5/5 is not certainty, and the interval must say so.

    The normal approximation collapses to zero width here, which would report
    five observations as a proven 100%.
    """
    low, high = wilson_interval(5, 5)
    assert high - low > 0.4


def test_wilson_interval_is_none_without_trials() -> None:
    assert wilson_interval(0, 0) is None


def test_more_evidence_narrows_the_interval() -> None:
    small = wilson_interval(5, 5)
    large = wilson_interval(50, 50)
    assert (large[1] - large[0]) < (small[1] - small[0])


def test_score_summary_reports_median_and_observed_range() -> None:
    summary = score_summary([1.0, 1.0, 0.0])

    assert summary["median"] == 1.0
    assert summary["min"] == 0.0
    assert summary["max"] == 1.0
    assert summary["range"] == 1.0
    assert summary["n"] == 3


def test_score_summary_is_none_when_nothing_was_measurable() -> None:
    assert score_summary([]) is None


def test_unmeasured_attempts_are_excluded_from_the_rate() -> None:
    """Infrastructure failure is not Agent behaviour.

    A container that never started, or a run that ended `review`, is reported
    separately and kept out of the denominator -- counting it as a failure would
    let a flaky machine look like an incompetent Agent.
    """
    summary = summarize_attempts(
        "agent=codex",
        [
            _attempt("pass", outcome_score=1.0),
            _attempt("fail", outcome_score=0.0),
            _attempt(None),  # never ran
            {"status": "error", "error": "docker unavailable"},
        ],
    )

    assert summary["attempted"] == 4
    assert summary["measured"] == 2
    assert summary["unmeasured"] == 2
    assert summary["pass_rate"] == 0.5
    assert summary["passed"] == 1 and summary["failed"] == 1


def test_review_counts_as_unmeasured_not_as_failure() -> None:
    summary = summarize_attempts("c", [_attempt("review"), _attempt("pass", outcome_score=1.0)])

    assert summary["measured"] == 1
    assert summary["unmeasured"] == 1
    assert summary["pass_rate"] == 1.0


def test_a_cell_with_nothing_measured_has_no_rate() -> None:
    """Not `0.0`. An unmeasured cell must not read as "it always fails"."""
    summary = summarize_attempts("c", [{"status": "error"}])

    assert summary["pass_rate"] is None
    assert summary["pass_rate_ci95"] is None
    assert summary["unmeasured"] == 1


def test_scores_are_aggregated_per_cell() -> None:
    summary = summarize_attempts(
        "c",
        [
            _attempt("pass", outcome_score=1.0, quality_score=0.9),
            _attempt("pass", outcome_score=1.0, quality_score=0.7),
            _attempt("fail", outcome_score=0.0),
        ],
    )

    assert summary["outcome"]["median"] == 1.0
    assert summary["outcome"]["range"] == 1.0
    assert summary["quality"]["median"] == 0.8
    # A score nobody reported is absent, not zero.
    assert summary["quality"]["n"] == 2


def test_small_difference_is_not_evidence() -> None:
    """4/5 versus 3/5 is not a difference, and the verdict must say so."""
    cells = [
        summarize_attempts("codex", [_attempt("pass")] * 4 + [_attempt("fail")]),
        summarize_attempts("dsh", [_attempt("pass")] * 3 + [_attempt("fail")] * 2),
    ]

    verdict = discrimination(cells)

    assert verdict["separated"] is False
    assert verdict["overlapping"]


def test_a_real_difference_is_found_even_when_intervals_overlap() -> None:
    """The bug this replaced.

    13/20 versus 19/20 has overlapping 95% intervals, and the first version of
    this function called that "cannot tell". Fisher's exact test gives p=0.044:
    the difference is real. Interval overlap is only a conservative screen --
    disjoint intervals prove a difference, but overlapping ones prove nothing,
    and reading them as sameness hides genuine effects.
    """
    low = summarize_attempts("low", [_attempt("pass")] * 13 + [_attempt("fail")] * 7)
    high = summarize_attempts("high", [_attempt("pass")] * 19 + [_attempt("fail")])

    verdict = discrimination([low, high])

    assert intervals_overlap(low["pass_rate_ci95"], high["pass_rate_ci95"]), (
        "this case only means anything while the intervals do overlap"
    )
    assert verdict["separated"] is True
    assert verdict["comparisons"][0]["p_value"] < 0.05


def test_indistinguishable_cells_are_still_reported_as_such() -> None:
    """The correction must not turn every comparison into a finding."""
    cells = [
        summarize_attempts("codex", [_attempt("pass")] * 18 + [_attempt("fail")] * 2),
        summarize_attempts("dsh", [_attempt("pass")] * 20),
    ]

    verdict = discrimination(cells)

    assert verdict["separated"] is False
    assert verdict["comparisons"][0]["p_value"] > 0.05


def test_disjoint_intervals_are_separated() -> None:
    cells = [
        summarize_attempts("perfect", [_attempt("pass")] * 40),
        summarize_attempts("hopeless", [_attempt("fail")] * 40),
    ]

    verdict = discrimination(cells)

    assert verdict["separated"] is True
    assert verdict["overlapping"] == []


def test_more_comparisons_need_more_evidence() -> None:
    """Four cells ask six questions, so the bar per question has to rise.

    Without the correction, a matrix of many cells would manufacture a finding
    from noise: testing six pairs at 0.05 each gives roughly a one-in-four chance
    of at least one false positive.
    """
    two = discrimination(
        [
            summarize_attempts("a", [_attempt("pass")] * 13 + [_attempt("fail")] * 7),
            summarize_attempts("b", [_attempt("pass")] * 19 + [_attempt("fail")]),
        ]
    )
    four = discrimination(
        [
            summarize_attempts("a", [_attempt("pass")] * 13 + [_attempt("fail")] * 7),
            summarize_attempts("b", [_attempt("pass")] * 19 + [_attempt("fail")]),
            summarize_attempts("c", [_attempt("pass")] * 15 + [_attempt("fail")] * 5),
            summarize_attempts("d", [_attempt("pass")] * 16 + [_attempt("fail")] * 4),
        ]
    )

    assert four["bonferroni_threshold"] < two["bonferroni_threshold"]
    assert len(four["comparisons"]) == 6
    assert len(two["comparisons"]) == 1


def test_fisher_matches_an_independently_computed_value() -> None:
    """13/20 against 19/20, computed by brute-force enumeration: p=0.0436."""
    assert fisher_exact_two_sided(13, 20, 19, 20) == pytest.approx(0.0436, abs=0.0005)


@pytest.mark.parametrize(
    ("passed_a", "total_a", "passed_b", "total_b"),
    [(0, 10, 10, 10), (10, 10, 10, 10), (5, 10, 5, 10), (9, 10, 10, 10)],
)
def test_fisher_stays_in_range(
    passed_a: int, total_a: int, passed_b: int, total_b: int
) -> None:
    p_value = fisher_exact_two_sided(passed_a, total_a, passed_b, total_b)
    assert 0.0 <= p_value <= 1.0


def test_identical_counts_are_never_a_difference() -> None:
    assert fisher_exact_two_sided(7, 10, 7, 10) == pytest.approx(1.0)


def test_one_cell_cannot_be_compared() -> None:
    verdict = discrimination([summarize_attempts("only", [_attempt("pass")])])

    assert verdict["separated"] is None


def test_cells_without_measurements_are_not_compared() -> None:
    """A cell with no rate has no interval, so it cannot overlap or separate."""
    cells = [
        summarize_attempts("measured", [_attempt("pass")] * 5),
        summarize_attempts("broken", [{"status": "error"}]),
    ]

    assert discrimination(cells)["separated"] is None


@pytest.mark.parametrize(("successes", "total"), [(0, 1), (1, 1), (1, 2), (7, 10)])
def test_interval_always_brackets_the_observed_rate(successes: int, total: int) -> None:
    low, high = wilson_interval(successes, total)
    observed = successes / total
    assert low <= observed <= high
