"""Run the same Task through several Agent profiles and persist a comparison."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .docker_runtime import DockerRuntimeError, start_docker_run, wait_docker_run
from .lifecycle import prepare_run
from .resolver import EvalConfigError, resolve_run


class ComparisonError(RuntimeError):
    """Raised when comparison setup cannot be completed."""


def compare_task(
    repo_root: Path,
    task_id: str,
    agents: Iterable[str],
    *,
    config_path: Path | None = None,
    model_profile: str | None = None,
    mcp_profile: str | None = None,
    sandbox_profile: str | None = None,
    preset: str | None = None,
    game_engine_ref: str | None = None,
    dsh_ref: str | None = None,
    evaluate: bool = True,
) -> dict[str, Any]:
    """Execute each Agent sequentially under identical Task conditions."""
    from ..evaluation.runner import EvaluationError, evaluate_run

    selected_agents = tuple(dict.fromkeys(agent.strip() for agent in agents if agent.strip()))
    if not selected_agents:
        raise ComparisonError("compare requires at least one Agent id")
    repo_root = repo_root.resolve()
    specs = [
        resolve_run(
            repo_root,
            task_id,
            config_path=config_path,
            agent=agent,
            model_profile=model_profile,
            mcp_profile=mcp_profile,
            sandbox_profile=sandbox_profile,
            preset=preset,
            game_engine_ref=game_engine_ref,
            dsh_ref=dsh_ref,
        )
        for agent in selected_agents
    ]
    first = specs[0]
    invariant = {
        "task_id": task_id,
        "task_bundle": first.task_bundle,
        "test_plan": first.test_plan.to_dict(),
        "model_profile": first.model_profile,
        "model": first.model,
        "model_provider": first.model_provider,
        "mcp_profile": first.mcp_profile,
        "mcp_servers": first.mcp_servers,
        "sandbox_profile": first.sandbox_profile,
        "sandbox": first.sandbox,
        "resource_specs": [resource.to_dict() for resource in first.resource_specs],
    }
    runs: list[dict[str, Any]] = []
    for spec in specs:
        run_record: dict[str, Any] = {
            "agent": spec.agent,
            "model": spec.model,
            "run_id": spec.run_id,
            "status": "not_started",
        }
        try:
            run_dir = prepare_run(
                repo_root,
                spec,
                game_engine_ref=game_engine_ref,
                dsh_ref=dsh_ref,
            )
            start_docker_run(run_dir, repo_root)
            manifest = wait_docker_run(run_dir)
            run_record["run_dir"] = str(run_dir)
            run_record["status"] = manifest.get("status")
            if evaluate and manifest.get("status") in {"completed", "failed"}:
                run_record["evaluation"] = evaluate_run(run_dir, repo_root=repo_root)
        except (DockerRuntimeError, EvalConfigError, EvaluationError, OSError, ValueError) as exc:
            run_record.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
        runs.append(run_record)

    runs_root = first.runs_root
    comparison_id = f"{task_id}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:6]}"
    output_dir = runs_root / "comparisons" / comparison_id
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "comparison_id": comparison_id,
        "created_at": datetime.now(UTC).isoformat(),
        "config_path": str(config_path.resolve()) if config_path else None,
        "invariant": invariant,
        "runs": runs,
        "output_dir": str(output_dir),
    }
    _write_json(output_dir / "comparison.json", payload)
    (output_dir / "comparison.md").write_text(render_comparison_markdown(payload), encoding="utf-8")
    return payload


def render_comparison_markdown(payload: dict[str, Any]) -> str:
    """Render a compact comparison table for humans and CI artifacts."""
    lines = [
        f"# Agent comparison: `{payload.get('invariant', {}).get('task_id', '')}`",
        "",
        f"- comparison: `{payload.get('comparison_id', '')}`",
        f"- created: `{payload.get('created_at', '')}`",
        "",
        "| Agent | Run | Status | Outcome | Quality | Process | Decision |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for run in payload.get("runs", []):
        evaluation = run.get("evaluation") or {}
        lines.append(
            (
                "| {agent} | `{run_id}` | {status} | {outcome} | {quality} | "
                "{process} | {decision} |"
            ).format(
                agent=run.get("agent", ""),
                run_id=run.get("run_id", ""),
                status=run.get("status", ""),
                outcome=evaluation.get("outcome_score", "-"),
                quality=evaluation.get("quality_score", "-"),
                process=evaluation.get("process_score", "-"),
                decision=evaluation.get("decision", "-"),
            )
        )
    if payload.get("output_dir"):
        lines.extend(["", f"Output: `{payload['output_dir']}`"])
    return "\n".join(lines) + "\n"


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
