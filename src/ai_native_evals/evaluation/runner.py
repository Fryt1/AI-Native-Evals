"""Execute a validated TestPlan against one completed run."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from ..adapters.run_digest import digest_run
from ..plans import CheckSpec, TestPlan, TestPlanError
from ..runs.agent_sandbox import default_readonly_mounts, run_evaluator_agent
from ..runs.lifecycle import load_manifest, update_manifest
from ..scorers.multi_dcc_host_verifier import _verify_blender, _verify_ue5
from .contracts import CheckResult, EvaluationReport

Evaluator = Callable[["EvaluationContext", CheckSpec], CheckResult]


class EvaluationError(RuntimeError):
    """Raised when a TestPlan cannot be executed."""


@dataclass(slots=True)
class EvaluationContext:
    """Mutable context shared by checks in one plan execution."""

    run_dir: Path
    manifest: dict[str, Any]
    plan: TestPlan
    repo_root: Path
    results: dict[str, CheckResult]

    @property
    def paths(self) -> dict[str, Any]:
        """Resolved run paths from the manifest."""
        value = self.manifest.get("paths")
        return value if isinstance(value, dict) else {}

    @property
    def workspace_dir(self) -> Path:
        """Host path of this run's canonical workspace."""
        return Path(str(self.paths.get("workspace", self.run_dir / "workspace")))

    @property
    def evidence_dir(self) -> Path:
        """Host path where evaluator evidence is written."""
        return Path(str(self.paths.get("evidence", self.run_dir / "evidence")))

    @property
    def trace_dir(self) -> Path:
        """Host path containing the subject Agent trace."""
        return Path(str(self.paths.get("trace", self.run_dir / "trace")))


_REGISTRY: dict[str, Evaluator] = {}


def register_evaluator(name: str) -> Callable[[Evaluator], Evaluator]:
    """Register a reusable evaluator implementation by stable id."""

    def decorator(function: Evaluator) -> Evaluator:
        if not name or name in _REGISTRY:
            raise ValueError(f"evaluator id is empty or already registered: {name!r}")
        _REGISTRY[name] = function
        return function

    return decorator


def available_evaluators() -> tuple[str, ...]:
    """Return registered evaluator ids."""
    return tuple(sorted(_REGISTRY))


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
        status = {"review": "review", "skip": "skipped"}.get(check.on_error, "error")
        return CheckResult(
            check_id=check.id,
            phase=check.phase,
            evaluator=check.evaluator,
            status=status,  # type: ignore[arg-type]
            error=f"{type(exc).__name__}: {exc}",
            started_at=started,
            finished_at=_utc_now(),
        )
    if result.check_id != check.id:
        raise EvaluationError(
            f"evaluator {check.evaluator!r} returned check id "
            f"{result.check_id!r}; expected {check.id!r}"
        )
    return result


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

    hard_ids = set(context.plan.hard_checks)
    hard_failures = [
        result
        for result in checks
        if result.check_id in hard_ids and result.status != "passed"
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
    outcome_score = _binary_score(outcome)
    weights = {check.id: check.weight for check in context.plan.checks}
    quality_score = _weighted_score(quality, weights)
    process_score = _weighted_score(process, weights)

    if hard_failures:
        decision = "fail"
    elif (
        context.plan.quality_threshold is not None
        and quality_score is not None
        and quality_score < context.plan.quality_threshold
    ):
        decision = "fail"
    elif any(result.status in {"review", "blocked", "error"} for result in checks):
        decision = "review"
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


# ---------------------------------------------------------------------------
# Built-in evaluator implementations
# ---------------------------------------------------------------------------
@register_evaluator("script.file_exists.v1")
def _file_exists(context: EvaluationContext, check: CheckSpec) -> CheckResult:
    """Check a direct container or host path exists."""
    started = _utc_now()
    raw_path = check.input.get("path") or check.config.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return _error_result(check, started, "file_exists requires input.path or config.path")
    path = _resolve_path(context, raw_path)
    passed = path.is_file()
    return CheckResult(
        check_id=check.id,
        phase=check.phase,
        evaluator=check.evaluator,
        status="passed" if passed else "failed",
        passed=passed,
        score=1.0 if passed else 0.0,
        details={"path": str(path), "exists": passed},
        evidence_refs=(str(path),),
        error=None if passed else f"file does not exist: {path}",
        started_at=started,
        finished_at=_utc_now(),
    )


@register_evaluator("script.text_equals.v1")
def _text_equals(context: EvaluationContext, check: CheckSpec) -> CheckResult:
    """Compare a selected artifact's exact UTF-8 contents."""
    started = _utc_now()
    raw_artifact = check.input.get("artifact") or check.input.get("path")
    if not isinstance(raw_artifact, str) or not raw_artifact:
        return _error_result(check, started, "text_equals requires input.artifact or input.path")
    path = _resolve_artifact_reference(context, raw_artifact)
    expected = check.config.get("expected_text")
    if not isinstance(expected, str):
        return _error_result(check, started, "text_equals requires config.expected_text")
    try:
        actual = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return CheckResult(
            check_id=check.id,
            phase=check.phase,
            evaluator=check.evaluator,
            status="failed",
            passed=False,
            score=0.0,
            details={"path": str(path), "expected": expected},
            evidence_refs=(str(path),),
            error=f"file does not exist: {path}",
            started_at=started,
            finished_at=_utc_now(),
        )
    except OSError as exc:
        return _error_result(check, started, f"could not read {path}: {exc}")
    passed = actual == expected
    return CheckResult(
        check_id=check.id,
        phase=check.phase,
        evaluator=check.evaluator,
        status="passed" if passed else "failed",
        passed=passed,
        score=1.0 if passed else 0.0,
        details={
            "path": str(path),
            "expected": expected,
            "actual": actual,
            "expected_length": len(expected),
            "actual_length": len(actual),
        },
        evidence_refs=(str(path),),
        error=None if passed else "file contents do not match expected_text",
        started_at=started,
        finished_at=_utc_now(),
    )


@register_evaluator("script.json_contract.v1")
def _json_contract(context: EvaluationContext, check: CheckSpec) -> CheckResult:
    """Validate a JSON artifact against task-declared structural constraints."""
    started = _utc_now()
    raw_artifact = check.input.get("artifact") or check.input.get("path")
    if not isinstance(raw_artifact, str) or not raw_artifact:
        return _error_result(check, started, "json_contract requires input.artifact or input.path")
    try:
        path = _resolve_artifact_reference(context, raw_artifact)
    except (EvaluationError, OSError) as exc:
        return _error_result(check, started, str(exc))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _error_result(check, started, f"JSON artifact does not exist: {path}")
    except (OSError, json.JSONDecodeError) as exc:
        return _error_result(check, started, f"could not parse JSON artifact {path}: {exc}")
    if not isinstance(payload, dict):
        return _error_result(check, started, "JSON contract root must be an object")

    config = check.config
    problems: list[str] = []
    checks: dict[str, Any] = {}
    required = config.get("required_fields", [])
    if not isinstance(required, list):
        return _error_result(check, started, "json_contract required_fields must be a list")
    for field_name in required:
        if not isinstance(field_name, str) or not field_name:
            return _error_result(
                check, started, "json_contract required_fields must contain strings"
            )
        found, value = _get_field(payload, field_name)
        checks[f"required:{field_name}"] = found
        if not found:
            problems.append(f"missing required field: {field_name}")

    expected_values = config.get("field_values", {})
    if not isinstance(expected_values, dict):
        return _error_result(check, started, "json_contract field_values must be a mapping")
    for field_name, expected in expected_values.items():
        found, actual = _get_field(payload, str(field_name))
        checks[f"value:{field_name}"] = {"expected": expected, "actual": actual}
        if not found or actual != expected:
            problems.append(f"field {field_name!r}={actual!r}, expected {expected!r}")

    non_empty = config.get("non_empty_fields", [])
    if not isinstance(non_empty, list):
        return _error_result(check, started, "json_contract non_empty_fields must be a list")
    for field_name in non_empty:
        found, actual = _get_field(payload, str(field_name))
        valid = found and actual not in (None, "", [], {})
        checks[f"non_empty:{field_name}"] = valid
        if not valid:
            problems.append(f"field {field_name!r} must be non-empty")

    field_types = config.get("field_types", {})
    if not isinstance(field_types, dict):
        return _error_result(check, started, "json_contract field_types must be a mapping")
    for field_name, expected_type in field_types.items():
        found, actual = _get_field(payload, str(field_name))
        valid = found and _json_type_name(actual) == str(expected_type)
        checks[f"type:{field_name}"] = {
            "expected": expected_type,
            "actual": _json_type_name(actual) if found else None,
        }
        if not valid:
            problems.append(
                f"field {field_name!r} has type "
                f"{_json_type_name(actual) if found else None!r}, expected {expected_type!r}"
            )

    array_min_lengths = config.get("array_min_lengths", {})
    if not isinstance(array_min_lengths, dict):
        return _error_result(check, started, "json_contract array_min_lengths must be a mapping")
    for field_name, minimum in array_min_lengths.items():
        found, actual = _get_field(payload, str(field_name))
        valid = isinstance(actual, list) and len(actual) >= int(minimum)
        checks[f"array_min:{field_name}"] = {
            "minimum": minimum,
            "actual": len(actual) if isinstance(actual, list) else None,
        }
        if not valid:
            problems.append(f"field {field_name!r} must contain at least {minimum} items")

    passed = not problems
    return CheckResult(
        check_id=check.id,
        phase=check.phase,
        evaluator=check.evaluator,
        status="passed" if passed else "failed",
        passed=passed,
        score=1.0 if passed else 0.0,
        details={"path": str(path), "payload": payload, "checks": checks, "problems": problems},
        evidence_refs=(str(path),),
        error=None if passed else "; ".join(problems),
        started_at=started,
        finished_at=_utc_now(),
    )


def _get_field(payload: dict[str, Any], field_name: str) -> tuple[bool, Any]:
    """Read a dotted field path from a JSON object."""
    current: Any = payload
    for part in field_name.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _json_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


@register_evaluator("script.blender_scene.v1")
def _blender_scene(context: EvaluationContext, check: CheckSpec) -> CheckResult:
    """Open a selected Blender scene and verify task-specific expectations."""
    started = _utc_now()
    raw_artifact = check.input.get("artifact") or check.input.get("path")
    if not isinstance(raw_artifact, str) or not raw_artifact:
        return _error_result(check, started, "blender_scene requires input.artifact or input.path")
    try:
        scene = _resolve_artifact_reference(context, raw_artifact)
    except (EvaluationError, OSError) as exc:
        return _error_result(check, started, str(exc))
    expectations = check.config.get("objects", [])
    if not isinstance(expectations, list):
        return _error_result(check, started, "blender_scene config.objects must be a list")
    try:
        passed, findings = _verify_blender(scene, expectations)
    except (OSError, ValueError) as exc:
        return _error_result(check, started, f"Blender verification failed: {exc}")
    return CheckResult(
        check_id=check.id,
        phase=check.phase,
        evaluator=check.evaluator,
        status="passed" if passed else "failed",
        passed=passed,
        score=1.0 if passed else 0.0,
        details=findings,
        evidence_refs=(str(scene),),
        error=None if passed else "Blender scene did not satisfy expectations",
        started_at=started,
        finished_at=_utc_now(),
    )


@register_evaluator("script.ue5_state.v1")
def _ue5_state(context: EvaluationContext, check: CheckSpec) -> CheckResult:
    """Read back UE5 state through a fresh MCP session."""
    started = _utc_now()
    expectations = check.config.get("expectations")
    if not isinstance(expectations, dict):
        return _error_result(check, started, "ue5_state requires config.expectations mapping")
    url = str(
        check.config.get(
            "mcp_url",
            "http://127.0.0.1:8000/mcp",
        )
    )
    try:
        passed, findings = _verify_ue5(
            url,
            expectations,
            timeout_seconds=check.timeout_seconds or 60,
        )
    except (OSError, ValueError) as exc:
        return _error_result(check, started, f"UE5 verification failed: {exc}")
    return CheckResult(
        check_id=check.id,
        phase=check.phase,
        evaluator=check.evaluator,
        status="passed" if passed else "failed",
        passed=passed,
        score=1.0 if passed else 0.0,
        details=findings,
        evidence_refs=(),
        error=None if passed else "UE5 state did not satisfy expectations",
        started_at=started,
        finished_at=_utc_now(),
    )


@register_evaluator("agent.artifact_locator.v1")
def _artifact_locator(context: EvaluationContext, check: CheckSpec) -> CheckResult:
    """Ask an evaluator Agent in Docker to locate a candidate artifact."""
    started = _utc_now()
    roots = check.input.get("roots") or ["/workspace/output"]
    if not isinstance(roots, list) or not all(isinstance(root, str) for root in roots):
        return _error_result(
            check, started, "artifact_locator input.roots must be a list of strings"
        )
    expected_name = check.config.get("expected_name")
    artifact_kind = check.config.get("artifact_kind", "artifact")
    output_filename = "locator-result.json"
    base_prompt = _load_text_config(context, check.config.get("prompt"))
    prompt = _locator_prompt(
        base_prompt=base_prompt,
        roots=roots,
        expected_name=str(expected_name) if expected_name else None,
        artifact_kind=str(artifact_kind),
        output_filename=output_filename,
    )
    result = run_evaluator_agent(
        context.run_dir,
        role=f"outcome-locator-{check.id}",
        prompt=prompt,
        mounts=default_readonly_mounts(context.manifest),
        output_filename=output_filename,
        image=str(check.config.get("image")) if check.config.get("image") else None,
        model=str(check.config.get("model")) if check.config.get("model") else None,
        protocol=str(check.config.get("protocol")) if check.config.get("protocol") else None,
        reasoning_effort=(
            str(check.config.get("reasoning_effort"))
            if check.config.get("reasoning_effort")
            else None
        ),
        timeout_seconds=check.timeout_seconds or 900,
        repo_root=context.repo_root,
    )
    payload = _load_agent_json(result.output_path, result.last_message_path)
    if result.status != "completed":
        return _error_result(
            check,
            started,
            result.failure_message or f"locator Agent exited with {result.exit_code}",
        )
    locator_status = payload.get("status") if isinstance(payload, dict) else None
    if locator_status not in {"found", "pass"}:
        return _error_result(
            check,
            started,
            f"locator did not find an unambiguous artifact: status={locator_status!r}",
        )
    selected = payload.get("selected_artifact") if isinstance(payload, dict) else None
    if not isinstance(selected, str) or not selected:
        return _error_result(check, started, "locator did not return selected_artifact")
    try:
        host_path = _validate_selected_artifact(selected, roots, context.workspace_dir)
    except ValueError as exc:
        return _error_result(check, started, str(exc))
    candidates = payload.get("candidates", [])
    return CheckResult(
        check_id=check.id,
        phase=check.phase,
        evaluator=check.evaluator,
        status="passed",
        passed=True,
        score=1.0,
        details={
            "selected_artifact": selected,
            "selected_host_path": str(host_path),
            "candidates": candidates,
            "locator_agent": result.to_dict(),
        },
        evidence_refs=(str(host_path), str(result.last_message_path)),
        started_at=started,
        finished_at=_utc_now(),
    )


@register_evaluator("agent.quality_judge.v1")
def _quality_judge(context: EvaluationContext, check: CheckSpec) -> CheckResult:
    """Run a read-only Judge Agent with a versioned prompt and rubric."""
    started = _utc_now()
    prompt_text = _load_text_config(context, check.config.get("prompt"))
    rubric = _load_structured_config(context, check.config.get("rubric"))
    if not prompt_text:
        return _error_result(check, started, "quality_judge requires config.prompt")
    if rubric is None:
        return _error_result(check, started, "quality_judge requires config.rubric")
    output_filename = "quality-result.json"
    input_description = _quality_input_description(context, check)
    prompt = _quality_prompt(prompt_text, rubric, output_filename, input_description)
    result = run_evaluator_agent(
        context.run_dir,
        role=f"quality-judge-{check.id}",
        prompt=prompt,
        mounts=default_readonly_mounts(context.manifest),
        output_filename=output_filename,
        image=str(check.config.get("image")) if check.config.get("image") else None,
        model=str(check.config.get("model")) if check.config.get("model") else None,
        protocol=str(check.config.get("protocol")) if check.config.get("protocol") else None,
        reasoning_effort=(
            str(check.config.get("reasoning_effort"))
            if check.config.get("reasoning_effort")
            else None
        ),
        timeout_seconds=check.timeout_seconds or 1200,
        repo_root=context.repo_root,
    )
    payload = _load_agent_json(result.output_path, result.last_message_path)
    if result.status != "completed":
        return _error_result(
            check,
            started,
            result.failure_message or f"quality Judge exited with {result.exit_code}",
        )
    score = payload.get("score") if isinstance(payload, dict) else None
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return _error_result(check, started, "quality Judge did not return numeric score")
    score_float = float(score)
    if not 0.0 <= score_float <= 1.0:
        return _error_result(check, started, "quality Judge score must be between 0 and 1")
    judge_status = payload.get("status", "pass")
    if judge_status not in {"pass", "fail", "review"}:
        judge_status = "review"
    return CheckResult(
        check_id=check.id,
        phase=check.phase,
        evaluator=check.evaluator,
        status={"pass": "passed", "fail": "failed", "review": "review"}[judge_status],  # type: ignore[arg-type]
        passed=judge_status == "pass",
        score=score_float,
        details={"judge": payload, "judge_agent": result.to_dict()},
        evidence_refs=(str(result.last_message_path),),
        error=None,
        started_at=started,
        finished_at=_utc_now(),
    )


@register_evaluator("trace.process_analyzer.v1")
def _process_analyzer(context: EvaluationContext, check: CheckSpec) -> CheckResult:
    """Measure observable process signals without pretending to judge quality."""
    started = _utc_now()
    trace_path = context.trace_dir / "agent-container.log"
    if not trace_path.is_file():
        return CheckResult(
            check_id=check.id,
            phase=check.phase,
            evaluator=check.evaluator,
            status="skipped",
            passed=None,
            details={"reason": f"trace does not exist: {trace_path}"},
            evidence_refs=(),
            started_at=started,
            finished_at=_utc_now(),
        )
    digest = digest_run(context.run_dir)
    stats = _json_safe(digest.get("stats", {}))
    struggle = _json_safe(digest.get("struggle", {}))
    mcp_total = stats.get("mcp_calls_total") or 0
    shell_total = stats.get("shell_commands_total") or 0
    total = mcp_total + shell_total
    failed = (stats.get("mcp_calls_failed") or 0) + (
        stats.get("shell_commands_failed") or 0
    )
    score = None if not total else round(max(0.0, 1.0 - failed / total), 3)
    return CheckResult(
        check_id=check.id,
        phase=check.phase,
        evaluator=check.evaluator,
        status="observed",
        passed=True,
        score=score,
        details={
            "stats": stats,
            "struggle": struggle,
            "note": (
                "process score is provisional telemetry over observable actions; "
                "task policy does not use it as a hard gate"
            ),
        },
        evidence_refs=(str(context.trace_dir / "agent-container.log"),),
        started_at=started,
        finished_at=_utc_now(),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _locator_prompt(
    *,
    base_prompt: str | None,
    roots: list[str],
    expected_name: str | None,
    artifact_kind: str,
    output_filename: str,
) -> str:
    """Build a task-injected prompt from a versioned locator template."""
    if expected_name:
        name_clause = f"The expected name is exactly {expected_name!r}."
    else:
        name_clause = (
            "There is no fixed filename; identify the best candidate from the task context."
        )
    roots_json = json.dumps(roots, ensure_ascii=False)
    json_shape = (
        '{{"status":"found|review|not_found",'
        '"selected_artifact":"/workspace/... or null",'
        '"candidates":[{{"path":"/workspace/...","reason":"..."}}],'
        '"reason":"..."}}'
    )
    stable = base_prompt or "You are the Outcome Artifact Locator for an evaluation run."
    return "\n".join(
        [
            stable.rstrip(),
            "",
            f"Artifact kind: {artifact_kind}.",
            f"Search only these container roots: {roots_json}.",
            name_clause,
            "Return JSON only with this shape:",
            json_shape,
            f"Write the same JSON to /workspace/trace/{output_filename},",
            "then return the JSON as your final message.",
            "",
        ]
    )


def _quality_prompt(
    prompt_text: str,
    rubric: Any,
    output_filename: str,
    input_description: str,
) -> str:
    """Build the stable prompt for a read-only Quality Judge Agent."""
    rubric_json = json.dumps(rubric, ensure_ascii=False, indent=2)
    json_shape = (
        '{{"status":"pass|fail|review","score":0.0,"confidence":0.0,'
        '"criteria":[],"hard_failures":[],"suggestions":[]}}'
    )
    return "\n".join(
        [
            prompt_text,
            "",
            "You are a read-only Quality Judge. Do not modify the workspace or evidence.",
            "Inspect the candidate result and the available evidence under /workspace.",
            f"Candidate input for this check: {input_description}",
            "Use this rubric exactly:",
            rubric_json,
            "Return JSON only with this shape:",
            json_shape,
            "The score must be a number from 0 to 1.",
            "Cite concrete evidence in each criterion.",
            f"Write the same JSON to /workspace/trace/{output_filename},",
            "then return the JSON as your final message.",
            "",
        ]
    )


def _quality_input_description(context: EvaluationContext, check: CheckSpec) -> str:
    """Resolve quality input references to container-visible paths for the Judge."""
    raw = check.input.get("artifact") or check.input.get("path")
    if not isinstance(raw, str) or not raw:
        return json.dumps(check.input, ensure_ascii=False)
    try:
        host_path = _resolve_artifact_reference(context, raw)
    except (EvaluationError, OSError):
        return raw
    try:
        relative = host_path.relative_to(context.workspace_dir.resolve())
    except ValueError:
        return raw
    return f"/workspace/{relative.as_posix()}"


def _load_agent_json(output_path: Path | None, last_message_path: Path) -> dict[str, Any]:
    for path in (output_path, last_message_path):
        if not path or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        parsed = _extract_json(text)
        if isinstance(parsed, dict):
            return parsed
    return {}


def _extract_json(text: str) -> Any:
    """Extract the first JSON object from a final message or log-like text."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    while start >= 0:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : index + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def _resolve_artifact_reference(context: EvaluationContext, reference: str) -> Path:
    """Resolve a direct path or ``check-id.field`` reference."""
    if "." in reference and not reference.startswith(("/", "\\")):
        check_id, field = reference.split(".", 1)
        previous = context.results.get(check_id)
        if previous is None:
            raise EvaluationError(f"artifact reference points to unknown check: {check_id}")
        value = previous.details.get(field)
        if not isinstance(value, str) or not value:
            raise EvaluationError(f"artifact reference has no string field: {reference}")
        return _resolve_path(context, value)
    return _resolve_path(context, reference)


def _resolve_path(context: EvaluationContext, raw_path: str) -> Path:
    """Resolve a container path under /workspace or a run-relative path safely."""
    if raw_path.startswith("/workspace/") or raw_path == "/workspace":
        relative = raw_path.removeprefix("/workspace/")
        return (context.workspace_dir / relative).resolve()
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate.resolve()
    return (context.run_dir / candidate).resolve()


def _validate_selected_artifact(selected: str, roots: list[str], workspace: Path) -> Path:
    """Validate an Agent-selected container path and map it to the host."""
    if not selected.startswith("/workspace/"):
        raise ValueError("selected artifact must be a /workspace/... container path")
    selected_posix = PurePosixPath(selected)
    allowed = False
    for root in roots:
        root_posix = PurePosixPath(root)
        try:
            selected_posix.relative_to(root_posix)
        except ValueError:
            continue
        allowed = True
        break
    if not allowed:
        raise ValueError(f"selected artifact is outside allowed roots: {selected}")
    host_path = (workspace / selected.removeprefix("/workspace/")).resolve()
    try:
        host_path.relative_to(workspace.resolve())
    except ValueError as exc:
        raise ValueError(f"selected artifact escapes workspace: {selected}") from exc
    if not host_path.is_file():
        raise ValueError(f"selected artifact does not exist on host: {host_path}")
    return host_path


def _load_text_config(context: EvaluationContext, value: Any) -> str | None:
    if isinstance(value, str):
        candidate = Path(value)
        path = candidate if candidate.is_absolute() else context.repo_root / candidate
        if path.is_file():
            return path.read_text(encoding="utf-8")
        return value
    return None


def _load_structured_config(context: EvaluationContext, value: Any) -> Any:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        candidate = Path(value)
        path = candidate if candidate.is_absolute() else context.repo_root / candidate
        if not path.is_file():
            return None
        try:
            import yaml

            return yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    return None


def _json_safe(value: Any) -> Any:
    """Convert mappings/tuples from telemetry into ordinary JSON values."""
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _binary_score(results: list[CheckResult]) -> float | None:
    if not results:
        return None
    values = [1.0 if result.status == "passed" else 0.0 for result in results]
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


def _error_result(check: CheckSpec, started: str, message: str) -> CheckResult:
    return CheckResult(
        check_id=check.id,
        phase=check.phase,
        evaluator=check.evaluator,
        status="error",
        passed=False,
        score=0.0 if check.phase == "outcome" else None,
        error=message,
        started_at=started,
        finished_at=_utc_now(),
    )


def _write_check_result(evidence_dir: Path, result: CheckResult) -> None:
    checks_dir = evidence_dir / "checks"
    checks_dir.mkdir(parents=True, exist_ok=True)
    _write_json(checks_dir / f"{result.check_id}.json", result.to_dict())


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
