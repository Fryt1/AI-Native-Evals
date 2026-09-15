from pathlib import Path
from types import SimpleNamespace

from ai_native_evals.experiments import compare
from ai_native_evals.experiments import runner as experiment_runner


def _fake_spec(tmp_path: Path, agent: str, task_id: str = "task") -> SimpleNamespace:
    return SimpleNamespace(
        agent=agent,
        run_id=f"{agent}-run",
        model="test-model",
        model_provider="eval",
        model_profile="test-model-profile",
        provider="sub2api",
        reasoning_effort="high",
        mcp_profile="none",
        mcp_servers={},
        sandbox_profile="docker-default",
        sandbox={"read_only_root": True},
        resource_specs=(),
        task_bundle={"id": task_id},
        test_plan=SimpleNamespace(to_dict=lambda: {"checks": []}),
        runs_root=tmp_path / "EvalRuns",
        run_dir=tmp_path / "EvalRuns" / f"{agent}-run",
    )


def _install_fakes(monkeypatch, tmp_path: Path, *, resolve, start=None, wait=None) -> None:
    """Wire the runner's runtime seams to fakes.

    The runner holds module references (`resolver`, `lifecycle`, `docker_runtime`)
    rather than importing the functions by name, so a fake replaces the attribute
    on those modules. It used to go through `_resolver()`/`_lifecycle()`/
    `_docker()` accessors that existed only to defer a circular import; the cycle
    is gone, and the seam is now the plain module.
    """
    monkeypatch.setattr(experiment_runner.resolver, "resolve_run", resolve)
    monkeypatch.setattr(
        experiment_runner.lifecycle,
        "prepare_run",
        lambda _root, spec, **_kwargs: spec.run_dir,
    )
    monkeypatch.setattr(
        experiment_runner.docker_runtime,
        "start_docker_run",
        start or (lambda run_dir, _root: None),
    )
    monkeypatch.setattr(
        experiment_runner.docker_runtime,
        "wait_docker_run",
        wait or (lambda run_dir: {"status": "completed", "run": {"run_id": run_dir.name}}),
    )


def test_compare_task_keeps_common_invariant_and_writes_report(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []

    def fake_resolve(repo_root, task_id, **kwargs):
        return _fake_spec(tmp_path, kwargs["agent"], task_id)

    _install_fakes(
        monkeypatch,
        tmp_path,
        resolve=fake_resolve,
        start=lambda run_dir, _root: calls.append(f"start:{run_dir.name}"),
    )
    monkeypatch.setattr(
        "ai_native_evals.evaluation.runner.evaluate_run",
        lambda run_dir, **_kwargs: {
            "decision": "pass",
            "outcome_score": 1.0,
            "quality_score": 0.8,
            "process_score": 0.9,
            "run_id": run_dir.name,
        },
    )

    payload = compare.compare_task(tmp_path, "task", ["codex", "dsh-release"])

    assert calls == ["start:codex-run", "start:dsh-release-run"]
    assert len(payload["runs"]) == 2
    assert all(run["evaluation"]["decision"] == "pass" for run in payload["runs"])
    output_dir = Path(payload["output_dir"])
    assert (output_dir / "comparison.json").is_file()
    assert "dsh-release" in (output_dir / "comparison.md").read_text(encoding="utf-8")


def test_compare_runs_each_agent_the_requested_number_of_times(
    tmp_path: Path, monkeypatch
) -> None:
    """`--runs N` is the whole point: one attempt cannot show variance."""
    counter = {"n": 0}

    def fake_resolve(repo_root, task_id, **kwargs):
        counter["n"] += 1
        # A fresh run id per attempt, as the real resolver produces.
        spec = _fake_spec(tmp_path, kwargs["agent"], task_id)
        return SimpleNamespace(**{**vars(spec), "run_id": f"{kwargs['agent']}-{counter['n']}"})

    _install_fakes(monkeypatch, tmp_path, resolve=fake_resolve)
    monkeypatch.setattr(
        "ai_native_evals.evaluation.runner.evaluate_run",
        lambda run_dir, **_kwargs: {
            "decision": "pass",
            "outcome_score": 1.0,
            "quality_score": 1.0,
            "process_score": 1.0,
        },
    )

    payload = compare.compare_task(tmp_path, "task", ["codex", "dsh"], repeat=3)

    assert len(payload["runs"]) == 6
    assert payload["repeat"] == 3
    assert {cell["agent"] for cell in payload["agents"]} == {"codex", "dsh"}
    assert all(cell["measured"] == 3 for cell in payload["agents"])
    assert all(cell["pass_rate"] == 1.0 for cell in payload["agents"])
    # Every attempt on record, numbered, so a reader can see which one differed.
    assert sorted(run["attempt"] for run in payload["runs"]) == [1, 1, 2, 2, 3, 3]


def test_compare_marks_agents_overlapping_intervals_as_indistinguishable(
    tmp_path: Path, monkeypatch
) -> None:
    """The verdict must say "cannot tell" rather than pick a winner."""
    decisions = {
        "codex": iter(["pass", "pass", "pass", "pass", "fail"]),
        "dsh": iter(["pass", "fail", "pass", "fail", "pass"]),
    }

    def fake_resolve(repo_root, task_id, **kwargs):
        spec = _fake_spec(tmp_path, kwargs["agent"], task_id)
        return SimpleNamespace(**{**vars(spec), "run_id": f"{kwargs['agent']}-{id(object())}"})

    _install_fakes(monkeypatch, tmp_path, resolve=fake_resolve)

    def fake_evaluate(run_dir, **_kwargs):
        agent = run_dir.name.split("-")[0]
        decision = next(decisions[agent])
        return {
            "decision": decision,
            "outcome_score": 1.0 if decision == "pass" else 0.0,
            "quality_score": None,
            "process_score": None,
        }

    monkeypatch.setattr("ai_native_evals.evaluation.runner.evaluate_run", fake_evaluate)

    payload = compare.compare_task(tmp_path, "task", ["codex", "dsh"], repeat=5)

    verdict = payload["discrimination"]
    assert verdict["separated"] is False
    assert verdict["overlapping"]
