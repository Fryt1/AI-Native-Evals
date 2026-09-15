"""Executing a matrix: every cell, every attempt, and nothing quietly dropped.

The runner is the seam where a design becomes real containers. These tests drive
it with fakes so the matrix arithmetic and the persisted record are pinned
without starting Docker.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from ai_native_evals.experiments import runner as experiment_runner
from ai_native_evals.experiments.spec import ExperimentSpec


def _fake_spec(tmp_path: Path, **selectors) -> SimpleNamespace:
    agent = selectors.get("agent", "codex")
    return SimpleNamespace(
        agent=agent,
        run_id=f"{agent}-{selectors.get('reasoning_effort', 'x')}-{id(object())}",
        model=selectors.get("model", "test-model"),
        model_provider="eval",
        model_profile="test-model-profile",
        provider="sub2api",
        reasoning_effort=selectors.get("reasoning_effort", "high"),
        mcp_profile="none",
        mcp_servers={},
        sandbox_profile="docker-default",
        sandbox={"read_only_root": True},
        resource_specs=(),
        task_bundle={"id": "task"},
        test_plan=SimpleNamespace(to_dict=lambda: {"checks": []}),
        runs_root=tmp_path / "EvalRuns",
        run_dir=tmp_path / "EvalRuns" / f"{agent}-run",
    )


def _install(monkeypatch, tmp_path: Path, *, resolve, decisions) -> None:
    """Wire the runner's runtime seams and a scripted evaluator.

    The seams are the modules the runner holds references to, not accessors: the
    accessors existed only to defer a circular import between `experiments/` and
    `runs/`, and that cycle is gone.
    """
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
    scripted = iter(decisions)
    monkeypatch.setattr(
        "ai_native_evals.evaluation.runner.evaluate_run",
        lambda run_dir, **_kwargs: dict(next(scripted)),
    )


def _spec(**overrides) -> ExperimentSpec:
    body = {
        "id": "exp",
        "task_id": "task",
        "vary": {"agent": ["codex", "dsh"]},
        "repeats": 2,
    }
    body.update(overrides)
    return ExperimentSpec.from_mapping(body)


def test_every_cell_runs_repeats_times(tmp_path: Path, monkeypatch) -> None:
    seen: list[dict] = []

    def fake_resolve(repo_root, task_id, **kwargs):
        seen.append(kwargs)
        return _fake_spec(tmp_path, **kwargs)

    _install(
        monkeypatch,
        tmp_path,
        resolve=fake_resolve,
        decisions=[{"decision": "pass", "outcome_score": 1.0}] * 4,
    )

    payload = experiment_runner.run_experiment(tmp_path, _spec())

    assert len(payload["attempts"]) == 4
    assert len(payload["cells"]) == 2
    assert {kwargs["agent"] for kwargs in seen} == {"codex", "dsh"}
    assert all(cell["measured"] == 2 for cell in payload["cells"])


def test_varying_axis_values_reach_the_resolver(tmp_path: Path, monkeypatch) -> None:
    """The axis must actually change the run, not just the label on it."""
    seen: list[str | None] = []

    def fake_resolve(repo_root, task_id, **kwargs):
        seen.append(kwargs.get("reasoning_effort"))
        return _fake_spec(tmp_path, **kwargs)

    _install(
        monkeypatch,
        tmp_path,
        resolve=fake_resolve,
        decisions=[{"decision": "pass", "outcome_score": 1.0}] * 4,
    )
    spec = _spec(
        vary={"agent": ["codex"], "reasoning_effort": ["low", "high"]}, repeats=2
    )

    payload = experiment_runner.run_experiment(tmp_path, spec)

    # The first resolution primes the invariant, before the attempt loop.
    assert seen[:1] == ["low"]
    assert seen[1:] == ["low", "low", "high", "high"]
    assert [cell["selectors"]["reasoning_effort"] for cell in payload["cells"]] == [
        "low",
        "high",
    ]


def test_frozen_inputs_are_recorded_in_the_invariant(tmp_path: Path, monkeypatch) -> None:
    """What was held constant is part of the result, not a detail of the command."""
    _install(
        monkeypatch,
        tmp_path,
        resolve=lambda repo_root, task_id, **kwargs: _fake_spec(tmp_path, **kwargs),
        decisions=[{"decision": "pass", "outcome_score": 1.0}] * 4,
    )

    payload = experiment_runner.run_experiment(
        tmp_path, _spec(fixed={"model": "m", "reasoning_effort": "high"})
    )

    assert payload["invariant"]["fixed"] == {"model": "m", "reasoning_effort": "high"}
    assert payload["invariant"]["vary"] == {"agent": ["codex", "dsh"]}
    assert payload["invariant"]["repeats"] == 2


def test_a_failed_attempt_is_recorded_rather_than_dropped(tmp_path: Path, monkeypatch) -> None:
    """An attempt that dies mid-matrix is still an attempt, and still visible.

    The first resolution is what primes the invariant, so it must succeed; the
    failures here are the ones that happen once the matrix is under way, which is
    where a machine runs out of Docker or a network blips.
    """
    state = {"n": 0}

    def flaky_resolve(repo_root, task_id, **kwargs):
        state["n"] += 1
        if state["n"] > 1:  # first call primes the invariant
            raise OSError("docker is not running")
        return _fake_spec(tmp_path, **kwargs)

    _install(monkeypatch, tmp_path, resolve=flaky_resolve, decisions=[])

    payload = experiment_runner.run_experiment(tmp_path, _spec(repeats=1))

    assert len(payload["attempts"]) == 2
    assert all(attempt["status"] == "error" for attempt in payload["attempts"])
    assert all("docker is not running" in attempt["error"] for attempt in payload["attempts"])
    # Not measured, so not scored as a failure either.
    assert all(cell["pass_rate"] is None for cell in payload["cells"])
    assert all(cell["unmeasured"] == 1 for cell in payload["cells"])


def test_an_unresolvable_design_fails_before_any_attempt(tmp_path: Path, monkeypatch) -> None:
    """A design that cannot resolve should not start three containers first."""
    calls = {"n": 0}

    def failing_resolve(repo_root, task_id, **kwargs):
        calls["n"] += 1
        raise ValueError("unknown agent profile")

    _install(monkeypatch, tmp_path, resolve=failing_resolve, decisions=[])

    import pytest

    with pytest.raises(experiment_runner.ExperimentRunError, match="cannot be resolved"):
        experiment_runner.run_experiment(tmp_path, _spec())

    assert calls["n"] == 1


def test_artifacts_are_written_with_both_readable_names(tmp_path: Path, monkeypatch) -> None:
    """Console indexes `comparison_id`; this module's own name is `experiment_run_id`."""
    _install(
        monkeypatch,
        tmp_path,
        resolve=lambda repo_root, task_id, **kwargs: _fake_spec(tmp_path, **kwargs),
        decisions=[{"decision": "pass", "outcome_score": 1.0}] * 4,
    )

    payload = experiment_runner.run_experiment(tmp_path, _spec())

    output_dir = Path(payload["output_dir"])
    assert output_dir.parent.name == "experiments"
    written = json.loads((output_dir / "experiment.json").read_text(encoding="utf-8"))
    assert written["comparison_id"] == written["experiment_run_id"]
    assert written["runs"] == written["attempts"]
    markdown = (output_dir / "experiment.md").read_text(encoding="utf-8")
    assert "## Cells" in markdown
    assert "## Attempts" in markdown


def test_the_markdown_states_the_denominator_and_the_verdict(
    tmp_path: Path, monkeypatch
) -> None:
    _install(
        monkeypatch,
        tmp_path,
        resolve=lambda repo_root, task_id, **kwargs: _fake_spec(tmp_path, **kwargs),
        decisions=[{"decision": "pass", "outcome_score": 1.0}] * 4,
    )

    payload = experiment_runner.run_experiment(tmp_path, _spec())
    markdown = (Path(payload["output_dir"]) / "experiment.md").read_text(encoding="utf-8")

    assert "Unmeasured" in markdown
    # Two cells both at 4/4 overlap, so the report must refuse to separate them.
    assert "无法区分" in markdown
