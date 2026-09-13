"""Background environment checks for the Console.

A preflight probes Docker, the network, and host services, so it can take tens of
seconds. Running it inside the request would block the browser and hide progress,
so it follows the same shape as a launch job: start it, then poll.

Results are cached per ``(selector, force)`` request so opening the page twice
does not re-probe a machine that has not changed. The cache is deliberately not
time-based: an operator who fixed a problem wants ``force`` to be meaningful, and
one who did not wants the last answer rather than a fresh wait.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from ai_native_evals.preflight import run_preflight
from ai_native_evals.runs.resolver import EvalConfigError, resolve_run

#: Finished checks kept so the page can be reloaded without re-probing.
_MAX_CHECKS = 20


class PreflightError(RuntimeError):
    """Raised when a preflight cannot be started or is unknown."""


@dataclass(slots=True)
class _PreflightRecord:
    check_id: str
    status: str
    selector: dict[str, Any]
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    report: dict[str, Any] | None = None
    error: str | None = None
    future: Future[Any] | None = None


@dataclass(slots=True)
class _AgentCheckRecord:
    check_id: str
    agent: str
    level: str
    status: str
    created_at: str
    version: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    report: dict[str, Any] | None = None
    error: str | None = None
    future: Future[Any] | None = None


class PreflightManager:
    """Runs environment checks off the request thread, one machine at a time."""

    def __init__(self, repo_root: Path, *, config_path: Path | None = None) -> None:
        self.repo_root = repo_root.resolve()
        self.config_path = config_path.resolve() if config_path else None
        self._records: dict[str, _PreflightRecord] = {}
        self._agent_records: dict[str, _AgentCheckRecord] = {}
        self._lock = Lock()
        # One worker: two concurrent Docker probes would fight over container
        # names and give confusing results.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="preflight")

    def start(self, selector: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        """Start a check, or return the last one when the selector is unchanged.

        Re-probing on every page visit would make the page slow for no new
        information; ``force`` is how an operator asks again after a fix.
        """
        key = _selector_key(selector)
        with self._lock:
            if not force:
                existing = self._find_succeeded(key)
                if existing is not None:
                    return self._view(existing)
            record = _PreflightRecord(
                check_id=f"check-{uuid4().hex[:12]}",
                status="running",
                selector=selector,
                created_at=datetime.now(UTC).isoformat(),
                started_at=datetime.now(UTC).isoformat(),
            )
            self._records[record.check_id] = record
            self._prune_locked()
            record.future = self._executor.submit(self._run, record.check_id, selector)
        return self._view(record)

    def get(self, check_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._records.get(check_id)
        return self._view(record) if record else None

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            records = sorted(self._records.values(), key=lambda r: r.created_at)
        return self._view(records[-1]) if records else None

    def start_agent(
        self, agent: str, *, level: str = "static", version: str = ""
    ) -> dict[str, Any]:
        """Verify one Agent, statically or with a real container probe.

        `version` checks a specific build of the Agent instead of the profile's
        declared default, so one Agent can be verified at each version.

        Always a fresh run: the whole point is to answer "does this work right
        now", and a cached verdict from before a rebuild would be worse than no
        verdict at all.
        """
        record = _AgentCheckRecord(
            check_id=f"agent-{uuid4().hex[:12]}",
            agent=agent,
            level=level,
            version=version,
            status="running",
            created_at=datetime.now(UTC).isoformat(),
            started_at=datetime.now(UTC).isoformat(),
        )
        with self._lock:
            self._agent_records[record.check_id] = record
            self._prune_agents_locked()
            record.future = self._executor.submit(
                self._run_agent, record.check_id, agent, level, version
            )
        return self._agent_view(record)

    def get_agent(self, check_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._agent_records.get(check_id)
        return self._agent_view(record) if record else None

    def agent_results(self) -> dict[str, Any]:
        """The most recent verdict per Agent and version.

        Keyed by both, because one Agent now has several builds and each keeps
        its own verdict; keying by Agent alone would let the newer result
        silently replace the older one.
        """
        with self._lock:
            latest: dict[str, _AgentCheckRecord] = {}
            for record in sorted(self._agent_records.values(), key=lambda r: r.created_at):
                key = f"{record.agent}@{record.version}" if record.version else record.agent
                latest[key] = record
        return {key: self._agent_view(record) for key, record in latest.items()}

    def _run_agent(self, check_id: str, agent: str, level: str, version: str = "") -> None:
        try:
            report = self._verify_agent(agent, level, version)
        except Exception as exc:  # noqa: BLE001 - a probe crash is a finding, not a 500
            self._finish_agent(check_id, status="error", error=f"{type(exc).__name__}: {exc}")
            return
        self._finish_agent(check_id, status="completed", report=report.to_dict())

    def _verify_agent(self, agent: str, level: str, version: str = "") -> Any:
        from ai_native_evals.runs.agent_check import check_static, smoke_test_agent

        if level != "smoke":
            return check_static(self.repo_root, agent, version=version)

        # A smoke run needs a real model and real credentials; both come from the
        # selection a run would make, resolved here so the probe matches it.
        model, env_file = self._smoke_targets()
        if not model or env_file is None:
            report = check_static(self.repo_root, agent, version=version)
            report.add(
                _smoke_unavailable(
                    "没有可用的模型或凭据，无法进行真实启动测试"
                    if not model
                    else f"缺少凭据文件：{env_file}"
                )
            )
            return report
        return smoke_test_agent(
            self.repo_root, agent, model=model, provider_env_file=env_file, version=version
        )

    def _smoke_targets(self) -> tuple[str, Path | None]:
        """The default provider's model and env file, as a real run would use."""
        from ai_native_evals.providers import (
            load_provider_profiles,
            provider_env_file,
        )
        from ai_native_evals.runs.resolver import load_config

        config_path = self.config_path or self.repo_root / "config" / "eval.yaml"
        config = load_config(config_path)
        defaults = config.get("defaults") or {}
        wanted = str(defaults.get("provider") or "")
        try:
            profiles = load_provider_profiles(self.repo_root, config)
        except Exception:  # noqa: BLE001 - reported by the caller as unavailable
            return "", None
        profile = profiles.get(wanted)
        if profile is None:
            # Fall back to whichever provider is configured, so the check still
            # works on a machine that renamed its default.
            for candidate in profiles.values():
                env_path = provider_env_file(self.repo_root, candidate)
                if env_path and env_path.is_file():
                    profile = candidate
                    break
        if profile is None:
            return "", None
        env_path = provider_env_file(self.repo_root, profile)
        model = str(defaults.get("model") or "")
        if not model:
            declared = profile.get("models")
            if isinstance(declared, list) and declared:
                first = declared[0]
                model = str(first.get("id") if isinstance(first, dict) else first)
        return model, env_path

    def _finish_agent(
        self,
        check_id: str,
        *,
        status: str,
        report: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            record = self._agent_records.get(check_id)
            if record is None:
                return
            record.status = status
            record.report = report
            record.error = error
            record.finished_at = datetime.now(UTC).isoformat()

    def _prune_agents_locked(self) -> None:
        if len(self._agent_records) <= _MAX_CHECKS:
            return
        finished = sorted(
            (r for r in self._agent_records.values() if r.status != "running"),
            key=lambda r: r.created_at,
        )
        for record in finished[: len(self._agent_records) - _MAX_CHECKS]:
            self._agent_records.pop(record.check_id, None)

    @staticmethod
    def _agent_view(record: _AgentCheckRecord) -> dict[str, Any]:
        return {
            "check_id": record.check_id,
            "agent": record.agent,
            "level": record.level,
            "status": record.status,
            "created_at": record.created_at,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "report": record.report,
            "error": record.error,
        }

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)

    def _run(self, check_id: str, selector: dict[str, Any]) -> None:
        spec = None
        resolution_error: str | None = None
        task_id = selector.get("task_id")
        if task_id:
            try:
                spec = resolve_run(
                    self.repo_root,
                    str(task_id),
                    config_path=self.config_path,
                    agent=_optional(selector.get("agent")),
                    provider=_optional(selector.get("provider")),
                    model=_optional(selector.get("model")),
                    reasoning_effort=_optional(selector.get("reasoning_effort")),
                    mcp_profile=_optional(selector.get("mcp_profile")),
                    sandbox_profile=_optional(selector.get("sandbox_profile")),
                    preset=_optional(selector.get("preset")),
                    # The images check reports its own finding with a hint;
                    # failing resolution would hide every other result behind it.
                    check_images=False,
                )
            except (EvalConfigError, OSError, ValueError) as exc:
                resolution_error = str(exc)

        try:
            report = run_preflight(
                self.repo_root, spec=spec, distro=_optional(selector.get("distro"))
            )
        except Exception as exc:  # noqa: BLE001 - a probe crash is a finding, not a 500
            self._finish(
                check_id,
                status="error",
                error=f"{type(exc).__name__}: {exc}",
            )
            return

        payload = report.to_dict()
        if resolution_error is not None:
            # Reported as a warning rather than a blocker: the environment was
            # still checked, and that is what the operator came for.
            payload["checks"]["run_spec"] = {
                "status": "missing",
                "ok": False,
                "detail": resolution_error,
                "hint": "choose a preset, or a provider and model, then run the check again",
            }
            payload.setdefault("warnings", []).append("run_spec")
        self._finish(check_id, status="completed", report=payload)

    def _finish(
        self,
        check_id: str,
        *,
        status: str,
        report: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            record = self._records.get(check_id)
            if record is None:
                return
            record.status = status
            record.report = report
            record.error = error
            record.finished_at = datetime.now(UTC).isoformat()

    def _find_succeeded(self, key: str) -> _PreflightRecord | None:
        for record in sorted(self._records.values(), key=lambda r: r.created_at, reverse=True):
            if record.status == "completed" and _selector_key(record.selector) == key:
                return record
        return None

    def _prune_locked(self) -> None:
        """Bound the history; the durable record of a run is its directory."""
        if len(self._records) <= _MAX_CHECKS:
            return
        finished = sorted(
            (r for r in self._records.values() if r.status != "running"),
            key=lambda r: r.created_at,
        )
        for record in finished[: len(self._records) - _MAX_CHECKS]:
            self._records.pop(record.check_id, None)

    @staticmethod
    def _view(record: _PreflightRecord) -> dict[str, Any]:
        return {
            "check_id": record.check_id,
            "status": record.status,
            "selector": record.selector,
            "created_at": record.created_at,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "report": record.report,
            "error": record.error,
        }


def _optional(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _smoke_unavailable(detail: str) -> Any:
    """A finding that explains why a smoke run could not be attempted.

    Reported as ``unknown`` rather than a failure: the Agent was not shown to be
    broken, the machine simply cannot run the probe.
    """
    from ai_native_evals.runs.agent_check import AgentCheck

    return AgentCheck(
        "smoke",
        "unknown",
        detail=detail,
        hint="在 config/eval.local.yaml 中配置 provider，并确保 config/.env.local 有凭据",
    )


def _selector_key(selector: dict[str, Any]) -> str:
    items = sorted((str(k), str(v)) for k, v in selector.items() if v not in (None, ""))
    return "|".join(f"{k}={v}" for k, v in items)
