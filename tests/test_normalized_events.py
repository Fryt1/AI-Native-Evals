"""Tests for the common Agent event vocabulary."""

import json
from pathlib import Path

from ai_native_evals.adapters.events import normalize_log_file, read_normalized_events


def test_normalize_log_file_handles_codex_and_docker_timestamps(tmp_path: Path) -> None:
    raw = tmp_path / "agent.log"
    normalized = tmp_path / "normalized.jsonl"
    raw.write_text(
        """2026-09-08T13:00:00.000000000Z {\"type\":\"thread.started\",\"thread_id\":\"t1\"}\n"""
        "{\"type\":\"item.started\",\"item\":{"
        "\"type\":\"command_execution\",\"id\":\"c1\","
        "\"command\":\"echo ok\"}}\n",
        encoding="utf-8",
    )

    count = normalize_log_file(raw, normalized, adapter="codex", agent_id="codex")

    events = read_normalized_events(normalized)
    assert count == 2
    assert [event["type"] for event in events] == ["run_started", "command_started"]
    assert json.loads(normalized.read_text(encoding="utf-8").splitlines()[0])["agent_id"] == "codex"
