"""Execute a validated TestPlan against one completed run.

This module owns the engine and nothing else: how checks are ordered, how a
failed dependency blocks its dependents, and how results aggregate into a
verdict. What a check *is* lives in `builtin.py`; what both halves need lives in
`support.py`.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..plans import CheckSpec, TestPlan, TestPlanError
from ..runs.lifecycle import load_manifest, update_manifest

# Imported for its side effect: the decorators register every built-in evaluator
# id. Without it `available_evaluators()` reports nothing and each plan fails as
# "evaluator is not registered".
from . import builtin as _builtin  # noqa: F401
from .contracts import CheckResult, EvaluationReport
from .support import (
    _REGISTRY,
    EvaluationContext,
    EvaluationError,
    _error_result,
    _repo_root,
    _utc_now,
    _write_check_result,
    _write_json,
    available_evaluators,
    register_evaluator,
)

__all__ = [
    "EvaluationContext",
    "EvaluationError",
    "available_evaluators",
    "evaluate_run",
    # Re-exported because it is the documented seam for adding an evaluator, and
    # `runner` is where callers and the plugin entry point already look for it.
    # Moving the definition into `support` must not move the seam.
    "register_evaluator",
]


def evaluate_run(
    run_dir: str | Path,
    *,
    repo_root: str | Path | None = None,
    write_evidence: bool = True,
) -> dict[str, Any]:
    """Execute the run's TestPlan and return its persisted report mapping."""
    run_dir = Path(run_dir).resolve()
    manifest = load_manifest(run_dir)
    raw_plan = manifest.get("run", {}).get("test_plan")
    try:
        plan = TestPlan.from_mapping(raw_plan)
    except TestPlanError as exc:
        raise EvaluationError(str(exc)) from exc

    run = manifest.get("run")
    if not isinstance(run, dict):
        raise EvaluationError("run manifest has no run metadata")
    context = EvaluationContext(
        run_dir=run_dir,
        manifest=manifest,
        plan=plan,
        repo_root=(Path(repo_root).resolve() if repo_root else _repo_root()),
        results={},
    )

    check_results: list[CheckResult] = []
    for check in plan.ordered_checks():
        blocked_by = [
            dependency
            for dependency in check.depends_on
            if context.results.get(dependency) is None
            or context.results[dependency].status
            not in {"passed", "observed"}
        ]
        if blocked_by:
            result = CheckResult(
                check_id=check.id,
                phase=check.phase,
                evaluator=check.evaluator,
                status="blocked",
                error=f"blocked by checks: {blocked_by}",
                started_at=_utc_now(),
                finished_at=_utc_now(),
            )
        else:
            result = _execute_check(context, check)
        context.results[check.id] = result
        check_results.append(result)
        if write_evidence:
            _write_check_result(context.evidence_dir, result)

    report = _aggregate(context, check_results)
    payload = report.to_dict()
    if write_evidence:
        context.evidence_dir.mkdir(parents=True, exist_ok=True)
        evaluation_path = context.evidence_dir / "evaluation.json"
        _write_json(evaluation_path, payload)
        _write_json(context.evidence_dir / "verdict.json", payload)
        update_manifest(
            run_dir,
            evaluation={
                "decision": report.decision,
                "path": str(evaluation_path),
                "evaluated_at": _utc_now(),
            },
        )
    return payload


def _execute_check(context: EvaluationContext, check: CheckSpec) -> CheckResult:
    """Execute one evaluator and normalize unexpected errors."""
    evaluator = _REGISTRY.get(check.evaluator)
    started = _utc_now()
    if evaluator is None:
        return _error_result(
            check,
            started,
            f"evaluator is not registered: {check.evaluator!r}; available={available_evaluators()}",
        )
    try:
        result = evaluator(context, check)
    except Exception as exc:  # evaluator boundary: keep the plan report durable
        # An unhandled exception here is a bug in the evaluator, not a finding
        # about the Agent. Letting it inherit `on_error` let a Task that declares
        # `on_error: fail` -- which both shipped Tasks do, for a Locator that can
        # return without an answer -- record our own crash as the Agent scoring
        # zero. It stays `error`, flagged as ours, and `_effective_status` reads
        # that as undetermined whatever the check's policy says.
        return CheckResult(
            check_id=check.id,
            phase=check.phase,
            evaluator=check.evaluator,
            status="error",
            details={"evaluator_fault": True},
            error=f"evaluator raised {type(exc).__name__}: {exc}",
            started_at=started,
            finished_at=_utc_now(),
        )
    if result.check_id != check.id:
        raise EvaluationError(
            f"evaluator {check.evaluator!r} returned check id "
            f"{result.check_id!r}; expected {check.id!r}"
        )
    return result


def _effective_status(result: CheckResult, plan: TestPlan) -> str:
    """Collapse a CheckResult into the verdict it carries for aggregation.

    The distinction that decides a run is *determinate negative* versus
    *undetermined*. An evaluator that could not reach an answer, a dependency that
    never ran, or a check the task chose to skip tells us nothing about the Agent,
    so it must not be silently reported as a failed check.

    ``on_error`` says how a check that **could not run** is read. It deliberately
    does not cover an evaluator that raised: that is a defect in this repository,
    and reading it as an Agent failure would let our own bug score a run. Such a
    result carries ``details["evaluator_fault"]`` and is undetermined regardless
    of policy.
    """
    if result.status == "error":
        if result.details.get("evaluator_fault"):
            return "error"
        check = next((item for item in plan.checks if item.id == result.check_id), None)
        policy = check.on_error if check is not None else "fail"
        return {"fail": "failed", "review": "review", "skip": "skipped"}[policy]
    return result.status


def _aggregate(context: EvaluationContext, checks: list[CheckResult]) -> EvaluationReport:
    """Apply task policy without collapsing diagnostic details."""
    if not checks:
        return EvaluationReport(
            run_id=str(context.manifest["run"]["run_id"]),
            task_id=str(context.manifest["run"]["task_id"]),
            decision="not_evaluable",
            outcome_score=None,
            quality_score=None,
            process_score=None,
            checks=(),
            errors=("test plan contains no checks",),
        )

    plan = context.plan
    effective = {result.check_id: _effective_status(result, plan) for result in checks}
    hard_ids = set(plan.hard_checks)
    hard_failures = [
        result
        for result in checks
        if result.check_id in hard_ids and effective[result.check_id] == "failed"
    ]
    errors = tuple(
        f"{result.check_id}: {result.error}"
        for result in checks
        if result.error
    )

    outcome = [result for result in checks if result.phase == "outcome"]
    quality = [
        result for result in checks if result.phase == "quality" and result.score is not None
    ]
    process = [
        result for result in checks if result.phase == "process" and result.score is not None
    ]
    outcome_score = _binary_score(outcome, effective)
    weights = {check.id: check.weight for check in plan.checks}
    quality_score = _weighted_score(quality, weights)
    process_score = _weighted_score(process, weights)

    determinate = [
        status for status in effective.values() if status in {"passed", "failed", "observed"}
    ]
    # A missing verdict is not a negative verdict. A crashed evaluator, a blocked
    # dependency, or a plan that skipped every check must never be reported as a
    # pass; it follows the plan's uncertain_result policy instead.
    undetermined = (
        not determinate
        or any(status in {"review", "blocked", "error"} for status in effective.values())
    )
    # `pass` is a claim about the Agent, so it needs at least one determinate
    # verdict on the Agent's *output*. Process telemetry describes how the work
    # was done and says nothing about whether it was done, so a plan that checks
    # only the trace cannot support a pass -- it was previously reported as one
    # with every score null.
    has_outcome_verdict = any(
        effective.get(result.check_id) in {"passed", "failed"}
        for result in checks
        if result.phase == "outcome"
    )

    if hard_failures:
        decision = "fail"
    elif (
        plan.quality_threshold is not None
        and quality_score is not None
        and quality_score < plan.quality_threshold
    ):
        decision = "fail"
    elif undetermined or not has_outcome_verdict:
        decision = plan.uncertain_result
    else:
        decision = "pass"

    return EvaluationReport(
        run_id=str(context.manifest["run"]["run_id"]),
        task_id=str(context.manifest["run"]["task_id"]),
        decision=decision,
        outcome_score=outcome_score,
        quality_score=quality_score,
        process_score=process_score,
        checks=tuple(checks),
        errors=errors,
    )


def _binary_score(results: list[CheckResult], effective: Mapping[str, str]) -> float | None:
    """Average the outcome checks that produced a determinate verdict.

    Undetermined checks are excluded rather than scored as zero, so a broken
    evaluator cannot masquerade as an Agent that scored 0.
    """
    values = [
        1.0 if effective.get(result.check_id) == "passed" else 0.0
        for result in results
        if effective.get(result.check_id) in {"passed", "failed"}
    ]
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def _weighted_score(
    results: list[CheckResult], weights: dict[str, float]
) -> float | None:
    if not results:
        return None
    weighted = 0.0
    total_weight = 0.0
    for result in results:
        score = result.score
        weight = weights.get(result.check_id, 1.0)
        if score is None or weight <= 0.0:
            continue
        weighted += score * weight
        total_weight += weight
    return round(weighted / total_weight, 3) if total_weight else None
