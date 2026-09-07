"""Declarative evaluation plan execution."""

from .contracts import CheckResult, EvaluationReport
from .runner import evaluate_run

__all__ = ["CheckResult", "EvaluationReport", "evaluate_run"]
