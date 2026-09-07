"""Tests for declarative TestPlan execution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_native_evals.evaluation import runner
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

    monkeypatch.setattr(runner, "run_evaluator_agent", fake_agent)
    report = runner.evaluate_run(run_dir)

    assert report["decision"] == "pass"
    assert report["checks"][0]["details"]["selected_host_path"] == str(result_path.resolve())
    assert report["checks"][1]["status"] == "passed"


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
