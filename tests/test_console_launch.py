"""Tests for the Console launch manager's process-local bookkeeping.

Plans and jobs exist only in memory; the durable record is the Run directory.
These tests pin the bounds that keep a long-lived Console from growing forever,
and the rule that pruning never evicts an in-flight run.
"""

from __future__ import annotations

from pathlib import Path

from ai_native_evals_console import launch as launch_module
from ai_native_evals_console.launch import LaunchManager


class _FakeSpec:
    run_id = "run-x"
    run_dir = Path(".")

    def to_dict(self) -> dict:
        return {}

    class test_plan:
        checks: list = []


class _FakeRequest:
    task_id = "task"
    agent = None
    model_profile = None
    model_provider = None
    model = None
    mcp_profile = None
    sandbox_profile = None
    preset = None
    game_engine_ref = None
    dsh_ref = None
    no_evaluate = True


def _manager() -> LaunchManager:
    return LaunchManager(Path("."))


def _add_plan(manager: LaunchManager, plan_id: str) -> None:
    manager._plans[plan_id] = launch_module._PlanRecord(
        plan_id=plan_id, request=_FakeRequest(), spec=_FakeSpec(), created_at="now"
    )


def _add_job(manager: LaunchManager, job_id: str, status: str, plan_id: str = "p") -> None:
    job = launch_module._JobRecord(job_id=job_id, plan_id=plan_id, run_id="run-x")
    job.status = status
    manager._jobs[job_id] = job


def test_idle_plans_are_bounded() -> None:
    manager = _manager()
    try:
        with manager._lock:
            for index in range(launch_module._MAX_PLANS + 25):
                _add_plan(manager, f"plan-{index:03d}")
            manager._prune_locked()
            assert len(manager._plans) == launch_module._MAX_PLANS
    finally:
        manager.shutdown()


def test_finished_jobs_are_bounded() -> None:
    manager = _manager()
    try:
        with manager._lock:
            for index in range(launch_module._MAX_JOBS + 30):
                _add_job(manager, f"job-{index:03d}", "completed")
            manager._prune_locked()
            assert len(manager._jobs) == launch_module._MAX_JOBS
    finally:
        manager.shutdown()


def test_pruning_never_evicts_a_live_job_or_its_plan() -> None:
    manager = _manager()
    try:
        with manager._lock:
            _add_plan(manager, "live-plan")
            _add_job(manager, "live-job", "running", plan_id="live-plan")
            for index in range(launch_module._MAX_JOBS + launch_module._MAX_PLANS + 10):
                _add_job(manager, f"done-{index:03d}", "completed", plan_id="done-plan")
                _add_plan(manager, f"idle-{index:03d}")
            manager._prune_locked()

            assert "live-job" in manager._jobs
            assert "live-plan" in manager._plans
            assert len(manager._jobs) == launch_module._MAX_JOBS + 1
            assert len(manager._plans) <= launch_module._MAX_PLANS + 1
    finally:
        manager.shutdown()


def test_finishing_a_job_triggers_pruning() -> None:
    """_set_job is the hot path that actually observes jobs going terminal."""
    manager = _manager()
    try:
        with manager._lock:
            for index in range(launch_module._MAX_JOBS + 5):
                _add_job(manager, f"done-{index:03d}", "completed")
            _add_job(manager, "live", "running")

        manager._set_job("live", status="completed")

        with manager._lock:
            assert len(manager._jobs) <= launch_module._MAX_JOBS
            assert "live" in manager._jobs
    finally:
        manager.shutdown()
