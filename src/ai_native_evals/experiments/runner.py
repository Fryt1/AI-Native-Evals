"""Execute an ExperimentSpec and persist the result.

The runner owns one job: given a validated design, run every cell `repeats`
times under identical frozen inputs, and write a record that separates what was
measured from what was assumed. It does not decide what to vary -- that is the
definition's business -- and it does not soften an inconclusive result into a
conclusion.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..runs import docker_runtime, lifecycle, resolver
from ..runs.docker_runtime import DockerRuntimeError
from ..runs.resolver import EvalConfigError
from .spec import Cell, ExperimentSpec
from .stats import discrimination, summarize_attempts


class ExperimentRunError(RuntimeError):
    """Raised when an experiment cannot be started at all."""


def _resolve_cell(repo_root: Path, spec: ExperimentSpec, cell: Cell, *, config_path: Path | None):
    """Resolve one attempt: frozen selectors plus this cell's axis values.

    Re-resolved per attempt, never reused, so every attempt gets its own run id
    and workspace. Sharing a spec across attempts would share its run id, and the
    attempts would overwrite each other's evidence instead of accumulating.
    """
    selectors = spec.selectors_for(cell)
    return resolver.resolve_run(
        repo_root,
        spec.task_id,
        config_path=config_path,
        agent=selectors.get("agent"),
        model_profile=selectors.get("model_profile"),
        provider=selectors.get("provider"),
        model=selectors.get("model"),
        reasoning_effort=selectors.get("reasoning_effort"),
        mcp_profile=selectors.get("mcp_profile"),
        sandbox_profile=selectors.get("sandbox_profile"),
        preset=selectors.get("preset"),
        game_engine_ref=selectors.get("game_engine_ref"),
        dsh_ref=selectors.get("dsh_ref"),
    )


def _with_runs_root(spec: Any, runs_root: Path) -> Any:
    """One resolved spec re-pointed at another runs root.

    ``run_dir`` is derived, not independent: leaving it on the old root would
    prepare the workspace somewhere the caller did not ask for.
    """
    resolved = runs_root.expanduser().resolve()
    return replace(spec, runs_root=resolved, run_dir=resolved / spec.run_id)


def _attempt(
    repo_root: Path,
    spec: ExperimentSpec,
    cell: Cell,
    attempt: int,
    config_path: Path | None,
    evaluate: bool,
    runs_root: Path | None,
) -> dict[str, Any]:
    """Run one attempt to completion and return its record.

    Returns a record for every outcome, including a failure that never reached
    Docker. Attempts are independent -- they share no state beyond the frozen
    inputs -- which is what makes running several at once safe. Each gets its own
    run id, so each gets its own workspace, network and container names.
    """
    from ..evaluation.runner import EvaluationError, evaluate_run

    record: dict[str, Any] = {
        "cell": cell.label,
        "cell_index": cell.index,
        "selectors": spec.selectors_for(cell),
        "attempt": attempt,
        "status": "not_started",
    }
    try:
        run_spec = _resolve_cell(repo_root, spec, cell, config_path=config_path)
        if runs_root is not None:
            run_spec = _with_runs_root(run_spec, runs_root)
        record.update(
            {
                "agent": run_spec.agent,
                "model": run_spec.model,
                "provider": run_spec.provider,
                "run_id": run_spec.run_id,
            }
        )
        run_dir = lifecycle.prepare_run(
            repo_root,
            run_spec,
            game_engine_ref=spec.fixed.get("game_engine_ref"),
            dsh_ref=spec.fixed.get("dsh_ref"),
        )
        docker_runtime.start_docker_run(run_dir, repo_root)
        manifest = docker_runtime.wait_docker_run(run_dir)
        record["run_dir"] = str(run_dir)
        record["status"] = manifest.get("status")
        if evaluate and manifest.get("status") in {"completed", "failed"}:
            record["evaluation"] = evaluate_run(run_dir, repo_root=repo_root)
    except (
        DockerRuntimeError,
        EvalConfigError,
        EvaluationError,
        ExperimentRunError,
        OSError,
        ValueError,
    ) as exc:
        record.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
    return record


def _run_parallel(
    items: list[Any],
    *,
    concurrency: int,
    work: Any,
) -> list[Any]:
    """Run ``work`` over ``items`` with at most ``concurrency`` in flight.

    Each attempt waits on a remote model, so the pool's real limit is memory and
    provider rate limits rather than CPU. Results come back in the order the
    items were given, so a report never depends on which attempt happened to
    finish first.

    A worker that dies takes only its own attempt down: exceptions are captured
    per item and returned in its slot, because losing a whole batch to one
    transient failure is exactly what repeating an experiment is meant to
    survive. KeyboardInterrupt is deliberately not caught -- an operator asking
    to stop should stop the whole run, not be collected as a failed attempt.
    """
    if concurrency < 1:
        raise ExperimentRunError("concurrency must be at least 1")
    results: list[Any] = [None] * len(items)
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(work, item): index for index, item in enumerate(items)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:  # one attempt must not sink the batch
                item = items[index]
                cell, attempt = item if isinstance(item, tuple) else (None, None)
                results[index] = {
                    "cell": getattr(cell, "label", ""),
                    "cell_index": getattr(cell, "index", index),
                    "selectors": {},
                    "attempt": attempt,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
    return results


def run_experiment(
    repo_root: Path,
    spec: ExperimentSpec,
    *,
    config_path: Path | None = None,
    evaluate: bool = True,
    runs_root: Path | None = None,
    artifact_dir: str = "experiments",
    concurrency: int = 1,
) -> dict[str, Any]:
    """Execute every cell and write the run artifact under ``artifact_dir``.

    Each attempt is recorded as it finishes and nothing is dropped for being
    inconvenient, so an attempt that could not start is still on the record.

    ``artifact_dir`` selects the subdirectory of the runs root. `compare` keeps
    writing ``comparisons/`` because the Console indexes that path; a matrix
    experiment writes ``experiments/``.

    ``concurrency`` is how many attempts may be in flight at once. An attempt
    spends nearly all of its wall clock waiting on a model, and a container
    measures around 60 MiB resident, so the ceiling is provider rate limits and
    the sandbox memory limit rather than CPU. The default stays 1: a caller who
    wants parallel load should ask for it explicitly.
    """
    repo_root = repo_root.resolve()
    cells = spec.cells
    if not cells:
        raise ExperimentRunError("experiment has no cells to run")

    # Resolve the first attempt up front: a design that cannot be resolved at all
    # should fail before the first container starts, not after three good runs.
    try:
        first_spec = _resolve_cell(repo_root, spec, cells[0], config_path=config_path)
    except (EvalConfigError, OSError, ValueError) as exc:
        raise ExperimentRunError(f"experiment cannot be resolved: {exc}") from exc
    if runs_root is not None:
        first_spec = _with_runs_root(first_spec, runs_root)

    invariant = {
        "task_id": spec.task_id,
        "task_bundle": first_spec.task_bundle,
        "test_plan": first_spec.test_plan.to_dict(),
        # What the design froze, recorded next to what it varied. A result whose
        # held-constant inputs are unstated cannot be reproduced or compared.
        "fixed": dict(spec.fixed),
        "vary": {key: list(values) for key, values in spec.vary.items()},
        "repeats": spec.repeats,
        "resource_specs": [resource.to_dict() for resource in first_spec.resource_specs],
    }

    runs: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []

    # Every (cell, attempt) pair, in a fixed order. The order is what the report
    # is written in; execution order is whatever the pool finishes in, so the
    # two are kept separate on purpose.
    schedule = [
        (cell, attempt) for cell in cells for attempt in range(1, spec.repeats + 1)
    ]
    if concurrency > 1:

        def _work(item: tuple[Cell, int]) -> dict[str, Any]:
            cell, attempt = item
            return _attempt(
                repo_root, spec, cell, attempt, config_path, evaluate, runs_root
            )

        results = _run_parallel(schedule, concurrency=concurrency, work=_work)
    else:
        results = [
            _attempt(repo_root, spec, cell, attempt, config_path, evaluate, runs_root)
            for cell, attempt in schedule
        ]
    for record in results:
        runs.append(record)

    for cell in cells:
        cell_runs = [record for record in runs if record.get("cell_index") == cell.index]
        summary = summarize_attempts(cell.label, cell_runs)
        summary.update({"cell_index": cell.index, "selectors": spec.selectors_for(cell)})
        summaries.append(summary)

    root = runs_root if runs_root is not None else first_spec.runs_root
    experiment_run_id = (
        f"{spec.experiment_id}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:6]}"
    )
    output_dir = root / artifact_dir / experiment_run_id
    payload = {
        "experiment_run_id": experiment_run_id,
        "experiment_id": spec.experiment_id,
        # Carried so the existing Console comparison reader can index this
        # artifact unchanged: it discovers `comparison.json` under
        # `runs_root/comparisons/` and keys on `comparison_id`. An experiment is
        # a comparison with more than one axis, so the reader needs no new
        # concept -- only the same facts under the names it already looks for.
        "comparison_id": experiment_run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "config_path": str(config_path.resolve()) if config_path else None,
        "definition": spec.to_dict(),
        # How the attempts were scheduled. Recorded because a reader comparing two
        # runs of the same design should be able to tell whether the only
        # difference was how many ran at once -- which, if the upstream throttles,
        # is a difference that can move the result.
        "concurrency": concurrency,
        "invariant": invariant,
        "cells": summaries,
        # The reader iterates `runs` for per-participant detail; `attempts` is
        # the same list under this module's own name.
        "runs": runs,
        "attempts": runs,
        "discrimination": discrimination(summaries),
        "output_dir": str(output_dir),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "experiment.json", payload)
    (output_dir / "experiment.md").write_text(render_experiment_markdown(payload), encoding="utf-8")
    return payload


def _format_score(summary: dict[str, Any] | None) -> str:
    if not summary:
        return "-"
    if summary["range"] == 0:
        return f"{summary['median']:.3f}"
    return f"{summary['median']:.3f} ({summary['min']:.3f}–{summary['max']:.3f})"


def render_experiment_markdown(payload: dict[str, Any]) -> str:
    """Render the design, the per-cell result, and the sample-size verdict."""
    definition = payload.get("definition") or {}
    lines = [
        f"# Experiment: `{payload.get('experiment_id', '')}`",
        "",
        f"- run: `{payload.get('experiment_run_id', '')}`",
        f"- created: `{payload.get('created_at', '')}`",
        f"- task: `{definition.get('task_id', '')}`",
    ]
    if definition.get("description"):
        lines.append(f"- description: {definition['description']}")
    vary = definition.get("vary") or {}
    if vary:
        rendered = ", ".join(f"{key}={values}" for key, values in sorted(vary.items()))
        lines.append(f"- vary: {rendered}")
    fixed = definition.get("fixed") or {}
    if fixed:
        rendered = ", ".join(f"{key}={value}" for key, value in sorted(fixed.items()))
        lines.append(f"- fixed: {rendered}")
    lines.extend(
        [
            f"- repeats per cell: **{definition.get('repeats', 1)}**",
            f"- concurrency: **{payload.get('concurrency', 1)}**",
            "",
        ]
    )

    lines.extend(
        [
            "## Cells",
            "",
            "| Cell | Passed | Pass rate | 95% CI | Unmeasured | Outcome (median) "
            "| Quality (median) |",
            "|---|---:|---:|---|---:|---|---|",
        ]
    )
    for cell in payload.get("cells", []):
        rate = cell.get("pass_rate")
        rate_text = f"{rate:.0%}" if isinstance(rate, float) else "-"
        interval = cell.get("pass_rate_ci95")
        interval_text = f"{interval[0]:.2f}–{interval[1]:.2f}" if interval else "-"
        lines.append(
            "| {label} | {passed}/{measured} | {rate} | {interval} | {unmeasured} | "
            "{outcome} | {quality} |".format(
                label=cell.get("label", ""),
                passed=cell.get("passed", 0),
                measured=cell.get("measured", 0),
                rate=rate_text,
                interval=interval_text,
                unmeasured=cell.get("unmeasured", 0),
                outcome=_format_score(cell.get("outcome")),
                quality=_format_score(cell.get("quality")),
            )
        )
    lines.extend(
        [
            "",
            "Pass rate counts only attempts that produced a decision. An attempt that",
            "failed to run, or that ended `review`, is reported in **Unmeasured**: it is not",
            "evidence about the Agent, so it is excluded from the rate instead of being",
            "counted as a failure.",
            "",
        ]
    )
    verdict = payload.get("discrimination") or {}
    if verdict.get("separated") is False:
        lines.extend([f"> **无法区分。** {verdict.get('reason', '')}", ""])
    elif verdict.get("separated") is True:
        lines.extend([f"> **可区分。** {verdict.get('reason', '')}", ""])
    comparisons = verdict.get("comparisons") or []
    if comparisons:
        # The p-value is the evidence for the verdict above. Printing only the
        # conclusion would leave a reader unable to check it.
        lines.extend(
            [
                "| Pair | Fisher p | Significant |",
                "|---|---:|---|",
            ]
        )
        for entry in comparisons:
            lines.append(
                "| {left} vs {right} | {p} | {sig} |".format(
                    left=entry.get("left", ""),
                    right=entry.get("right", ""),
                    p=entry.get("p_value", ""),
                    sig="yes" if entry.get("significant") else "no",
                )
            )
        lines.append("")

    lines.extend(
        [
            "## Attempts",
            "",
            "| Cell | # | Run | Status | Outcome | Quality | Process | Decision |",
            "|---|---:|---|---|---:|---:|---:|---|",
        ]
    )
    for run in payload.get("attempts", []):
        evaluation = run.get("evaluation") or {}
        lines.append(
            "| {cell} | {attempt} | `{run_id}` | {status} | {outcome} | {quality} | "
            "{process} | {decision} |".format(
                cell=run.get("cell", ""),
                attempt=run.get("attempt", 1),
                run_id=run.get("run_id", ""),
                status=run.get("status", ""),
                outcome=evaluation.get("outcome_score", "-"),
                quality=evaluation.get("quality_score", "-"),
                process=evaluation.get("process_score", "-"),
                decision=evaluation.get("decision", "-"),
            )
        )
    lines.extend(["", f"Output: `{payload.get('output_dir', '')}`"])
    return "\n".join(lines) + "\n"


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
