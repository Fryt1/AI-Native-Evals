"""Project Codex JSONL events into Inspect transcript messages."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from inspect_ai.model import ChatMessage, ChatMessageAssistant, ChatMessageSystem, ChatMessageTool
from inspect_ai.tool import ToolCall


def project_codex_events(path: Path) -> list[ChatMessage]:
    """Convert persisted Codex JSONL events into Inspect ChatMessages."""
    messages: list[ChatMessage] = []
    if not path.exists():
        return messages

    for event in _read_events(path):
        messages.extend(_project_event(event))
    return messages


def _read_events(path: Path) -> Iterable[Mapping[str, Any]]:
    """Yield valid JSON objects while ignoring malformed diagnostic lines."""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, Mapping):
            yield value


def _project_event(event: Mapping[str, Any]) -> list[ChatMessage]:
    """Project one Codex event without failing the whole evaluation."""
    event_type = event.get("type")
    item = event.get("item")

    if event_type == "thread.started":
        thread_id = _text(event.get("thread_id"), "unknown")
        return [_system(f"Agent started (Codex thread {thread_id})", event)]

    if event_type == "turn.started":
        return [_system("Turn started", event)]

    if event_type == "turn.completed":
        usage = event.get("usage")
        suffix = f": usage={_compact(usage)}" if isinstance(usage, Mapping) else ""
        return [_system(f"Agent turn completed{suffix}", event)]

    if event_type == "item.started" and isinstance(item, Mapping):
        return _project_item_started(item, event)

    if event_type == "item.completed" and isinstance(item, Mapping):
        return _project_item_completed(item, event)

    return []


def _project_item_started(item: Mapping[str, Any], event: Mapping[str, Any]) -> list[ChatMessage]:
    """Project a started Codex item as a visible action."""
    item_id = _text(item.get("id"), "codex-item")
    item_type = _text(item.get("type"), "item")
    if item_type == "command_execution":
        command = _text(item.get("command"), "")
        call = ToolCall(
            id=item_id,
            function="command_execution",
            arguments={"command": command},
        )
        return [
            ChatMessageAssistant(
                content="",
                source="generate",
                model="codex",
                tool_calls=[call],
                metadata={"codex_event": "tool_call", "item_id": item_id},
            )
        ]

    if item_type == "mcp_tool_call":
        function = _mcp_function(item)
        arguments = item.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        call = ToolCall(id=item_id, function=function, arguments=arguments)
        return [
            ChatMessageAssistant(
                content="",
                source="generate",
                model="codex",
                tool_calls=[call],
                metadata={"codex_event": "tool_call", "item_id": item_id},
            )
        ]

    return [_system(f"Agent action started: {item_type}", event)]


def _project_item_completed(item: Mapping[str, Any], event: Mapping[str, Any]) -> list[ChatMessage]:
    """Project a completed Codex item as a result or assistant message."""
    item_id = _text(item.get("id"), "codex-item")
    item_type = _text(item.get("type"), "item")

    if item_type == "agent_message":
        return [
            ChatMessageAssistant(
                content=_text(item.get("text"), ""),
                source="generate",
                model="codex",
                metadata={"codex_event": "agent_message", "item_id": item_id},
            )
        ]

    if item_type == "command_execution":
        content = _command_result(item)
        return [
            ChatMessageTool(
                content=content,
                source="generate",
                tool_call_id=item_id,
                function="command_execution",
                metadata={"codex_event": "tool_result", "item_id": item_id},
            )
        ]

    if item_type == "mcp_tool_call":
        return [
            ChatMessageTool(
                content=_mcp_result(item),
                source="generate",
                tool_call_id=item_id,
                function=_mcp_function(item),
                metadata={"codex_event": "tool_result", "item_id": item_id},
            )
        ]

    if item_type == "file_change":
        return [_system(f"File changed: {_file_change_summary(item)}", event)]

    return [_system(f"Agent action completed: {item_type}", event)]


def _system(content: str, event: Mapping[str, Any]) -> ChatMessageSystem:
    """Create a visible system event with bounded raw metadata."""
    return ChatMessageSystem(
        content=f"{content}\n\n",
        source="generate",
        metadata={"codex_event": event.get("type", "unknown")},
    )


def _command_result(item: Mapping[str, Any]) -> str:
    """Render a command execution result for the transcript."""
    output = _text(item.get("aggregated_output"), "").strip()
    status = _text(item.get("status"), "unknown")
    exit_code = item.get("exit_code")
    header = f"status={status}; exit_code={exit_code}"
    return f"{header}\n{output}".strip()


def _mcp_function(item: Mapping[str, Any]) -> str:
    """Return a readable MCP function name."""
    server = _text(item.get("server"), "mcp")
    tool = _text(item.get("tool"), _text(item.get("name"), "call"))
    return f"{server}.{tool}"


def _mcp_result(item: Mapping[str, Any]) -> str:
    """Render an MCP result without assuming one provider schema."""
    result = item.get("result", item.get("output", item.get("error", "")))
    return _compact(result)


def _file_change_summary(item: Mapping[str, Any]) -> str:
    """Extract a concise file-change label from provider-specific data."""
    changes = item.get("changes", item.get("files", item.get("path", "unknown")))
    if isinstance(changes, list):
        names = []
        for change in changes:
            if isinstance(change, Mapping):
                names.append(_text(change.get("path"), "unknown"))
            else:
                names.append(str(change))
        return ", ".join(names) or "unknown"
    return _compact(changes)


def _text(value: Any, fallback: str) -> str:
    """Convert a JSON value to bounded display text."""
    if value is None:
        return fallback
    return str(value)


def _compact(value: Any, limit: int = 2000) -> str:
    """Serialize event details without flooding the transcript."""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text[:limit]
