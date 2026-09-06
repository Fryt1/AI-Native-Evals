"""Tests for projecting Codex JSONL events into Inspect messages."""

import json
from pathlib import Path

from inspect_ai.model import ChatMessageAssistant, ChatMessageSystem, ChatMessageTool

from ai_native_evals.adapters.codex_events import project_codex_events


def test_project_codex_events_exposes_lifecycle_tool_and_agent_messages(tmp_path: Path) -> None:
    events = [
        {"type": "thread.started", "thread_id": "thread-1"},
        {"type": "turn.started"},
        {
            "type": "item.started",
            "item": {
                "id": "item-1",
                "type": "command_execution",
                "command": "echo hello",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "item-1",
                "type": "command_execution",
                "status": "completed",
                "exit_code": 0,
                "aggregated_output": "hello",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "item-2",
                "type": "agent_message",
                "text": "Done",
            },
        },
        {"type": "turn.completed", "usage": {"output_tokens": 2}},
    ]
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")

    messages = project_codex_events(path)

    assert isinstance(messages[0], ChatMessageSystem)
    assert "Agent started" in messages[0].content
    assert any(isinstance(message, ChatMessageAssistant) for message in messages)
    assert any(isinstance(message, ChatMessageTool) for message in messages)
    assert any(getattr(message, "content", "") == "Done" for message in messages)


def test_project_codex_events_ignores_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        "not json\n{\"type\": \"thread.started\", \"thread_id\": \"x\"}\n",
        encoding="utf-8",
    )

    messages = project_codex_events(path)

    assert len(messages) == 1
