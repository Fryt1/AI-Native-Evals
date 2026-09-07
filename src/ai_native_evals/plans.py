"""Declarative per-task test plans.

A Task owns a ``TestPlan``. The plan is data: it names checks and their
configuration, while evaluator implementations remain reusable framework
components. A check may be deterministic, agent-backed, or trace-based; the
plan does not care how the evaluator is implemented.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

CheckPhase = Literal["outcome", "quality", "process"]
CheckErrorPolicy = Literal["fail", "review", "skip"]
UncertainPolicy = Literal["fail", "review"]

_ALLOWED_PHASES = frozenset({"outcome", "quality", "process"})
_ALLOWED_ERROR_POLICIES = frozenset({"fail", "review", "skip"})
_ALLOWED_UNCERTAIN_POLICIES = frozenset({"fail", "review"})


class TestPlanError(ValueError):
    """Raised when a task test plan is malformed."""


@dataclass(frozen=True, slots=True)
class CheckSpec:
    """One declarative check in a task's test plan."""

    id: str
    phase: CheckPhase
    evaluator: str
    input: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    required: bool = True
    weight: float = 1.0
    depends_on: tuple[str, ...] = ()
    timeout_seconds: int | None = None
    on_error: CheckErrorPolicy = "fail"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], index: int) -> CheckSpec:
        """Validate and materialize one check mapping."""
        check_id = _required_string(value, "id", f"checks[{index}]")
        phase = _required_string(value, "phase", f"check {check_id!r}")
        if phase not in _ALLOWED_PHASES:
            raise TestPlanError(
                f"check {check_id!r} phase must be one of {sorted(_ALLOWED_PHASES)}"
            )
        evaluator = _required_string(value, "evaluator", f"check {check_id!r}")

        input_value = value.get("input", {})
        if not isinstance(input_value, dict):
            raise TestPlanError(f"check {check_id!r} input must be a mapping")
        config_value = value.get("config", {})
        if not isinstance(config_value, dict):
            raise TestPlanError(f"check {check_id!r} config must be a mapping")

        required = value.get("required", True)
        if not isinstance(required, bool):
            raise TestPlanError(f"check {check_id!r} required must be boolean")

        weight_value = value.get("weight", 1.0)
        if isinstance(weight_value, bool) or not isinstance(weight_value, (int, float)):
            raise TestPlanError(f"check {check_id!r} weight must be a number")
        weight = float(weight_value)
        if weight < 0.0:
            raise TestPlanError(f"check {check_id!r} weight must be non-negative")

        dependencies = value.get("depends_on", ())
        if not isinstance(dependencies, (list, tuple)) or not all(
            isinstance(item, str) and item for item in dependencies
        ):
            raise TestPlanError(
                f"check {check_id!r} depends_on must be a list of non-empty strings"
            )
        if len(set(dependencies)) != len(dependencies):
            raise TestPlanError(f"check {check_id!r} depends_on contains duplicates")

        timeout_value = value.get("timeout_seconds")
        timeout: int | None
        if timeout_value is None:
            timeout = None
        else:
            if isinstance(timeout_value, bool):
                raise TestPlanError(f"check {check_id!r} timeout_seconds must be an integer")
            try:
                timeout = int(timeout_value)
            except (TypeError, ValueError) as exc:
                raise TestPlanError(
                    f"check {check_id!r} timeout_seconds must be an integer"
                ) from exc
            if timeout <= 0:
                raise TestPlanError(f"check {check_id!r} timeout_seconds must be positive")

        on_error = value.get("on_error", "fail")
        if on_error not in _ALLOWED_ERROR_POLICIES:
            raise TestPlanError(
                f"check {check_id!r} on_error must be one of {sorted(_ALLOWED_ERROR_POLICIES)}"
            )

        return cls(
            id=check_id,
            phase=phase,  # type: ignore[arg-type]
            evaluator=evaluator,
            input=dict(input_value),
            config=dict(config_value),
            required=required,
            weight=weight,
            depends_on=tuple(dependencies),
            timeout_seconds=timeout,
            on_error=on_error,  # type: ignore[arg-type]
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe mapping."""
        value = asdict(self)
        value["depends_on"] = list(self.depends_on)
        return value


@dataclass(frozen=True, slots=True)
class TestPlan:
    """Validated collection of checks owned by one Task."""

    checks: tuple[CheckSpec, ...] = ()
    hard_checks: tuple[str, ...] = ()
    quality_threshold: float | None = None
    uncertain_result: UncertainPolicy = "review"
    version: str = "1"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> TestPlan:
        """Validate and materialize a YAML/JSON test plan."""
        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise TestPlanError("test_plan must be a mapping")

        raw_checks = value.get("checks", [])
        if not isinstance(raw_checks, list):
            raise TestPlanError("test_plan.checks must be a list")
        checks: list[CheckSpec] = []
        seen: set[str] = set()
        for index, raw_check in enumerate(raw_checks):
            if not isinstance(raw_check, Mapping):
                raise TestPlanError(f"test_plan.checks[{index}] must be a mapping")
            check = CheckSpec.from_mapping(raw_check, index)
            if check.id in seen:
                raise TestPlanError(f"duplicate check id: {check.id!r}")
            seen.add(check.id)
            checks.append(check)

        hard_value = value.get("hard_checks")
        if hard_value is None:
            hard_checks = tuple(
                check.id for check in checks if check.required and check.phase == "outcome"
            )
        else:
            if not isinstance(hard_value, (list, tuple)) or not all(
                isinstance(item, str) and item for item in hard_value
            ):
                raise TestPlanError("test_plan.hard_checks must be a list of strings")
            hard_checks = tuple(hard_value)
            if len(set(hard_checks)) != len(hard_checks):
                raise TestPlanError("test_plan.hard_checks contains duplicates")
            unknown = sorted(set(hard_checks) - seen)
            if unknown:
                raise TestPlanError(f"test_plan.hard_checks references unknown checks: {unknown}")

        threshold_value = value.get("quality_threshold")
        if threshold_value is None:
            quality_threshold = None
        else:
            if isinstance(threshold_value, bool) or not isinstance(
                threshold_value, (int, float)
            ):
                raise TestPlanError("test_plan.quality_threshold must be a number")
            quality_threshold = float(threshold_value)
            if not 0.0 <= quality_threshold <= 1.0:
                raise TestPlanError("test_plan.quality_threshold must be between 0 and 1")

        uncertain_result = value.get("uncertain_result", "review")
        if uncertain_result not in _ALLOWED_UNCERTAIN_POLICIES:
            raise TestPlanError(
                "test_plan.uncertain_result must be 'fail' or 'review'"
            )

        version = value.get("version", "1")
        if not isinstance(version, (str, int)) or not str(version):
            raise TestPlanError("test_plan.version must be a non-empty string or integer")

        plan = cls(
            checks=tuple(checks),
            hard_checks=hard_checks,
            quality_threshold=quality_threshold,
            uncertain_result=uncertain_result,  # type: ignore[arg-type]
            version=str(version),
        )
        plan._validate_dependencies()
        return plan

    def _validate_dependencies(self) -> None:
        """Validate references and cycles in the check dependency graph."""
        ids = {check.id for check in self.checks}
        for check in self.checks:
            unknown = sorted(set(check.depends_on) - ids)
            if unknown:
                raise TestPlanError(
                    f"check {check.id!r} references unknown dependencies: {unknown}"
                )
            if check.id in check.depends_on:
                raise TestPlanError(f"check {check.id!r} cannot depend on itself")
        self.ordered_checks()  # raises on cycles

    def ordered_checks(self) -> tuple[CheckSpec, ...]:
        """Return checks in stable dependency order."""
        by_id = {check.id: check for check in self.checks}
        remaining = {check.id: set(check.depends_on) for check in self.checks}
        ordered: list[CheckSpec] = []
        declaration_order = [check.id for check in self.checks]
        while remaining:
            ready = [
                check_id
                for check_id in declaration_order
                if check_id in remaining and not remaining[check_id]
            ]
            if not ready:
                cycle = sorted(remaining)
                raise TestPlanError(f"test_plan check dependencies contain a cycle: {cycle}")
            for check_id in ready:
                ordered.append(by_id[check_id])
                del remaining[check_id]
                for dependencies in remaining.values():
                    dependencies.discard(check_id)
        return tuple(ordered)

    def to_dict(self) -> dict[str, Any]:
        """Return the normalized plan for a run manifest."""
        return {
            "version": self.version,
            "checks": [check.to_dict() for check in self.checks],
            "hard_checks": list(self.hard_checks),
            "quality_threshold": self.quality_threshold,
            "uncertain_result": self.uncertain_result,
        }


def _required_string(value: Mapping[str, Any], key: str, context: str) -> str:
    child = value.get(key)
    if not isinstance(child, str) or not child.strip():
        raise TestPlanError(f"{context} requires a non-empty string {key!r}")
    return child.strip()
