"""Tests for declarative per-task test plans."""

from __future__ import annotations

import pytest

from ai_native_evals.plans import TestPlan as EvalTestPlan
from ai_native_evals.plans import TestPlanError as EvalTestPlanError


def _plan() -> EvalTestPlan:
    return EvalTestPlan.from_mapping(
        {
            "version": 1,
            "checks": [
                {
                    "id": "locate",
                    "phase": "outcome",
                    "evaluator": "agent.artifact_locator.v1",
                },
                {
                    "id": "validate",
                    "phase": "outcome",
                    "evaluator": "script.validator.v1",
                    "depends_on": ["locate"],
                    "required": True,
                    "weight": 2,
                },
                {
                    "id": "quality",
                    "phase": "quality",
                    "evaluator": "agent.quality_judge.v1",
                    "depends_on": ["validate"],
                    "required": False,
                    "weight": 0.5,
                },
            ],
            "quality_threshold": 0.7,
            "uncertain_result": "review",
        }
    )


def test_plan_parses_checks_and_orders_dependencies() -> None:
    plan = _plan()
    assert [check.id for check in plan.checks] == ["locate", "validate", "quality"]
    assert [check.id for check in plan.ordered_checks()] == ["locate", "validate", "quality"]
    assert plan.hard_checks == ("locate", "validate")
    assert plan.quality_threshold == 0.7
    assert plan.to_dict()["checks"][1]["depends_on"] == ["locate"]


def test_plan_allows_declaration_order_to_differ_from_execution_order() -> None:
    plan = EvalTestPlan.from_mapping(
        {
            "checks": [
                {
                    "id": "second",
                    "phase": "outcome",
                    "evaluator": "script.second.v1",
                    "depends_on": ["first"],
                },
                {"id": "first", "phase": "outcome", "evaluator": "script.first.v1"},
            ]
        }
    )
    assert [check.id for check in plan.ordered_checks()] == ["first", "second"]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"checks": [{"id": "x", "phase": "unknown", "evaluator": "e"}]},
            "phase",
        ),
        (
            {
                "checks": [
                    {"id": "x", "phase": "outcome", "evaluator": "e"},
                    {"id": "x", "phase": "outcome", "evaluator": "e"},
                ]
            },
            "duplicate check id",
        ),
        (
            {
                "checks": [
                    {
                        "id": "x",
                        "phase": "outcome",
                        "evaluator": "e",
                        "depends_on": ["missing"],
                    }
                ]
            },
            "unknown dependencies",
        ),
        (
            {
                "checks": [
                    {"id": "a", "phase": "outcome", "evaluator": "e", "depends_on": ["b"]},
                    {"id": "b", "phase": "outcome", "evaluator": "e", "depends_on": ["a"]},
                ]
            },
            "cycle",
        ),
    ],
)
def test_plan_rejects_invalid_definitions(payload: dict, message: str) -> None:
    with pytest.raises(EvalTestPlanError, match=message):
        EvalTestPlan.from_mapping(payload)
