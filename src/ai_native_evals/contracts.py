"""Stable contracts for external Agent adapters and domain scorers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

AgentRunStatus = Literal["completed", "failed", "timed_out", "cancelled"]

_ALLOWED_STATUSES = frozenset(
    {
        "succeeded",
        "degraded",
        "failed",
        "blocked",
        "needs_approval",
        "unknown",
    }
)


@dataclass(frozen=True, slots=True)
class AgentLaunchSpec:
    """Inputs shared by external Agent adapters."""

    agent_id: str
    run_dir: Path
    task_text: str
    task_id: str = ""
    timeout_seconds: int = 1800
    model: str | None = None
    environment: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    """Machine-readable outcome of one external Agent process run."""

    agent_id: str
    task_id: str
    run_dir: Path
    status: AgentRunStatus
    exit_code: int | None
    started_at: str
    finished_at: str
    events_path: Path
    stderr_path: Path
    last_message_path: Path
    manifest_path: Path
    model: str | None = None
    failure_message: str | None = None

    @property
    def completed(self) -> bool:
        """Whether the external process exited successfully."""
        return self.status == "completed"

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON representation persisted in the run directory."""
        return {
            "agent_id": self.agent_id,
            "task_id": self.task_id,
            "run_dir": str(self.run_dir),
            "status": self.status,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "events_path": str(self.events_path),
            "stderr_path": str(self.stderr_path),
            "last_message_path": str(self.last_message_path),
            "manifest_path": str(self.manifest_path),
            "model": self.model,
            "failure_message": self.failure_message,
        }


class AgentAdapterNotImplemented(RuntimeError):
    """Raised by an Agent adapter seam that has not been wired yet."""


@dataclass(frozen=True, slots=True)
class Verdict:
    """Machine-readable result produced by a domain verifier.

    The evaluator treats ``passed`` as the primary binary outcome and keeps the
    remaining fields as diagnostic metadata. Domain-specific acceptance remains
    owned by ``AI-Native-Game-Engine``; this type only transports its result.
    """

    status: str
    score: float
    passed: bool
    evidence_complete: bool = False
    stages_completed: tuple[str, ...] = ()
    human_interventions: int = 0
    retries: int = 0
    failure_class: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> Verdict:
        """Validate and materialize a JSON-like verifier payload."""
        status = payload.get("status")
        if not isinstance(status, str) or status not in _ALLOWED_STATUSES:
            raise ValueError(f"unsupported verdict status: {status!r}")

        score = payload.get("score", 1.0 if status == "succeeded" else 0.0)
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError("verdict score must be a number")
        score_float = float(score)
        if not 0.0 <= score_float <= 1.0:
            raise ValueError("verdict score must be between 0 and 1")

        passed = payload.get("passed", status in {"succeeded", "degraded"})
        if not isinstance(passed, bool):
            raise ValueError("verdict passed must be a boolean")

        stages = payload.get("stages_completed", ())
        if not isinstance(stages, (list, tuple)) or not all(
            isinstance(stage, str) for stage in stages
        ):
            raise ValueError("verdict stages_completed must be a list of strings")

        human_interventions = payload.get("human_interventions", 0)
        retries = payload.get("retries", 0)
        if (
            isinstance(human_interventions, bool)
            or not isinstance(human_interventions, int)
            or human_interventions < 0
        ):
            raise ValueError("human_interventions must be a non-negative integer")
        if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
            raise ValueError("retries must be a non-negative integer")

        evidence_complete = payload.get("evidence_complete", False)
        if not isinstance(evidence_complete, bool):
            raise ValueError("evidence_complete must be a boolean")

        failure_class = payload.get("failure_class")
        if failure_class is not None and not isinstance(failure_class, str):
            raise ValueError("failure_class must be a string or null")

        details = payload.get("details", {})
        if not isinstance(details, dict):
            raise ValueError("verdict details must be an object")

        return cls(
            status=status,
            score=score_float,
            passed=passed,
            evidence_complete=evidence_complete,
            stages_completed=tuple(stages),
            human_interventions=human_interventions,
            retries=retries,
            failure_class=failure_class,
            details=dict(details),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the stable JSON representation used in logs and reports."""
        return {
            "status": self.status,
            "score": self.score,
            "passed": self.passed,
            "evidence_complete": self.evidence_complete,
            "stages_completed": list(self.stages_completed),
            "human_interventions": self.human_interventions,
            "retries": self.retries,
            "failure_class": self.failure_class,
            "details": self.details,
        }
