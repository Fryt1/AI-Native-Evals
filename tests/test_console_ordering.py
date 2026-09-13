"""What the run list shows first, and what counts as a live run.

Two things went wrong here, and both were visible only by using the Console:

* the list grouped by status before sorting by time, so a week-old failure sat
  above the run the operator had just finished;
* "live" was spelled out in three separate SQL fragments that could disagree,
  and one of them counted `prepared`, so an abandoned run matched the "running"
  filter forever.

These tests pin the ordering and the single source of truth for liveliness.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_native_evals_console.catalog import _ACTIVE_SQL, _ACTIVE_STATUSES, Catalog

#: Distinct timestamps, so the ordering assertions read as intent, not dates.
T1 = "2026-01-01T00:00:00+00:00"
T2 = "2026-02-01T00:00:00+00:00"
T3 = "2026-03-01T00:00:00+00:00"


def _run(
    root: Path,
    run_id: str,
    *,
    status: str,
    decision: str | None = None,
    started_at: str = "2026-01-01T00:00:00+00:00",
    finished_at: str | None = None,
) -> None:
    run_dir = root / run_id
    (run_dir / "workspace").mkdir(parents=True)
    manifest = {
        "status": status,
        "run": {
            "run_id": run_id,
            "task_id": "task",
            "agent": "codex",
            "model": "m",
            "created_at": started_at,
            "agent_profile": {"id": "codex"},
        },
        "runtime": {"started_at": started_at, "completed_at": finished_at},
    }
    (run_dir / "run-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if decision:
        evidence = run_dir / "workspace" / "evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "evaluation.json").write_text(
            json.dumps({"run_id": run_id, "task_id": "task", "decision": decision}),
            encoding="utf-8",
        )


@pytest.fixture
def catalog(tmp_path: Path) -> Catalog:
    instance = Catalog(tmp_path)
    instance.reindex()
    return instance


def test_the_newest_finished_run_comes_first(tmp_path: Path) -> None:
    """The list exists to answer "what did I just run?"."""
    _run(tmp_path, "old-failure", status="failed", decision="fail", started_at=T1)
    _run(tmp_path, "fresh-pass", status="completed", decision="pass", started_at=T3)
    catalog = Catalog(tmp_path)
    catalog.reindex()

    rows, _total = catalog.list_runs(filters={"limit": 10})

    assert rows[0]["run_id"] == "fresh-pass"


def test_an_older_failure_does_not_outrank_a_newer_pass(tmp_path: Path) -> None:
    """A stale failure used to be pinned above everything else."""
    _run(tmp_path, "a-old-fail", status="failed", decision="fail", started_at=T1)
    _run(tmp_path, "b-new-pass", status="completed", decision="pass", started_at=T2)
    _run(tmp_path, "c-newer-pass", status="completed", decision="pass", started_at=T3)
    catalog = Catalog(tmp_path)
    catalog.reindex()

    rows, _total = catalog.list_runs(filters={"limit": 10})

    assert [r["run_id"] for r in rows] == ["c-newer-pass", "b-new-pass", "a-old-fail"]


def test_a_live_run_floats_to_the_top(tmp_path: Path) -> None:
    """In-flight runs are the only ones with something happening now."""
    _run(tmp_path, "finished", status="completed", decision="pass", started_at=T3)
    _run(tmp_path, "live", status="running", started_at=T1)
    catalog = Catalog(tmp_path)
    catalog.reindex()

    rows, _total = catalog.list_runs(filters={"limit": 10})

    assert rows[0]["run_id"] == "live"


def test_an_abandoned_prepared_run_is_not_live(tmp_path: Path) -> None:
    """`prepared` means nothing was started.

    A run prepared and then abandoned stays in that state forever, so counting
    it as live would park a stale entry at the top and under the "running"
    filter indefinitely.
    """
    _run(tmp_path, "abandoned", status="prepared", started_at=T3)
    catalog = Catalog(tmp_path)
    catalog.reindex()

    _rows, running = catalog.list_runs(filters={"bucket": "running"})

    assert running == 0


def test_ordering_and_filter_agree_about_what_is_live(tmp_path: Path) -> None:
    """The same set drives both, so they cannot disagree."""
    _run(tmp_path, "live", status="running", started_at=T1)
    _run(tmp_path, "abandoned", status="prepared", started_at=T3)
    catalog = Catalog(tmp_path)
    catalog.reindex()

    rows, _total = catalog.list_runs(filters={"limit": 10})
    live_rows, running_count = catalog.list_runs(filters={"bucket": "running"})

    assert rows[0]["run_id"] == "live"
    assert running_count == 1
    assert [r["run_id"] for r in live_rows] == ["live"]


def test_bucket_counts_match_the_filter(tmp_path: Path) -> None:
    _run(tmp_path, "live", status="running", started_at=T1)
    _run(tmp_path, "done", status="completed", decision="pass", started_at=T2)
    catalog = Catalog(tmp_path)
    catalog.reindex()

    _rows, running = catalog.list_runs(filters={"bucket": "running"})
    stats = catalog.stats()

    assert stats["bucket"]["running"] == running


def test_the_active_sql_matches_the_active_set() -> None:
    """The Python set and its SQL spelling must list the same statuses."""
    assert "prepared" not in _ACTIVE_STATUSES
    assert "'prepared'" not in _ACTIVE_SQL
    for status in _ACTIVE_STATUSES:
        assert f"'{status}'" in _ACTIVE_SQL, status
