"""Shared machinery for evaluator implementations.

The seam between executing a TestPlan and implementing one check. Everything here
is used by both halves, which is why it lives in neither `runner.py` (which runs
plans) nor `builtin.py` (which implements checks): putting it in either would
make the other depend on a module it has no business knowing about.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import entry_points
from pathlib import Path, PurePosixPath
from typing import Any

from ..plans import CheckSpec, TestPlan
from .contracts import CheckResult

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

    @property
    def evaluator_agent_profile(self) -> dict[str, Any]:
        """Return the run-scoped profile reused by locator and judge Agents."""
        run = self.manifest.get("run", {})
        if not isinstance(run, dict):
            return {}
        value = run.get("evaluator_agent_profile") or run.get("agent_profile")
        return dict(value) if isinstance(value, Mapping) else {}

    @property
    def task_bundle_dir(self) -> Path | None:
        """Return the Task bundle directory for local prompt/rubric references."""
        run = self.manifest.get("run", {})
        bundle = run.get("task_bundle", {}) if isinstance(run, dict) else {}
        source = bundle.get("source_path") if isinstance(bundle, dict) else None
        if not source:
            return None
        path = Path(str(source))
        return path.parent if path.is_file() else path


_REGISTRY: dict[str, Evaluator] = {}


_ENTRYPOINTS_LOADED = False


def register_evaluator(name: str) -> Callable[[Evaluator], Evaluator]:
    """Register a reusable evaluator implementation by stable id."""

    def decorator(function: Evaluator) -> Evaluator:
        if not name or name in _REGISTRY:
            raise ValueError(f"evaluator id is empty or already registered: {name!r}")
        _REGISTRY[name] = function
        return function

    return decorator


def available_evaluators() -> tuple[str, ...]:
    """Return built-in and externally installed evaluator ids."""
    _load_entrypoint_evaluators()
    return tuple(sorted(_REGISTRY))


def _load_entrypoint_evaluators() -> None:
    global _ENTRYPOINTS_LOADED
    if _ENTRYPOINTS_LOADED:
        return
    _ENTRYPOINTS_LOADED = True
    for plugin in entry_points(group="ai_native_evals.evaluators"):
        if plugin.name in _REGISTRY:
            continue
        loaded = plugin.load()
        if not callable(loaded):
            raise EvaluationError(f"evaluator plugin {plugin.name!r} is not callable")
        _REGISTRY[plugin.name] = loaded


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json_safe(value: Any) -> Any:
    """Convert mappings/tuples from telemetry into ordinary JSON values."""
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _error_result(check: CheckSpec, started: str, message: str) -> CheckResult:
    """Record an evaluator that could not run at all.

    This is the second producer of error results (the first is the exception
    boundary in ``_execute_check``); it must apply ``on_error`` the same way so
    the persisted status agrees with the verdict that gets aggregated from it.
    """
    status = {"review": "review", "skip": "skipped"}.get(check.on_error, "error")
    failed = status == "error"
    return CheckResult(
        check_id=check.id,
        phase=check.phase,
        evaluator=check.evaluator,
        status=status,  # type: ignore[arg-type]
        passed=False if failed else None,
        score=0.0 if failed and check.phase == "outcome" else None,
        error=message,
        started_at=started,
        finished_at=_utc_now(),
    )


def _resolve_path(context: EvaluationContext, raw_path: str) -> Path:
    """Resolve a container path under /workspace or a run-relative path safely."""
    if raw_path.startswith("/workspace/") or raw_path == "/workspace":
        relative = raw_path.removeprefix("/workspace/")
        return (context.workspace_dir / relative).resolve()
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate.resolve()
    return (context.run_dir / candidate).resolve()


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


def _config_candidates(context: EvaluationContext, value: str) -> tuple[Path, ...]:
    candidate = Path(value)
    if candidate.is_absolute():
        return (candidate,)
    candidates = [context.repo_root / candidate]
    if context.task_bundle_dir is not None:
        candidates.append(context.task_bundle_dir / candidate)
    return tuple(candidates)


def _load_text_config(context: EvaluationContext, value: Any) -> str | None:
    if isinstance(value, str):
        for path in _config_candidates(context, value):
            if path.is_file():
                return path.read_text(encoding="utf-8")
        return value
    return None


def _load_structured_config(context: EvaluationContext, value: Any) -> Any:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        for path in _config_candidates(context, value):
            if not path.is_file():
                continue
            try:
                import yaml

                return yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
    return None


def _write_check_result(evidence_dir: Path, result: CheckResult) -> None:
    checks_dir = evidence_dir / "checks"
    checks_dir.mkdir(parents=True, exist_ok=True)
    _write_json(checks_dir / f"{result.check_id}.json", result.to_dict())


def _write_check_artifact(
    context: EvaluationContext, check: CheckSpec, payload: Any, filename: str
) -> str:
    """Persist a check's raw evidence and return its container-visible path.

    A later check can only read an artifact by path, so evidence a Rubric needs
    to judge has to be written out rather than kept in the findings dict.
    """
    directory = context.evidence_dir / "artifacts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{check.id}-{filename}"
    _write_json(path, payload)
    try:
        relative = path.relative_to(context.workspace_dir.resolve())
    except ValueError:
        return str(path)
    return f"/workspace/{relative.as_posix()}"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
