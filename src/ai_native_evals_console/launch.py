"""Plan and execute user-selected evaluation runs from the local Console."""

from __future__ import annotations

import json
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from ai_native_evals.evaluation.runner import evaluate_run
from ai_native_evals.runs.docker_runtime import (
    DockerRuntimeError,
    start_docker_run,
    wait_docker_run,
)
from ai_native_evals.runs.lifecycle import prepare_run, update_manifest
from ai_native_evals.runs.resolver import resolve_run
from ai_native_evals.runs.spec import RunSpec

from .read_models import _safe_value


@dataclass(frozen=True, slots=True)
class LaunchRequest:
    """Selectors accepted by the Console; profile contents stay server-owned."""

    task_id: str
    agent: str | None = None
    model_profile: str | None = None
    model_provider: str | None = None
    model: str | None = None
    provider: str | None = None
    reasoning_effort: str | None = None
    mcp_profile: str | None = None
    sandbox_profile: str | None = None
    preset: str | None = None
    game_engine_ref: str | None = None
    dsh_ref: str | None = None
    no_evaluate: bool = False


@dataclass(slots=True)
class _PlanRecord:
    plan_id: str
    request: LaunchRequest
    spec: RunSpec
    created_at: str


@dataclass(slots=True)
class _JobRecord:
    job_id: str
    plan_id: str
    run_id: str
    status: str = "queued"
    run_dir: Path | None = None
    decision: str | None = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    started_at: str | None = None
    finished_at: str | None = None
    future: Future[Any] | None = None


# Previewed plans and finished jobs are process-local bookkeeping; the durable
# record is the Run directory in EvalRuns. Bound the in-memory history so a
# long-lived Console does not grow without limit.
_MAX_PLANS = 50
_MAX_JOBS = 100
_TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "error"})


class LaunchError(RuntimeError):
    """Raised when a Console plan or job cannot be created."""


class LaunchManager:
    """In-process local job manager; the actual evaluation core remains reusable by CLI."""

    def __init__(self, repo_root: Path, *, config_path: Path | None = None) -> None:
        self.repo_root = repo_root.resolve()
        self.config_path = config_path.resolve() if config_path else None
        self._plans: dict[str, _PlanRecord] = {}
        self._jobs: dict[str, _JobRecord] = {}
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="eval-console")

    def create_plan(self, request: LaunchRequest) -> dict[str, Any]:
        if not request.task_id.strip():
            raise LaunchError("task_id is required")
        try:
            spec = resolve_run(
                self.repo_root,
                request.task_id,
                config_path=self.config_path,
                agent=request.agent or None,
                model_profile=request.model_profile or None,
                model_provider=request.model_provider or None,
                model=request.model or None,
                provider=request.provider or None,
                reasoning_effort=request.reasoning_effort or None,
                mcp_profile=request.mcp_profile or None,
                sandbox_profile=request.sandbox_profile or None,
                preset=request.preset or None,
                game_engine_ref=request.game_engine_ref or None,
                dsh_ref=request.dsh_ref or None,
                # The Console's only write path ends in real Docker containers,
                # so the preview is exactly where a missing image must surface.
                check_images=True,
            )
        except (OSError, ValueError) as exc:
            raise LaunchError(str(exc)) from exc
        plan_id = f"plan-{uuid4().hex[:12]}"
        record = _PlanRecord(
            plan_id=plan_id,
            request=request,
            spec=spec,
            created_at=datetime.now(UTC).isoformat(),
        )
        with self._lock:
            self._plans[plan_id] = record
            self._prune_locked()
        return self._plan_view(record)

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._plans.get(plan_id)
        return self._plan_view(record) if record else None

    def execute(self, plan_id: str) -> dict[str, Any]:
        with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None:
                raise LaunchError("run plan not found or expired")
            for job in self._jobs.values():
                if job.plan_id == plan_id and job.status not in {"completed", "failed", "error"}:
                    return self._job_view(job)
            job = _JobRecord(
                job_id=f"job-{uuid4().hex[:12]}", plan_id=plan_id, run_id=plan.spec.run_id
            )
            self._jobs[job.job_id] = job
            job.future = self._executor.submit(self._execute, job.job_id, plan)
        return self._job_view(job)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
        return self._job_view(job) if job else None

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)

    def _execute(self, job_id: str, plan: _PlanRecord) -> None:
        run_dir: Path | None = None
        self._set_job(job_id, status="preparing", started_at=datetime.now(UTC).isoformat())
        try:
            run_dir = prepare_run(
                self.repo_root,
                plan.spec,
                game_engine_ref=plan.request.game_engine_ref or None,
                dsh_ref=plan.request.dsh_ref or None,
            )
            self._set_job(job_id, status="prepared", run_dir=run_dir)
            start_docker_run(run_dir, self.repo_root)
            self._set_job(job_id, status="running")
            result = wait_docker_run(run_dir)
            if not plan.request.no_evaluate and result.get("status") in {"completed", "failed"}:
                self._set_job(job_id, status="evaluating")
                update_manifest(run_dir, status="evaluating")
                evaluation = self._evaluate(run_dir, self.repo_root)
                if evaluation is not None:
                    self._persist_evaluation(run_dir, evaluation)
                    update_manifest(
                        run_dir,
                        status=str(result.get("status") or "failed"),
                        evaluation=evaluation,
                    )
                decision = evaluation.get("decision") if evaluation else None
            else:
                decision = None
            final_status = "completed" if result.get("status") == "completed" else "failed"
            self._set_job(
                job_id,
                status=final_status,
                run_dir=run_dir,
                decision=decision,
                finished_at=datetime.now(UTC).isoformat(),
            )
        except (DockerRuntimeError, LaunchError, OSError, ValueError) as exc:
            if run_dir is not None and (run_dir / "run-manifest.json").is_file():
                try:
                    update_manifest(run_dir, status="failed")
                except (OSError, ValueError, json.JSONDecodeError):
                    pass
            self._set_job(
                job_id,
                status="error",
                run_dir=run_dir,
                error=f"{type(exc).__name__}: {exc}",
                finished_at=datetime.now(UTC).isoformat(),
            )

    @staticmethod
    def _evaluate(run_dir: Path, repo_root: Path) -> dict[str, Any] | None:
        try:
            return evaluate_run(run_dir, repo_root=repo_root)
        except Exception as exc:  # Evaluator failure must be visible as a report, not a lost job.
            manifest_path = run_dir / "run-manifest.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                run = manifest.get("run") if isinstance(manifest, Mapping) else {}
                return {
                    "run_id": run.get("run_id", run_dir.name),
                    "task_id": run.get("task_id", ""),
                    "decision": "not_evaluable",
                    "outcome_score": None,
                    "quality_score": None,
                    "process_score": None,
                    "checks": [],
                    "errors": [f"{type(exc).__name__}: {exc}"],
                }
            except (OSError, ValueError, json.JSONDecodeError):
                return None

    def _set_job(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            for key, value in changes.items():
                setattr(job, key, value)
            if job.status in _TERMINAL_JOB_STATUSES:
                self._prune_locked()

    def _prune_locked(self) -> None:
        """Drop the oldest finished jobs and idle plans. Caller holds the lock.

        Plans still referenced by a live job are never evicted, so pruning cannot
        break an in-flight run.
        """
        finished = [
            job_id
            for job_id, job in self._jobs.items()
            if job.status in _TERMINAL_JOB_STATUSES
        ]
        for job_id in finished[: max(0, len(finished) - _MAX_JOBS)]:
            self._jobs.pop(job_id, None)
        in_use = {
            job.plan_id
            for job in self._jobs.values()
            if job.status not in _TERMINAL_JOB_STATUSES
        }
        idle = [plan_id for plan_id in self._plans if plan_id not in in_use]
        for plan_id in idle[: max(0, len(idle) - _MAX_PLANS)]:
            self._plans.pop(plan_id, None)

    @staticmethod
    def _plan_view(record: _PlanRecord | None) -> dict[str, Any] | None:
        if record is None:
            return None
        spec = record.spec
        resolved = spec.to_dict()
        safe = _safe_value(resolved, root=spec.run_dir)
        checks = [
            _safe_value(check.to_dict(), root=spec.run_dir) for check in spec.test_plan.checks
        ]
        request = {
            "task_id": record.request.task_id,
            "agent": record.request.agent,
            "model_profile": record.request.model_profile,
            "model_provider": record.request.model_provider,
            "model": record.request.model,
            "provider": record.request.provider,
            "reasoning_effort": record.request.reasoning_effort,
            "mcp_profile": record.request.mcp_profile,
            "sandbox_profile": record.request.sandbox_profile,
            "preset": record.request.preset,
            "game_engine_ref": record.request.game_engine_ref,
            "dsh_ref": record.request.dsh_ref,
            "no_evaluate": record.request.no_evaluate,
        }
        return {
            "plan_id": record.plan_id,
            "run_id": spec.run_id,
            "created_at": record.created_at,
            "request": _safe_value(request, root=spec.run_dir),
            "resolved": {
                "task_id": spec.task_id,
                "agent": spec.agent,
                "model": spec.model,
                "provider": spec.provider,
                "model_profile": spec.model_profile,
                "model_provider": spec.model_provider,
                "protocol": spec.protocol,
                "reasoning_effort": spec.reasoning_effort,
                "mcp_profile": spec.mcp_profile,
                "sandbox_profile": spec.sandbox_profile,
                "snapshot_mode": spec.snapshot_mode,
                "evaluator_agent": spec.evaluator_agent,
                "task_prompt": str(safe.get("task_prompt") or "")[:4000],
                "agent_profile": safe.get("agent_profile"),
                "evaluator_agent_profile": safe.get("evaluator_agent_profile"),
                "sandbox": safe.get("sandbox"),
                "resource_specs": safe.get("resource_specs") or [],
            },
            "test_plan": safe.get("test_plan") or {},
            "checks": checks,
        }

    @staticmethod
    def _persist_evaluation(run_dir: Path, evaluation: Mapping[str, Any]) -> None:
        target = run_dir / "workspace" / "evidence" / "evaluation.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(dict(evaluation), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)

    @staticmethod
    def _job_view(job: _JobRecord | None) -> dict[str, Any] | None:
        if job is None:
            return None
        return {
            "job_id": job.job_id,
            "plan_id": job.plan_id,
            "run_id": job.run_id,
            "status": job.status,
            "decision": job.decision,
            "error": job.error,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
        }
