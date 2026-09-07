"""Stable result contracts for declarative Check execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

CheckStatus = Literal[
    "passed",
    "failed",
    "review",
    "blocked",
    "error",
    "skipped",
    "observed",
]
EvaluationDecision = Literal["pass", "fail", "review", "not_evaluable"]


@dataclass(frozen=True, slots=True)
class CheckResult:
    """The durable result of one CheckSpec execution."""

    check_id: str
    phase: str
    evaluator: str
    status: CheckStatus
    passed: bool | None = None
    score: float | None = None
    details: dict[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    error: str | None = None
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe result mapping."""
        value = asdict(self)
        value["evidence_refs"] = list(self.evidence_refs)
        return value


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Aggregated result for one Task run."""

    run_id: str
    task_id: str
    decision: EvaluationDecision
    outcome_score: float | None
    quality_score: float | None
    process_score: float | None
    checks: tuple[CheckResult, ...] = ()
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return the persisted report representation."""
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "decision": self.decision,
            "outcome_score": self.outcome_score,
            "quality_score": self.quality_score,
            "process_score": self.process_score,
            "checks": [check.to_dict() for check in self.checks],
            "errors": list(self.errors),
        }
