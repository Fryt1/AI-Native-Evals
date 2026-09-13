"""Rebuildable SQLite catalog for the EvalRuns read model."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .readers import (
    events_path,
    iso_duration_ms,
    load_json,
    read_artifacts,
    read_digest,
    read_evaluation,
    read_manifest,
    run_from_manifest,
    runtime_from_manifest,
    trace_dir,
)

# A run that has not finished has no duration. Reporting one would either invent
# a number or, today, flatten it to 0 ms and make a live run look instantaneous.
#
# `prepared` is deliberately absent. It means "the workspace exists but nothing
# was started", and a run that was prepared and then abandoned stays in that
# state forever -- treating it as active would park a stale run at the top of
# the list and under the "running" filter indefinitely.
#
# This one set drives the ordering, the filter, and the bucket counts, so the
# three cannot disagree about whether a run is live.
_ACTIVE_STATUSES = frozenset({"starting", "preparing", "running", "evaluating"})

#: The SQL spelling of :data:`_ACTIVE_STATUSES`, so the two stay in step.
_ACTIVE_SQL = "status IN ('starting', 'preparing', 'running', 'evaluating')"


def _runs_columns(schema: str) -> tuple[str, ...]:
    """The `runs` column list, read from the schema that creates it.

    Derived rather than written twice: a hand-kept copy is exactly what drifts
    when a column is added, and the symptom is an insert failing at runtime.
    """
    import re

    body = schema.split("CREATE TABLE IF NOT EXISTS runs (", 1)[1].split(");", 1)[0]
    columns = []
    for line in body.splitlines():
        text = line.strip().rstrip(",")
        if not text or text.startswith(("PRIMARY KEY", "UNIQUE", "FOREIGN KEY")):
            continue
        name = text.split()[0]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            columns.append(name)
    return tuple(columns)


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    run_dir TEXT NOT NULL,
    task_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    model TEXT,
    status TEXT,
    decision TEXT,
    outcome_score REAL,
    quality_score REAL,
    process_score REAL,
    started_at TEXT,
    finished_at TEXT,
    duration_ms INTEGER,
    failed_check_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    artifact_count INTEGER NOT NULL DEFAULT 0,
    source_mtime_ns INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_finished ON runs(finished_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_task ON runs(task_id);
CREATE INDEX IF NOT EXISTS idx_runs_agent ON runs(agent_id);
CREATE INDEX IF NOT EXISTS idx_runs_decision ON runs(decision);
CREATE TABLE IF NOT EXISTS comparisons (
    comparison_id TEXT PRIMARY KEY,
    comparison_dir TEXT NOT NULL,
    task_id TEXT,
    created_at TEXT,
    run_count INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL,
    source_mtime_ns INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS index_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

#: The columns `runs` must have, taken from the statement that creates it.
_RUN_COLUMNS = _runs_columns(SCHEMA)


class Catalog:
    """Small local read index; raw Run directories remain the source of truth."""

    def __init__(self, runs_root: Path) -> None:
        self.runs_root = runs_root.resolve()
        self.index_dir = self.runs_root / ".console"
        self.db_path = self.index_dir / "catalog.sqlite"
        self._last_scan = 0.0
        self._index_lock = threading.Lock()
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        """Create the index, replacing it when it was built by an older schema.

        The catalog is a rebuildable projection of EvalRuns, which stay the
        source of truth, so a schema change drops it rather than migrating:
        `CREATE TABLE IF NOT EXISTS` would silently leave the old shape in
        place, and every insert would then fail on a column count that no
        longer matches. Rebuilding costs one scan.
        """
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='runs'"
            ).fetchone()
            if existing is not None and not self._schema_matches(connection):
                connection.executescript(
                    "DROP TABLE IF EXISTS runs;"
                    "DROP TABLE IF EXISTS comparisons;"
                    "DROP TABLE IF EXISTS index_state;"
                )
            connection.executescript(SCHEMA)

    @staticmethod
    def _schema_matches(connection: sqlite3.Connection) -> bool:
        """Whether the stored `runs` table has the columns this code expects."""
        expected = _RUN_COLUMNS
        actual = tuple(
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(runs)").fetchall()
        )
        return actual == expected

    def reindex(self) -> dict[str, int]:
        self._init_db()
        indexed_runs = 0
        skipped_runs = 0
        changed_runs = 0
        seen_runs: set[str] = set()
        with self._connect() as connection:
            if self.runs_root.is_dir():
                for run_dir in sorted(self.runs_root.iterdir()):
                    if not run_dir.is_dir() or run_dir.name in {".console", "comparisons"}:
                        continue
                    manifest_file = run_dir / "run-manifest.json"
                    if not manifest_file.is_file():
                        skipped_runs += 1
                        continue
                    manifest = read_manifest(run_dir)
                    if not manifest:
                        skipped_runs += 1
                        continue
                    run = run_from_manifest(manifest)
                    runtime = runtime_from_manifest(manifest)
                    run_id = str(run.get("run_id") or run_dir.name)
                    seen_runs.add(run_id)
                    source_files = [
                        manifest_file,
                        run_dir / "workspace" / "evidence" / "evaluation.json",
                        run_dir / "workspace" / "evidence" / "verdict.json",
                        events_path(run_dir, manifest),
                        trace_dir(run_dir, manifest) / "digest.json",
                        run_dir / "workspace" / "artifacts" / "manifest.json",
                    ]
                    mtime = max(
                        (path.stat().st_mtime_ns for path in source_files if path.is_file()),
                        default=manifest_file.stat().st_mtime_ns,
                    )
                    old = connection.execute(
                        "SELECT source_mtime_ns FROM runs WHERE run_id = ?", (run_id,)
                    ).fetchone()
                    if old and int(old[0]) == mtime:
                        indexed_runs += 1
                        continue
                    evaluation = read_evaluation(run_dir, manifest) or {}
                    checks = (
                        evaluation.get("checks")
                        if isinstance(evaluation.get("checks"), list)
                        else []
                    )
                    failed = sum(
                        1
                        for check in checks
                        if isinstance(check, dict) and check.get("status") in {"failed", "error"}
                    )
                    digest = read_digest(run_dir, manifest) or {}
                    stats = digest.get("stats") if isinstance(digest.get("stats"), dict) else {}
                    errors = int(stats.get("errors") or 0)
                    status = manifest.get("status")
                    started = runtime.get("started_at") or run.get("created_at") or ""
                    finished = (
                        runtime.get("completed_at")
                        or runtime.get("stopped_at")
                        or ("" if status in _ACTIVE_STATUSES else run.get("created_at"))
                        or ""
                    )
                    payload = {
                        "run_id": run_id,
                        "task_id": str(run.get("task_id") or ""),
                        "agent_id": str(run.get("agent") or ""),
                        "model": run.get("model"),
                        "status": status,
                        "decision": evaluation.get("decision"),
                        "outcome_score": evaluation.get("outcome_score"),
                        "quality_score": evaluation.get("quality_score"),
                        "process_score": evaluation.get("process_score"),
                        "started_at": started,
                        "finished_at": finished,
                        "duration_ms": iso_duration_ms(started, finished),
                        "failed_check_count": failed,
                        "error_count": errors,
                        "artifact_count": len(read_artifacts(run_dir)),
                        "run_dir": str(run_dir),
                    }
                    values = (
                        run_id,
                        str(run_dir),
                        payload["task_id"],
                        payload["agent_id"],
                        payload["model"],
                        payload["status"],
                        payload["decision"],
                        payload["outcome_score"],
                        payload["quality_score"],
                        payload["process_score"],
                        payload["started_at"],
                        payload["finished_at"],
                        payload["duration_ms"],
                        payload["failed_check_count"],
                        payload["error_count"],
                        payload["artifact_count"],
                        mtime,
                        json.dumps(payload, ensure_ascii=False),
                    )
                    run_upsert_sql = (
                        "INSERT INTO runs VALUES ("
                        "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(run_id) DO UPDATE SET "
                        "run_dir=excluded.run_dir, task_id=excluded.task_id, "
                        "agent_id=excluded.agent_id, model=excluded.model, "
                        "status=excluded.status, decision=excluded.decision, "
                        "outcome_score=excluded.outcome_score, "
                        "quality_score=excluded.quality_score, "
                        "process_score=excluded.process_score, "
                        "started_at=excluded.started_at, finished_at=excluded.finished_at, "
                        "duration_ms=excluded.duration_ms, "
                        "failed_check_count=excluded.failed_check_count, "
                        "error_count=excluded.error_count, artifact_count=excluded.artifact_count, "
                        "source_mtime_ns=excluded.source_mtime_ns, "
                        "payload_json=excluded.payload_json"
                    )
                    connection.execute(run_upsert_sql, values)
                    changed_runs += 1
                    indexed_runs += 1
                for row in connection.execute("SELECT run_id FROM runs").fetchall():
                    if row[0] not in seen_runs:
                        connection.execute("DELETE FROM runs WHERE run_id = ?", (row[0],))
            comparison_root = self.runs_root / "comparisons"
            seen_comparisons: set[str] = set()
            if comparison_root.is_dir():
                for comparison_dir in sorted(comparison_root.iterdir()):
                    path = comparison_dir / "comparison.json"
                    if not path.is_file():
                        continue
                    payload = load_json(path)
                    if not isinstance(payload, dict):
                        continue
                    comparison_id = str(payload.get("comparison_id") or comparison_dir.name)
                    seen_comparisons.add(comparison_id)
                    invariant = (
                        payload.get("invariant")
                        if isinstance(payload.get("invariant"), dict)
                        else {}
                    )
                    comparison_upsert_sql = (
                        "INSERT INTO comparisons VALUES (?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(comparison_id) DO UPDATE SET "
                        "comparison_dir=excluded.comparison_dir, task_id=excluded.task_id, "
                        "created_at=excluded.created_at, run_count=excluded.run_count, "
                        "payload_json=excluded.payload_json, "
                        "source_mtime_ns=excluded.source_mtime_ns"
                    )
                    connection.execute(
                        comparison_upsert_sql,
                        (
                            comparison_id,
                            str(comparison_dir),
                            invariant.get("task_id"),
                            payload.get("created_at"),
                            len(payload.get("runs") or [])
                            if isinstance(payload.get("runs"), list)
                            else 0,
                            json.dumps(payload, ensure_ascii=False),
                            path.stat().st_mtime_ns,
                        ),
                    )
            for row in connection.execute("SELECT comparison_id FROM comparisons").fetchall():
                if row[0] not in seen_comparisons:
                    connection.execute("DELETE FROM comparisons WHERE comparison_id = ?", (row[0],))
            connection.execute(
                "INSERT INTO index_state(key, value) VALUES("
                "'last_indexed_at', ?) ON CONFLICT(key) DO UPDATE SET "
                "value=excluded.value",
                (str(time.time()),),
            )
        self._last_scan = time.monotonic()
        return {
            "indexed_runs": indexed_runs,
            "changed_runs": changed_runs,
            "skipped_runs": skipped_runs,
        }

    def ensure_current(self, *, force: bool = False) -> None:
        """Reindex at most once per interval, even when requests arrive together."""
        if force or time.monotonic() - self._last_scan > 2.0:
            with self._index_lock:
                # Re-check under the lock: a concurrent caller may have just finished.
                if force or time.monotonic() - self._last_scan > 2.0:
                    self.reindex()

    @staticmethod
    def _summary(row: sqlite3.Row, *, include_internal: bool = False) -> dict[str, Any]:
        value = json.loads(row["payload_json"])
        if not include_internal:
            value.pop("run_dir", None)
        return value

    def list_runs(
        self, *, filters: dict[str, Any] | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        self.ensure_current()
        filters = filters or {}
        clauses: list[str] = []
        params: list[Any] = []
        for field in ("task_id", "agent_id", "model", "status", "decision"):
            value = filters.get(field)
            if isinstance(value, str) and value:
                clauses.append(f"{field} = ?")
                params.append(value)
        bucket = filters.get("bucket")
        if bucket == "running":
            clauses.append(_ACTIVE_SQL)
        elif bucket == "failed":
            clauses.append("(status IN ('failed', 'stopped') OR decision = 'fail')")
        elif bucket == "review":
            clauses.append("decision = 'review'")
        elif bucket == "passed":
            clauses.append("decision = 'pass'")
        if filters.get("has_errors") is True:
            clauses.append("error_count > 0")
        query = filters.get("query")
        if isinstance(query, str) and query:
            clauses.append("(run_id LIKE ? OR task_id LIKE ? OR agent_id LIKE ? OR model LIKE ?)")
            params.extend([f"%{query}%"] * 4)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit = max(1, min(int(filters.get("limit") or 50), 200))
        offset = max(0, int(filters.get("offset") or 0))
        with self._connect() as connection:
            total = int(
                connection.execute(f"SELECT COUNT(*) FROM runs {where}", params).fetchone()[0]
            )
            # Newest first is the primary order. An earlier version grouped by
            # status ahead of time, which put a week-old failure above the run
            # the operator had just finished -- the opposite of what the list is
            # for. In-flight runs still float up, because they are the only ones
            # with something happening right now.
            rows = connection.execute(
                f"SELECT * FROM runs {where} "
                "ORDER BY "
                f"CASE WHEN {_ACTIVE_SQL} THEN 0 ELSE 1 END, "
                "COALESCE(finished_at, started_at) DESC, source_mtime_ns DESC "
                "LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [self._summary(row) for row in rows], total

    def stats(self) -> dict[str, Any]:
        """Return aggregate counts for the Runs header without loading every Run."""
        self.ensure_current()
        with self._connect() as connection:
            status_rows = connection.execute(
                "SELECT COALESCE(status, 'unknown') AS value, COUNT(*) AS count "
                "FROM runs GROUP BY status"
            ).fetchall()
            decision_rows = connection.execute(
                "SELECT COALESCE(decision, 'unscored') AS value, COUNT(*) AS count "
                "FROM runs GROUP BY decision"
            ).fetchall()
            bucket_rows = connection.execute(
                "SELECT "
                f"SUM(CASE WHEN {_ACTIVE_SQL} THEN 1 ELSE 0 END) AS running, "
                "SUM(CASE WHEN status IN ('failed', 'stopped') OR decision = 'fail' "
                "THEN 1 ELSE 0 END) AS failed, "
                "SUM(CASE WHEN decision = 'review' THEN 1 ELSE 0 END) AS review, "
                "SUM(CASE WHEN decision = 'pass' THEN 1 ELSE 0 END) AS passed "
                "FROM runs"
            ).fetchone()
        return {
            "status": {str(row["value"]): int(row["count"]) for row in status_rows},
            "decision": {str(row["value"]): int(row["count"]) for row in decision_rows},
            "bucket": {
                key: int(bucket_rows[key] or 0) for key in ("running", "failed", "review", "passed")
            },
        }

    def get_run(self, run_id: str, *, include_internal: bool = False) -> dict[str, Any] | None:
        self.ensure_current()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._summary(row, include_internal=include_internal) if row else None

    def list_comparisons(self) -> list[dict[str, Any]]:
        self.ensure_current()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM comparisons ORDER BY created_at DESC"
            ).fetchall()
        result = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            result.append(
                {
                    "comparison_id": row["comparison_id"],
                    "task_id": row["task_id"],
                    "created_at": row["created_at"],
                    "run_count": row["run_count"],
                    "output_dir": payload.get("output_dir"),
                }
            )
        return result

    def get_comparison(self, comparison_id: str) -> dict[str, Any] | None:
        self.ensure_current()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM comparisons WHERE comparison_id = ?", (comparison_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def diagnostics(self) -> dict[str, Any]:
        self.ensure_current()
        with self._connect() as connection:
            run_count = int(connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
            comparison_count = int(
                connection.execute("SELECT COUNT(*) FROM comparisons").fetchone()[0]
            )
            last = connection.execute(
                "SELECT value FROM index_state WHERE key='last_indexed_at'"
            ).fetchone()
        return {
            "runs_root": str(self.runs_root),
            "catalog_path": str(self.db_path),
            "run_count": run_count,
            "comparison_count": comparison_count,
            "last_indexed_at": float(last[0]) if last else None,
        }
