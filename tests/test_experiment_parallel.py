"""Running attempts in parallel without changing what the experiment reports.

Parallelism here is a scheduling decision, not a semantic one: the same design
must produce the same report whether it ran one attempt at a time or eight. These
pin the properties that make that true -- a bound on what is in flight, isolation
between attempts, and an output order that does not depend on finish order.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_native_evals.experiments import runner as experiment_runner
from ai_native_evals.experiments.spec import ExperimentSpec


def _fake_spec(tmp_path: Path, **selectors) -> SimpleNamespace:
    agent = selectors.get("agent", "codex")
    return SimpleNamespace(
        agent=agent,
        run_id=f"{agent}-{selectors.get('reasoning_effort', 'x')}-{id(object())}",
        model=selectors.get("model", "test-model"),
        model_provider="eval",
        model_profile="p",
        provider="sub2api",
        reasoning_effort=selectors.get("reasoning_effort", "high"),
        mcp_profile="none",
        mcp_servers={},
        sandbox_profile="docker-default",
        sandbox={},
        resource_specs=(),
        task_bundle={"id": "task"},
        test_plan=SimpleNamespace(to_dict=lambda: {"checks": []}),
        runs_root=tmp_path / "EvalRuns",
        run_dir=tmp_path / "EvalRuns" / f"{agent}-run",
    )


def _install(monkeypatch, tmp_path: Path, *, resolve, decisions) -> None:
    monkeypatch.setattr(experiment_runner.resolver, "resolve_run", resolve)
    monkeypatch.setattr(
        experiment_runner.lifecycle,
        "prepare_run",
        lambda _root, spec, **_kwargs: spec.run_dir,
    )
    monkeypatch.setattr(
        experiment_runner.docker_runtime, "start_docker_run", lambda run_dir, _root: None
    )
    monkeypatch.setattr(
        experiment_runner.docker_runtime,
        "wait_docker_run",
        lambda run_dir: {"status": "completed"},
    )
    lock = threading.Lock()
    queue = list(decisions)

    def fake_evaluate(run_dir, **_kwargs):  # type: ignore[no-untyped-def]
        with lock:
            decision = queue.pop(0) if queue else {"decision": "pass", "outcome_score": 1.0}
        return dict(decision)

    monkeypatch.setattr("ai_native_evals.evaluation.runner.evaluate_run", fake_evaluate)


def _spec(**overrides) -> ExperimentSpec:
    body = {"id": "e", "task_id": "task", "vary": {"agent": ["codex"]}, "repeats": 8}
    body.update(overrides)
    return ExperimentSpec.from_mapping(body)


def test_concurrency_bounds_what_is_in_flight(tmp_path: Path, monkeypatch) -> None:
    """The peak number of simultaneous attempts never exceeds the bound."""
    live = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def resolve(repo_root, task_id, **kwargs):
        with lock:
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
        time.sleep(0.05)  # hold the slot long enough for overlap to be visible
        with lock:
            live["now"] -= 1
        return _fake_spec(tmp_path, **kwargs)

    _install(
        monkeypatch,
        tmp_path,
        resolve=resolve,
        decisions=[{"decision": "pass", "outcome_score": 1.0}] * 8,
    )

    experiment_runner.run_experiment(tmp_path, _spec(), concurrency=3)

    assert live["peak"] <= 3, f"more attempts ran at once than allowed: {live['peak']}"


def test_parallel_actually_overlaps(tmp_path: Path, monkeypatch) -> None:
    """A bound of 4 must be used, not merely permitted.

    Without this, a refactor that silently serialised the pool would still pass
    the bound test above.
    """
    live = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def resolve(repo_root, task_id, **kwargs):
        with lock:
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
        time.sleep(0.05)
        with lock:
            live["now"] -= 1
        return _fake_spec(tmp_path, **kwargs)

    _install(
        monkeypatch,
        tmp_path,
        resolve=resolve,
        decisions=[{"decision": "pass", "outcome_score": 1.0}] * 8,
    )

    experiment_runner.run_experiment(tmp_path, _spec(), concurrency=4)

    assert live["peak"] > 1, "attempts never overlapped; the pool is not parallel"


def test_concurrency_one_never_overlaps(tmp_path: Path, monkeypatch) -> None:
    live = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def resolve(repo_root, task_id, **kwargs):
        with lock:
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
        time.sleep(0.02)
        with lock:
            live["now"] -= 1
        return _fake_spec(tmp_path, **kwargs)

    _install(
        monkeypatch,
        tmp_path,
        resolve=resolve,
        decisions=[{"decision": "pass", "outcome_score": 1.0}] * 4,
    )

    experiment_runner.run_experiment(tmp_path, _spec(repeats=4), concurrency=1)

    assert live["peak"] == 1


def test_report_order_does_not_depend_on_finish_order(
    tmp_path: Path, monkeypatch
) -> None:
    """Attempts are reported in schedule order however they finish.

    Finish order is nondeterministic under a pool, so a report that inherited it
    would differ between two runs of the same design for no reason.
    """
    order: list[int] = []
    lock = threading.Lock()

    def resolve(repo_root, task_id, **kwargs):
        spec = _fake_spec(tmp_path, **kwargs)
        # Make later attempts finish FIRST, so completion order is reversed.
        index = len(order)
        with lock:
            order.append(index)
        time.sleep(0.06 - 0.008 * index)
        return spec

    _install(
        monkeypatch,
        tmp_path,
        resolve=resolve,
        decisions=[{"decision": "pass", "outcome_score": 1.0}] * 6,
    )

    payload = experiment_runner.run_experiment(
        tmp_path, _spec(repeats=6), concurrency=6
    )

    assert [a["attempt"] for a in payload["attempts"]] == [1, 2, 3, 4, 5, 6]


def test_one_failing_attempt_does_not_sink_the_batch(
    tmp_path: Path, monkeypatch
) -> None:
    """A worker that raises is recorded as a failed attempt, not a lost batch."""
    state = {"n": 0}
    lock = threading.Lock()

    def resolve(repo_root, task_id, **kwargs):
        with lock:
            state["n"] += 1
            n = state["n"]
        if n == 3:
            raise OSError("docker hiccup on one attempt")
        return _fake_spec(tmp_path, **kwargs)

    _install(
        monkeypatch,
        tmp_path,
        resolve=resolve,
        decisions=[{"decision": "pass", "outcome_score": 1.0}] * 8,
    )

    payload = experiment_runner.run_experiment(tmp_path, _spec(), concurrency=4)

    assert len(payload["attempts"]) == 8
    errored = [a for a in payload["attempts"] if a["status"] == "error"]
    assert len(errored) == 1
    assert "docker hiccup" in errored[0]["error"]
    # The other attempts still counted.
    assert payload["cells"][0]["measured"] == 7
    assert payload["cells"][0]["unmeasured"] == 1


def test_parallel_and_serial_agree_on_the_verdict(tmp_path: Path, monkeypatch) -> None:
    """The scheduling choice must not move the pass rate or the verdict."""
    decisions = [
        {"decision": "pass", "outcome_score": 1.0},
        {"decision": "fail", "outcome_score": 0.0},
        {"decision": "pass", "outcome_score": 1.0},
        {"decision": "pass", "outcome_score": 1.0},
        {"decision": "fail", "outcome_score": 0.0},
        {"decision": "pass", "outcome_score": 1.0},
    ]
    spec = _spec(repeats=6)

    _install(monkeypatch, tmp_path, resolve=lambda r, t, **k: _fake_spec(tmp_path, **k),
             decisions=list(decisions))
    serial = experiment_runner.run_experiment(tmp_path, spec, concurrency=1)

    _install(monkeypatch, tmp_path, resolve=lambda r, t, **k: _fake_spec(tmp_path, **k),
             decisions=list(decisions))
    parallel = experiment_runner.run_experiment(tmp_path, spec, concurrency=3)

    assert parallel["cells"][0]["pass_rate"] == serial["cells"][0]["pass_rate"]
    assert parallel["cells"][0]["passed"] == serial["cells"][0]["passed"]
    assert parallel["discrimination"] == serial["discrimination"]


def test_concurrency_is_recorded_in_the_artifact(tmp_path: Path, monkeypatch) -> None:
    """A result should say how its attempts were scheduled."""
    _install(
        monkeypatch,
        tmp_path,
        resolve=lambda r, t, **k: _fake_spec(tmp_path, **k),
        decisions=[{"decision": "pass", "outcome_score": 1.0}] * 8,
    )

    payload = experiment_runner.run_experiment(tmp_path, _spec(), concurrency=5)

    assert payload["concurrency"] == 5
    markdown = (Path(payload["output_dir"]) / "experiment.md").read_text(encoding="utf-8")
    assert "concurrency: **5**" in markdown


def test_zero_concurrency_is_refused(tmp_path: Path) -> None:
    with pytest.raises(experiment_runner.ExperimentRunError, match="at least 1"):
        experiment_runner._run_parallel([], concurrency=0, work=lambda item: item)
