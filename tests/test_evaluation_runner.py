"""Tests for declarative TestPlan execution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_native_evals.evaluation import builtin, runner
from ai_native_evals.evaluation.contracts import CheckResult
from ai_native_evals.runs.agent_sandbox import EvaluatorAgentResult


def _manifest(tmp_path: Path, plan: dict) -> tuple[Path, dict]:
    workspace = tmp_path / "workspace"
    for child in ("game-engine", "output", "scratch", "evidence", "trace", "agent-config"):
        (workspace / child).mkdir(parents=True)
    manifest = {
        "status": "completed",
        "run": {
            "run_id": "test-run",
            "task_id": "test-task",
            "test_plan": plan,
        },
        "paths": {
            "workspace": str(workspace),
            "project": str(workspace / "game-engine"),
            "output": str(workspace / "output"),
            "scratch": str(workspace / "scratch"),
            "evidence": str(workspace / "evidence"),
            "trace": str(workspace / "trace"),
            "agent_config": str(workspace / "agent-config"),
        },
    }
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return run_dir, manifest


def test_evaluate_run_executes_script_checks_and_writes_results(tmp_path: Path) -> None:
    run_dir, manifest = _manifest(
        tmp_path,
        {
            "checks": [
                {
                    "id": "exists",
                    "phase": "outcome",
                    "evaluator": "script.file_exists.v1",
                    "input": {"path": "/workspace/output/result.txt"},
                },
                {
                    "id": "contents",
                    "phase": "outcome",
                    "evaluator": "script.text_equals.v1",
                    "input": {"artifact": "exists.path"},
                    "config": {"expected_text": "ok"},
                    "depends_on": ["exists"],
                },
                {
                    "id": "process",
                    "phase": "process",
                    "evaluator": "trace.process_analyzer.v1",
                    "required": False,
                    "depends_on": ["contents"],
                },
            ]
        },
    )
    result_path = Path(manifest["paths"]["output"]) / "result.txt"
    result_path.write_text("ok", encoding="utf-8")
    trace_path = Path(manifest["paths"]["trace"]) / "agent-container.log"
    trace_path.write_text("", encoding="utf-8")

    report = runner.evaluate_run(run_dir)

    assert report["decision"] == "pass"
    assert report["outcome_score"] == 1.0
    assert [item["check_id"] for item in report["checks"]] == [
        "exists",
        "contents",
        "process",
    ]
    assert (Path(manifest["paths"]["evidence"]) / "evaluation.json").is_file()
    assert (Path(manifest["paths"]["evidence"]) / "checks" / "contents.json").is_file()


def test_evaluate_run_blocks_check_when_dependency_fails(tmp_path: Path) -> None:
    run_dir, _manifest_value = _manifest(
        tmp_path,
        {
            "checks": [
                {
                    "id": "missing",
                    "phase": "outcome",
                    "evaluator": "script.file_exists.v1",
                    "input": {"path": "/workspace/output/missing.txt"},
                },
                {
                    "id": "dependent",
                    "phase": "outcome",
                    "evaluator": "script.text_equals.v1",
                    "input": {"artifact": "missing.path"},
                    "config": {"expected_text": "ok"},
                    "depends_on": ["missing"],
                },
            ]
        },
    )

    report = runner.evaluate_run(run_dir)

    assert report["decision"] == "fail"
    assert report["checks"][0]["status"] == "failed"
    assert report["checks"][1]["status"] == "blocked"


def test_artifact_locator_maps_agent_path_and_script_uses_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir, manifest = _manifest(
        tmp_path,
        {
            "checks": [
                {
                    "id": "locate",
                    "phase": "outcome",
                    "evaluator": "agent.artifact_locator.v1",
                    "input": {"roots": ["/workspace/output"]},
                    "config": {"expected_name": "result.txt"},
                },
                {
                    "id": "content",
                    "phase": "outcome",
                    "evaluator": "script.text_equals.v1",
                    "input": {"artifact": "locate.selected_artifact"},
                    "config": {"expected_text": "located"},
                    "depends_on": ["locate"],
                },
            ]
        },
    )
    result_path = Path(manifest["paths"]["output"]) / "result.txt"
    result_path.write_text("located", encoding="utf-8")
    locator_trace = Path(manifest["paths"]["trace"]) / "fake-locator"
    locator_trace.mkdir()
    last_message = locator_trace / "last.txt"
    payload = {
        "status": "found",
        "selected_artifact": "/workspace/output/result.txt",
        "candidates": [{"path": "/workspace/output/result.txt", "reason": "test"}],
    }
    last_message.write_text(json.dumps(payload), encoding="utf-8")

    def fake_agent(*args, **kwargs):  # type: ignore[no-untyped-def]
        return EvaluatorAgentResult(
            run_id="test-run",
            role="outcome-locator-locate",
            status="completed",
            exit_code=0,
            started_at="",
            finished_at="",
            trace_dir=locator_trace,
            log_path=locator_trace / "agent-container.log",
            last_message_path=last_message,
            output_path=None,
        )

    # Patched where the evaluator looks it up, not where it used to live: the
    # built-in implementations moved out of `runner`, and a stub set on the old
    # module is silently ignored -- which makes the test pass against the real
    # container runner instead of the fake.
    monkeypatch.setattr(builtin, "run_evaluator_agent", fake_agent)
    report = runner.evaluate_run(run_dir)

    assert report["decision"] == "pass"
    assert report["checks"][0]["details"]["selected_host_path"] == str(result_path.resolve())
    assert report["checks"][1]["status"] == "passed"


def test_json_contract_checks_nested_fields(tmp_path: Path) -> None:
    run_dir, manifest = _manifest(
        tmp_path,
        {
            "checks": [
                {
                    "id": "contract",
                    "phase": "outcome",
                    "evaluator": "script.json_contract.v1",
                    "input": {"path": "/workspace/output/result.json"},
                    "config": {
                        "required_fields": ["task_id", "meta.owner", "items"],
                        "field_values": {"task_id": "demo", "meta.owner": "eval"},
                        "non_empty_fields": ["items"],
                        "field_types": {"task_id": "string", "items": "array"},
                        "array_min_lengths": {"items": 1},
                    },
                }
            ]
        },
    )
    result = Path(manifest["paths"]["output"]) / "result.json"
    result.write_text(
        json.dumps(
            {
                "task_id": "demo",
                "meta": {"owner": "eval"},
                "items": ["one"],
            }
        ),
        encoding="utf-8",
    )

    report = runner.evaluate_run(run_dir)

    assert report["decision"] == "pass"
    assert report["checks"][0]["details"]["payload"]["meta"]["owner"] == "eval"


def test_invalid_plan_fails_before_any_check(tmp_path: Path) -> None:
    run_dir, _manifest_value = _manifest(
        tmp_path,
        {
            "checks": [
                {
                    "id": "a",
                    "phase": "outcome",
                    "evaluator": "script.file_exists.v1",
                    "depends_on": ["missing"],
                }
            ]
        },
    )
    with pytest.raises(runner.EvaluationError):
        # The manifest plan is structurally invalid and should not be silently skipped.
        runner.evaluate_run(run_dir)


# --------------------------------------------------------------------------
# Aggregation policy: "we could not measure it" is not "the Agent failed".
# --------------------------------------------------------------------------


def _crashing_evaluator(context, check):  # type: ignore[no-untyped-def]
    raise RuntimeError("evaluator could not run")


def _passing_evaluator(context, check):  # type: ignore[no-untyped-def]
    return CheckResult(
        check_id=check.id,
        phase=check.phase,
        evaluator=check.evaluator,
        status="passed",
        passed=True,
        score=1.0,
    )


runner.register_evaluator("test.crashes.v1")(_crashing_evaluator)
runner.register_evaluator("test.passes.v1")(_passing_evaluator)


def test_measured_failure_is_still_a_definitive_fail(tmp_path: Path) -> None:
    """The policy fix must not soften failures we actually observed."""
    run_dir, _ = _manifest(
        tmp_path,
        {
            "checks": [
                {
                    "id": "missing",
                    "phase": "outcome",
                    "evaluator": "script.file_exists.v1",
                    "input": {"path": "/workspace/output/absent.txt"},
                }
            ]
        },
    )

    report = runner.evaluate_run(run_dir)

    assert report["checks"][0]["status"] == "failed"
    assert report["decision"] == "fail"
    assert report["outcome_score"] == 0.0


@pytest.mark.parametrize("on_error", ["fail", "review", "skip"])
def test_crashing_evaluator_never_scores_the_agent(tmp_path: Path, on_error: str) -> None:
    """Our own crash must not be recorded as the Agent failing.

    `on_error` describes a *check* that could not run. An evaluator that raises is
    a defect in this repository, so no policy can turn it into a verdict about the
    Agent -- including `on_error: fail`, which both shipped Tasks declare and
    which previously produced `outcome_score: 0.0` and a hard `fail` here.

    The status stays `error` under every policy: rewriting it to `skipped` or
    `review` would claim the TestPlan chose something it did not.
    """
    run_dir, _ = _manifest(
        tmp_path,
        {
            "checks": [
                {
                    "id": "must-work",
                    "phase": "outcome",
                    "evaluator": "test.crashes.v1",
                    "on_error": on_error,
                    # Left required: the point is that even a hard check cannot
                    # convert an internal fault into an Agent failure.
                    "required": True,
                }
            ]
        },
    )

    report = runner.evaluate_run(run_dir)

    assert report["checks"][0]["status"] == "error"
    assert report["checks"][0]["details"]["evaluator_fault"] is True
    assert report["decision"] == "review"
    assert report["outcome_score"] is None


@pytest.mark.parametrize(
    ("on_error", "expected_status", "expected_decision", "expected_outcome"),
    [
        ("fail", "error", "fail", 0.0),
        ("review", "review", "review", None),
        ("skip", "skipped", "review", None),
    ],
)
def test_check_that_cannot_configure_follows_on_error_policy(
    tmp_path: Path,
    on_error: str,
    expected_status: str,
    expected_decision: str,
    expected_outcome: float | None,
) -> None:
    """A check whose *config* the evaluator rejects still follows `on_error`.

    This is the case `on_error` is for: the evaluator ran and reported that this
    check is unusable, which is a fact about the TestPlan and may honestly be read
    as a failure by the Task author who wrote it.
    """
    run_dir, _ = _manifest(
        tmp_path,
        {
            "checks": [
                {
                    "id": "must-work",
                    "phase": "outcome",
                    "evaluator": "script.file_exists.v1",
                    # No path: a definite, reportable configuration error.
                    "on_error": on_error,
                }
            ]
        },
    )

    report = runner.evaluate_run(run_dir)

    assert report["checks"][0]["status"] == expected_status
    assert report["decision"] == expected_decision
    assert report["outcome_score"] == expected_outcome


def test_uncertain_result_policy_decides_an_undetermined_run(tmp_path: Path) -> None:
    """uncertain_result used to be parsed, persisted, and then ignored."""
    report = runner.evaluate_run(
        _manifest(
            tmp_path,
            {
                "uncertain_result": "fail",
                "checks": [
                    {"id": "ran", "phase": "outcome", "evaluator": "test.passes.v1"},
                    {
                        "id": "could-not-run",
                        "phase": "quality",
                        "evaluator": "test.crashes.v1",
                        "on_error": "review",
                        "required": False,
                    },
                ],
            },
        )[0]
    )

    assert report["decision"] == "fail"
    # The check that did produce a verdict still contributes its score.
    assert report["outcome_score"] == 1.0


def test_run_with_no_verdict_at_all_is_never_a_pass(tmp_path: Path) -> None:
    report = runner.evaluate_run(
        _manifest(
            tmp_path,
            {
                "checks": [
                    {
                        "id": "skipped",
                        "phase": "outcome",
                        "evaluator": "test.crashes.v1",
                        "on_error": "skip",
                    }
                ]
            },
        )[0]
    )

    assert report["decision"] == "review"
    assert report["outcome_score"] is None


def test_unregistered_evaluator_honours_on_error_policy(tmp_path: Path) -> None:
    """The "evaluator does not exist" path must not bypass the task's policy."""
    report = runner.evaluate_run(
        _manifest(
            tmp_path,
            {
                "checks": [
                    {
                        "id": "typo",
                        "phase": "outcome",
                        "evaluator": "script.does_not_exist.v9",
                        "on_error": "review",
                    }
                ]
            },
        )[0]
    )

    assert report["checks"][0]["status"] == "review"
    assert report["decision"] == "review"
    assert report["outcome_score"] is None


# --------------------------------------------------------------------------
# `required` and `pass` must mean the same thing in every phase.
# --------------------------------------------------------------------------


def test_required_quality_check_is_a_hard_check(tmp_path: Path) -> None:
    """`required: true` on a Judge must fail the Run, not just be recorded.

    It previously selected hard checks as `required and phase == "outcome"`, so a
    required Judge that answered `fail` was persisted as `failed` and the Run was
    still reported as `pass`.
    """
    from ai_native_evals.plans import TestPlan

    plan = TestPlan.from_mapping(
        {
            "checks": [
                {"id": "exists", "phase": "outcome", "evaluator": "script.file_exists.v1"},
                {
                    "id": "judge",
                    "phase": "quality",
                    "evaluator": "agent.quality_judge.v1",
                    "required": True,
                },
            ]
        }
    )

    assert set(plan.hard_checks) == {"exists", "judge"}


def test_failed_required_judge_fails_the_run_without_a_threshold(tmp_path: Path) -> None:
    """A Judge that answers `fail` decides the Run with no quality_threshold set."""
    run_dir, manifest = _manifest(
        tmp_path,
        {
            "checks": [
                {
                    "id": "exists",
                    "phase": "outcome",
                    "evaluator": "script.file_exists.v1",
                    "input": {"path": "/workspace/output/result.txt"},
                },
                {
                    "id": "judge",
                    "phase": "quality",
                    "evaluator": "agent.quality_judge.v1",
                    "required": True,
                    "input": {"artifact": "exists.path"},
                    "config": {
                        "prompt": "prompts/quality/text-artifact-v1.md",
                        "rubric": "config/rubrics/hello-world.yaml",
                    },
                    "depends_on": ["exists"],
                },
            ]
        },
    )
    Path(manifest["paths"]["output"]).joinpath("result.txt").write_text("ok", encoding="utf-8")

    trace = Path(manifest["paths"]["trace"]) / "judge"
    trace.mkdir(parents=True, exist_ok=True)
    last = trace / "last.txt"
    # A high score with a definite `fail`: the numeric average alone would pass,
    # which is exactly the hole this closes.
    last.write_text(
        json.dumps({"status": "fail", "score": 0.9, "rationale": "rubric row failed"}),
        encoding="utf-8",
    )
    _stub_evaluator_agent(builtin, trace, last)

    report = runner.evaluate_run(run_dir)

    judge = next(item for item in report["checks"] if item["check_id"] == "judge")
    assert judge["status"] == "failed"
    assert report["quality_score"] == 0.9
    assert report["decision"] == "fail"


def _stub_evaluator_agent(target, trace: Path, last: Path) -> None:  # type: ignore[no-untyped-def]
    """Point the evaluator Agent at a canned last message.

    ``target`` is the module the built-in evaluators resolve the name from --
    currently ``builtin``. It moved out of ``runner``, and a stub left on the old
    module is not an error: the real runner would then start a container and the
    assertions would be about something else entirely.
    """

    def fake(*args, **kwargs):  # type: ignore[no-untyped-def]
        return EvaluatorAgentResult(
            run_id="test-run",
            role="quality-judge-judge",
            status="completed",
            exit_code=0,
            started_at="",
            finished_at="",
            trace_dir=trace,
            log_path=trace / "agent-container.log",
            last_message_path=last,
            output_path=None,
        )

    target.run_evaluator_agent = fake  # type: ignore[assignment]


def test_ambiguous_locator_is_undetermined_not_a_failure(tmp_path: Path) -> None:
    """The Locator's own `review` answer must not become an Agent failure.

    Its prompt tells it to answer `review` when the artifact is ambiguous. That is
    a statement about the measurement, but it was routed through `_error_result`,
    inherited `on_error: fail`, and was recorded as a determinate failure with
    `outcome_score: 0.0` -- the Agent blamed for a question nobody answered.
    """
    run_dir, manifest = _manifest(
        tmp_path,
        {
            "checks": [
                {
                    "id": "locate",
                    "phase": "outcome",
                    "evaluator": "agent.artifact_locator.v1",
                    "input": {"roots": ["/workspace/output"]},
                    "config": {"expected_name": "hello.txt"},
                    "required": True,
                    "on_error": "fail",
                }
            ]
        },
    )
    Path(manifest["paths"]["output"]).joinpath("hello.txt").write_text("ok", encoding="utf-8")

    trace = Path(manifest["paths"]["trace"]) / "locator"
    trace.mkdir(parents=True, exist_ok=True)
    last = trace / "last.txt"
    last.write_text(
        json.dumps(
            {
                "status": "review",
                "selected_artifact": None,
                "candidates": [],
                "reason": "two candidates match",
            }
        ),
        encoding="utf-8",
    )
    _stub_evaluator_agent(builtin, trace, last)

    report = runner.evaluate_run(run_dir)

    assert report["checks"][0]["status"] == "review"
    assert report["checks"][0]["score"] is None
    assert report["decision"] == "review"
    assert report["outcome_score"] is None


def test_locator_not_found_is_still_a_failure(tmp_path: Path) -> None:
    """`not_found` is a real answer, and stays a determinate failure."""
    run_dir, manifest = _manifest(
        tmp_path,
        {
            "checks": [
                {
                    "id": "locate",
                    "phase": "outcome",
                    "evaluator": "agent.artifact_locator.v1",
                    "input": {"roots": ["/workspace/output"]},
                    "config": {"expected_name": "hello.txt"},
                    "on_error": "fail",
                }
            ]
        },
    )

    trace = Path(manifest["paths"]["trace"]) / "locator"
    trace.mkdir(parents=True, exist_ok=True)
    last = trace / "last.txt"
    last.write_text(
        json.dumps({"status": "not_found", "selected_artifact": None, "candidates": []}),
        encoding="utf-8",
    )
    _stub_evaluator_agent(builtin, trace, last)

    report = runner.evaluate_run(run_dir)

    assert report["checks"][0]["status"] == "error"
    assert report["decision"] == "fail"
    assert report["outcome_score"] == 0.0


def test_telemetry_only_plan_cannot_pass(tmp_path: Path) -> None:
    """A Plan with no outcome check cannot support a `pass`.

    Process telemetry describes how the work was done, never whether it was done.
    Such a Run used to report `decision: pass` with every score null.
    """
    report = runner.evaluate_run(
        _manifest(
            tmp_path,
            {
                "checks": [
                    {
                        "id": "process",
                        "phase": "process",
                        "evaluator": "trace.process_analyzer.v1",
                        "required": False,
                    }
                ]
            },
        )[0]
    )

    assert report["decision"] == "review"
    assert report["outcome_score"] is None
