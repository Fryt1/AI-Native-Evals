"""Initial smoke tests for the package wiring."""

from pathlib import Path

from ai_native_evals.contracts import Verdict
from ai_native_evals.sandboxes import WorkspaceSpec, prepare_workspace


def test_verdict_round_trip() -> None:
    payload = {
        "status": "succeeded",
        "score": 1,
        "passed": True,
        "evidence_complete": True,
        "stages_completed": ["stage.prepare", "stage.verify"],
        "human_interventions": 0,
        "retries": 1,
        "failure_class": None,
        "details": {"fixture": "smoke"},
    }

    verdict = Verdict.from_mapping(payload)

    assert verdict.to_dict() == payload


def test_prepare_workspace_creates_only_requested_directory(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "smoke-001"

    result = prepare_workspace(WorkspaceSpec(run_dir=run_dir))

    assert result == run_dir
    assert run_dir.is_dir()
    assert list(run_dir.iterdir()) == []


def test_verdict_rejects_non_boolean_evidence_flag() -> None:
    try:
        Verdict.from_mapping({"status": "succeeded", "evidence_complete": "yes"})
    except ValueError as exc:
        assert str(exc) == "evidence_complete must be a boolean"
    else:
        raise AssertionError("expected invalid evidence flag to be rejected")
