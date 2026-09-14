"""The exit-code contract for a run that cannot conclude anything.

`compare --runs N` and `experiment run` both promise exit 2 when the attempts ran
but the sample cannot separate the cells. A pipeline reads that code to decide
whether the result is actionable, so it is part of the interface, not a detail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_native_evals import cli


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    (root / "experiments").mkdir(parents=True)
    (root / "config").mkdir(parents=True)
    (root / "config" / "eval.yaml").write_text("version: 1\n", encoding="utf-8")
    (root / "experiments" / "sweep.yaml").write_text(
        "id: sweep\ntask_id: demo\nvary:\n  agent: [codex, dsh]\nrepeats: 5\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "_repo_root", lambda: root)
    return root


def _stub_run(monkeypatch: pytest.MonkeyPatch, *, separated: bool | None, repeats: int) -> None:
    """Replace the runner with a canned payload."""

    def fake_run_experiment(repo_root, spec, **_kwargs):  # type: ignore[no-untyped-def]
        return {
            "experiment_run_id": "e-1",
            "experiment_id": spec.experiment_id,
            "created_at": "2026-09-14T00:00:00+00:00",
            "config_path": None,
            "definition": spec.to_dict(),
            "invariant": {"task_id": spec.task_id},
            "cells": [
                {"label": "agent=codex", "measured": repeats, "unmeasured": 0},
                {"label": "agent=dsh", "measured": repeats, "unmeasured": 0},
            ],
            "attempts": [],
            "runs": [],
            "discrimination": {"separated": separated, "reason": "r", "overlapping": []},
            "output_dir": str(repo_root / "runs"),
        }

    monkeypatch.setattr("ai_native_evals.experiments.runner.run_experiment", fake_run_experiment)
    monkeypatch.setattr(
        "ai_native_evals.experiments.runner.render_experiment_markdown", lambda _p: "# ok\n"
    )


def test_indistinguishable_sample_exits_2(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """"Ran fine, proves nothing" must not look like success to CI."""
    _stub_run(monkeypatch, separated=False, repeats=5)

    code = cli.main(["experiment", "run", "sweep"])

    assert code == 2
    capsys.readouterr()


def test_separated_sample_exits_0(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub_run(monkeypatch, separated=True, repeats=5)

    assert cli.main(["experiment", "run", "sweep"]) == 0
    capsys.readouterr()


def test_single_repeat_does_not_claim_indistinguishable(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With one attempt per cell there is no interval, so there is no verdict.

    Exit 2 here would report a statistical conclusion the design never made.
    """
    _stub_run(monkeypatch, separated=None, repeats=1)

    assert cli.main(["experiment", "run", "sweep", "--runs", "1"]) == 0
    capsys.readouterr()


def test_compare_exits_2_on_overlapping_intervals(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub_run(monkeypatch, separated=False, repeats=5)
    monkeypatch.setattr(
        "ai_native_evals.runs.compare.render_comparison_markdown", lambda _p: "# ok\n"
    )

    def fake_compare(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return {
            "comparison_id": "c-1",
            "agents": [{"agent": "codex", "unmeasured": 0}],
            "runs": [{"status": "completed", "evaluation": {"decision": "pass"}}],
            "discrimination": {"separated": False},
        }

    monkeypatch.setattr("ai_native_evals.runs.compare.compare_task", fake_compare)

    assert cli.main(["compare", "demo", "--agents", "codex,dsh", "--runs", "5"]) == 2
    capsys.readouterr()
