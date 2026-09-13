"""Provider-neutral Agent event normalization.

Raw Codex JSONL and DSH ACP messages are kept intact. This module only adds a
small stable event vocabulary used by process scorers, reports, and viewers.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

_NORMALIZED_TYPES = {
    "run_started",
    "run_completed",
    "turn_started",
    "turn_completed",
    "model_request",
    "model_response",
    "prompt",
    "message_started",
    "message_completed",
    "agent_message",
    "reasoning",
    "tool_call",
    "tool_result",
    "command_started",
    "command_completed",
    "file_changed",
    "approval_requested",
    "retry",
    "compaction",
    "error",
    "provider_event",
}


def normalize_codex_event(
    event: Mapping[str, Any],
    *,
    seq: int,
    agent_id: str = "codex",
) -> dict[str, Any] | None:
    """Map one Codex JSONL event into the stable event vocabulary."""
    event_type = str(event.get("type", ""))
    item = event.get("item")
    item = item if isinstance(item, Mapping) else {}
    item_type = str(item.get("type", ""))

    mapping: dict[str, str] = {
        "thread.started": "run_started",
        "thread.completed": "run_completed",
        "turn.started": "turn_started",
        "turn.completed": "turn_completed",
    }
    normalized_type = mapping.get(event_type)
    if normalized_type is None and event_type == "item.started":
        normalized_type = {
            "command_execution": "command_started",
            "mcp_tool_call": "tool_call",
            "agent_message": "agent_message",
            "reasoning": "reasoning",
        }.get(item_type, "provider_event")
    elif normalized_type is None and event_type == "item.completed":
        normalized_type = {
            "command_execution": "command_completed",
            "mcp_tool_call": "tool_result",
            "agent_message": "agent_message",
            "reasoning": "reasoning",
            "file_change": "file_changed",
            "error": "error",
        }.get(item_type, "provider_event")
    elif normalized_type is None and event_type in {"error", "turn.failed"}:
        normalized_type = "error"
    elif normalized_type is None:
        normalized_type = "provider_event"

    return _event(
        normalized_type,
        seq=seq,
        agent_id=agent_id,
        payload={
            "source_type": event_type,
            "item_type": item_type or None,
            "item": dict(item) if item else None,
            "raw": dict(event),
        },
    )


def normalize_pi_event(
    event: Mapping[str, Any],
    *,
    seq: int,
    agent_id: str = "pi",
) -> dict[str, Any] | None:
    """Map one pi JSONL event into the stable event vocabulary.

    ``pi --print --mode json`` writes one object per line. The names below were
    read from a real run through this repository's gateway, not from the help
    text: a completed turn emits `session`, `agent_start`, `turn_start`, the
    two user `message_*` events, then `message_start` / `message_update` /
    `message_end` for the assistant, `turn_end`, `agent_end` and
    `agent_settled`.

    `message_update` carries the streaming deltas and is mapped to a provider
    event rather than to a message: the assistant's text arrives complete in
    `message_end`, and emitting it twice would double every response.

    Tool calls arrive as pi's own names (`read`, `bash`, `edit`, `write`), which
    is what this vocabulary is for: a consumer asking "did this run touch a
    file" should not need to know which product produced the log.
    """
    event_type = str(event.get("type", ""))
    message = event.get("message")
    message = message if isinstance(message, Mapping) else {}
    role = str(message.get("role", ""))

    mapping: dict[str, str] = {
        "session": "run_started",
        "agent_start": "run_started",
        "agent_end": "run_completed",
        "agent_settled": "run_completed",
        "turn_start": "turn_started",
        "turn_end": "turn_completed",
        "tool_execution_start": "tool_call",
        "tool_execution_end": "tool_result",
        "auto_retry_start": "retry",
        "compaction_start": "compaction",
    }

    # A failed call arrives as a `message_end` whose assistant message carries
    # `errorMessage` and no content. This is checked first: filing it by role
    # would report a run that never reached the model as an answered one.
    error = message.get("errorMessage")
    if error or event_type == "error":
        normalized_type = "error"
    elif event_type in {"message_start", "message_end"}:
        # What a message *is* comes from its role, not from the envelope. The
        # same event type carries the prompt, the assistant's answer and a tool
        # result, so a mapping keyed on the type alone cannot tell them apart --
        # an earlier version had both, the dictionary won, and every user
        # message was filed as an unclassified provider event.
        normalized_type = {
            "assistant": "agent_message",
            "tool": "tool_result",
            "user": "prompt",
        }.get(role, "provider_event")
    else:
        normalized_type = mapping.get(event_type, "provider_event")

    return _event(
        normalized_type,
        seq=seq,
        agent_id=agent_id,
        payload={
            "source_type": event_type,
            "role": role or None,
            "stop_reason": message.get("stopReason"),
            "error": str(error) if error else None,
            "content": _pi_content_text(message.get("content")),
            "usage": message.get("usage") if isinstance(message.get("usage"), Mapping) else None,
            "raw": dict(event),
        },
    )


def _pi_content_text(content: Any) -> str:
    """Flatten a pi content-block list into the text it carries.

    Blocks are `{type: "text", text: ...}` and tool calls; joining the text
    blocks gives a reader the same sentences pi showed, without reaching into
    the rest of the structure.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, (list, tuple)):
        return ""
    parts = [
        str(block.get("text"))
        for block in content
        if isinstance(block, Mapping) and block.get("type") == "text" and block.get("text")
    ]
    return "\n".join(parts)


def normalize_dsh_acp_message(
    message: Mapping[str, Any],
    *,
    seq: int,
    agent_id: str = "dsh",
) -> dict[str, Any] | None:
    """Map one standard ACP response/notification into a stable event."""
    if message.get("type") == "agent.lifecycle":
        lifecycle = str(message.get("event", "provider_event"))
        if lifecycle not in {"run_started", "run_completed", "error"}:
            lifecycle = "provider_event"
        return _event(
            lifecycle,
            seq=seq,
            agent_id=agent_id,
            payload={"raw": dict(message)},
        )
    method = str(message.get("method", ""))
    params = message.get("params")
    params = params if isinstance(params, Mapping) else {}
    update = params.get("update")
    update = update if isinstance(update, Mapping) else params
    update_type = str(update.get("sessionUpdate", update.get("type", "")))

    if method == "session/update":
        normalized_type = {
            "agent_message_chunk": "agent_message",
            "agent_thought_chunk": "reasoning",
            "tool_call": "tool_call",
            "tool_call_update": "tool_result",
            "plan": "provider_event",
            "current_mode_update": "provider_event",
            "config_option_update": "provider_event",
            "usage_update": "provider_event",
        }.get(update_type, "provider_event")
        return _event(
            normalized_type,
            seq=seq,
            agent_id=agent_id,
            payload={
                "source_method": method,
                "update_type": update_type,
                "session_id": params.get("sessionId"),
                "update": dict(update),
                "raw": dict(message),
            },
        )

    if method == "session/request_permission":
        return _event(
            "approval_requested",
            seq=seq,
            agent_id=agent_id,
            payload={"raw": dict(message), "params": dict(params)},
        )
    if "error" in message:
        return _event("error", seq=seq, agent_id=agent_id, payload={"raw": dict(message)})
    return None


def normalize_event_lines(
    lines: Iterable[str],
    *,
    adapter: str,
    agent_id: str,
) -> list[dict[str, Any]]:
    """Normalize newline-delimited raw output, tolerating Docker timestamps."""
    normalized: list[dict[str, Any]] = []
    for line in lines:
        value = _parse_json_line(line)
        if not isinstance(value, Mapping):
            continue
        seq = len(normalized)
        if adapter == "codex":
            event = normalize_codex_event(value, seq=seq, agent_id=agent_id)
        elif adapter == "pi":
            event = normalize_pi_event(value, seq=seq, agent_id=agent_id)
        elif adapter in {"dsh", "dsh-acp"}:
            # The DSH entrypoint may wrap raw ACP messages in an envelope so
            # logs can identify their source without changing protocol stdout.
            if value.get("type") == "acp.message" and isinstance(value.get("message"), Mapping):
                value = value["message"]
            event = normalize_dsh_acp_message(value, seq=seq, agent_id=agent_id)
        else:
            event = _event(
                "provider_event",
                seq=seq,
                agent_id=agent_id,
                payload={"raw": dict(value)},
            )
        if event is not None:
            normalized.append(event)
    return normalized


def normalize_log_file(
    source: Path,
    destination: Path,
    *,
    adapter: str,
    agent_id: str,
) -> int:
    """Normalize one raw log file and persist the result as JSONL."""
    if not source.is_file():
        destination.write_text("", encoding="utf-8")
        return 0
    events = normalize_event_lines(
        source.read_text(encoding="utf-8", errors="replace").splitlines(),
        adapter=adapter,
        agent_id=agent_id,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        "".join(
            json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
            for event in events
        ),
        encoding="utf-8",
    )
    return len(events)


def read_normalized_events(path: Path) -> list[dict[str, Any]]:
    """Read normalized JSONL while ignoring incomplete or malformed lines."""
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("type") in _NORMALIZED_TYPES:
            events.append(value)
    return events


def _event(
    event_type: str,
    *,
    seq: int,
    agent_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "seq": seq,
        "type": event_type if event_type in _NORMALIZED_TYPES else "provider_event",
        "agent_id": agent_id,
        "payload": dict(payload),
    }


def _parse_json_line(line: str) -> Any:
    value = line.strip()
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        if start < 0:
            return None
        try:
            return json.loads(value[start:])
        except json.JSONDecodeError:
            return None


def compact_value(value: Any, limit: int = 2000) -> str:
    """Serialize provider-neutral diagnostic values with a bounded length."""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text[:limit]
