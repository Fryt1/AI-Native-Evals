"""The built-in evaluator implementations.

Each one is a reusable check that a Task selects by id and parameterises with
`input` and `config`. They were previously defined inside `runner.py`, alongside
the code that executes a plan, which conflated two jobs: running a TestPlan, and
being one of the things a TestPlan can run. Adding a check meant editing the
engine.

Registration is by decorator, so importing this module is what makes the ids
resolvable. `runner` imports it for exactly that reason.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..adapters.run_digest import digest_run
from ..plans import CheckSpec
from ..runs.agent_sandbox import default_readonly_mounts, run_evaluator_agent
from ..scorers.multi_dcc_host_verifier import (
    _verify_blender,
    _verify_blender_live,
    _verify_ue5,
)
from .contracts import CheckResult
from .support import (
    EvaluationContext,
    EvaluationError,
    _error_result,
    _json_safe,
    _load_structured_config,
    _load_text_config,
    _resolve_artifact_reference,
    _resolve_path,
    _utc_now,
    _validate_selected_artifact,
    _write_check_artifact,
    register_evaluator,
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


def _merge_normalized_stats(
    current: Mapping[str, Any], events: list[dict[str, Any]]
) -> dict[str, Any]:
    """Project the shared trace vocabulary into provider-neutral counters."""
    stats = dict(current)
    calls = [
        event for event in events if event.get("type") in {"tool_call", "command_started"}
    ]
    results = [
        event for event in events if event.get("type") in {"tool_result", "command_completed"}
    ]
    mcp_calls = [event for event in calls if _is_normalized_mcp_event(event)]
    shell_calls = [event for event in calls if _is_normalized_shell_event(event)]
    generic_calls = [
        event for event in calls if event not in mcp_calls and event not in shell_calls
    ]
    failed_ids = {
        _normalized_event_id(event)
        for event in results
        if _normalized_event_failed(event)
    }
    failed = sum(1 for event in results if _normalized_event_failed(event))
    generic_failed = sum(
        1
        for event in generic_calls
        if _normalized_event_id(event) in failed_ids
    )
    shell_failed = sum(
        1
        for event in shell_calls
        if _normalized_event_id(event) in failed_ids
    )
    mcp_failed = sum(
        1
        for event in mcp_calls
        if _normalized_event_id(event) in failed_ids
    )
    stats.update(
        {
            "event_items": len(events),
            "mcp_calls_total": max(int(stats.get("mcp_calls_total") or 0), len(mcp_calls)),
            "shell_commands_total": max(
                int(stats.get("shell_commands_total") or 0), len(shell_calls)
            ),
            "tool_calls_total": len(generic_calls),
            "mcp_calls_failed": max(int(stats.get("mcp_calls_failed") or 0), mcp_failed),
            "shell_commands_failed": max(
                int(stats.get("shell_commands_failed") or 0), shell_failed
            ),
            "tool_calls_failed": generic_failed,
            "actions_total": len(mcp_calls) + len(shell_calls) + len(generic_calls),
            "actions_failed": failed if results else 0,
            "agent_messages": sum(1 for event in events if event.get("type") == "agent_message"),
            "reasoning_blocks": sum(1 for event in events if event.get("type") == "reasoning"),
        }
    )
    return stats


def _normalized_payload(event: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, Mapping) else {}


def _normalized_value(event: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = _normalized_payload(event)
    item = payload.get("item") if isinstance(payload.get("item"), Mapping) else {}
    update = payload.get("update") if isinstance(payload.get("update"), Mapping) else {}
    return {**item, **update}


def _normalized_event_id(event: Mapping[str, Any]) -> str:
    value = _normalized_value(event)
    return str(value.get("id") or value.get("toolCallId") or event.get("seq", ""))


def _is_normalized_mcp_event(event: Mapping[str, Any]) -> bool:
    payload = _normalized_payload(event)
    value = _normalized_value(event)
    item = payload.get("item")
    if isinstance(item, Mapping) and item.get("type") == "mcp_tool_call":
        return True
    return any(value.get(key) for key in ("server", "serverName", "mcp_server"))


def _is_normalized_shell_event(event: Mapping[str, Any]) -> bool:
    if event.get("type") in {"command_started", "command_completed"}:
        return True
    value = _normalized_value(event)
    title = str(value.get("title", value.get("tool", ""))).lower()
    return title in {"bash", "pwsh", "shell", "terminal"} or bool(value.get("command"))


def _normalized_event_failed(event: Mapping[str, Any]) -> bool:
    value = _normalized_value(event)
    status = str(value.get("status", "")).lower()
    return bool(value.get("error")) or status in {"failed", "error"} or (
        value.get("exit_code") not in (None, 0)
    )


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


@register_evaluator("script.blender_live_scene.v1")
def _blender_live_scene(context: EvaluationContext, check: CheckSpec) -> CheckResult:
    """Read the host Blender scene through MCP, without a saved file.

    Blender is a host service. A `.blend` it saves lands on the *host*
    filesystem, and its notion of `/workspace` is its own working directory --
    not the container path of the same name. A Task that needs a saved file is
    therefore coupled to how the host was started.

    Reading the live scene avoids that coupling entirely: the check opens its
    own MCP connection to the running Blender and asks what is in the scene now.
    It is also the stronger check, because it cannot be satisfied by a file the
    Agent wrote by hand.
    """
    started = _utc_now()
    expectations = check.config.get("objects")
    if not isinstance(expectations, list):
        return _error_result(check, started, "blender_live_scene requires config.objects list")
    absent = check.config.get("absent") or []
    if not isinstance(absent, list):
        return _error_result(check, started, "config.absent must be a list of names")
    host = str(check.config.get("host", "127.0.0.1"))
    port = int(check.config.get("port", 9876))
    try:
        passed, findings = _verify_blender_live(
            host,
            port,
            expectations,
            absent=[str(name) for name in absent],
            timeout_seconds=check.timeout_seconds or 60,
        )
    except (OSError, ValueError) as exc:
        return _error_result(check, started, f"Blender verification failed: {exc}")
    # Written as an artifact so a later quality check can read the same evidence
    # the outcome check used. Judging a findings dict directly is not possible:
    # an artifact reference resolves to a path.
    artifact = _write_check_artifact(context, check, findings, "blender-scene.json")
    return CheckResult(
        check_id=check.id,
        phase=check.phase,
        evaluator=check.evaluator,
        status="passed" if passed else "failed",
        passed=passed,
        score=1.0 if passed else 0.0,
        details={**findings, "selected_artifact": artifact},
        evidence_refs=(artifact,),
        error=None if passed else "Blender scene did not satisfy expectations",
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
        agent_profile=context.evaluator_agent_profile,
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
    if locator_status == "review":
        # The Locator's own prompt tells it to answer `review` when the artifact
        # is ambiguous, and that answer is a statement about the *measurement*,
        # not about the Agent. Routing it through `_error_result` made it inherit
        # `on_error`, and with the `on_error: fail` that both shipped Tasks use it
        # became a determinate failure -- an Agent credited with scoring zero for
        # a question nobody managed to answer. Report it as undetermined.
        return CheckResult(
            check_id=check.id,
            phase=check.phase,
            evaluator=check.evaluator,
            status="review",
            passed=None,
            score=None,
            details={"locator": payload, "locator_agent": result.to_dict()},
            evidence_refs=(str(result.last_message_path),),
            error=(
                "locator could not identify an unambiguous artifact: "
                f"{payload.get('reason') or 'no reason given'}"
            ),
            started_at=started,
            finished_at=_utc_now(),
        )
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
    if rubric is None:
        run = context.manifest.get("run", {})
        bundle = run.get("task_bundle", {}) if isinstance(run, dict) else {}
        rubric = bundle.get("rubric") if isinstance(bundle, dict) else None
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
        agent_profile=context.evaluator_agent_profile,
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
    if result.status != "completed":
        return _error_result(
            check,
            started,
            result.failure_message or f"quality Judge exited with {result.exit_code}",
        )
    payload = _load_agent_json(result.output_path, result.last_message_path)
    score = payload.get("score") if isinstance(payload, dict) else None
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return _error_result(check, started, "quality Judge did not return numeric score")
    score_float = float(score)
    if not 0.0 <= score_float <= 1.0:
        return _error_result(check, started, "quality Judge score must be between 0 and 1")
    judge_status = payload.get("status")
    if judge_status not in {"pass", "fail", "review"}:
        # The prompt requires an explicit verdict. A missing or unrecognized one
        # means the Judge did not answer the question, so fail closed into review
        # rather than letting an absent field read as a pass.
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
    normalized_path = context.trace_dir / "normalized-events.jsonl"
    if not trace_path.is_file() and not normalized_path.is_file():
        normalized_path = context.run_dir / "normalized-events.jsonl"
    if not trace_path.is_file() and not normalized_path.is_file():
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
    digest = digest_run(context.run_dir) if trace_path.is_file() else {}
    stats = _json_safe(digest.get("stats", {}))
    if normalized_path.is_file():
        from ..adapters.events import read_normalized_events

        normalized = read_normalized_events(normalized_path)
        stats = _merge_normalized_stats(stats, normalized)
    struggle = _json_safe(digest.get("struggle", {}))
    mcp_total = stats.get("mcp_calls_total") or 0
    shell_total = stats.get("shell_commands_total") or 0
    tool_total = stats.get("tool_calls_total") or 0
    total = mcp_total + shell_total + tool_total
    failed = (stats.get("mcp_calls_failed") or 0) + (
        stats.get("shell_commands_failed") or 0
    ) + (stats.get("tool_calls_failed") or 0)
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
        evidence_refs=(str(normalized_path if normalized_path.is_file() else trace_path),),
        started_at=started,
        finished_at=_utc_now(),
    )
