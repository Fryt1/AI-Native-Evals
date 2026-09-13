from pathlib import Path
from types import SimpleNamespace

from ai_native_evals.runs import compare


def test_compare_task_keeps_common_invariant_and_writes_report(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []

    def fake_resolve(repo_root, task_id, **kwargs):
        agent = kwargs["agent"]
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

    monkeypatch.setattr(compare, "resolve_run", fake_resolve)
    monkeypatch.setattr(compare, "prepare_run", lambda _root, spec, **_kwargs: spec.run_dir)
    monkeypatch.setattr(
        compare,
        "start_docker_run",
        lambda run_dir, _root: calls.append(f"start:{run_dir.name}"),
    )
    monkeypatch.setattr(
        compare,
        "wait_docker_run",
        lambda run_dir: {"status": "completed", "run": {"run_id": run_dir.name}},
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
