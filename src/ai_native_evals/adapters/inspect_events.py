"""Project normalized Agent events into Inspect transcript messages."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from inspect_ai.model import ChatMessage, ChatMessageAssistant, ChatMessageSystem, ChatMessageTool
from inspect_ai.tool import ToolCall

from .events import read_normalized_events


def project_normalized_events(path: Path) -> list[ChatMessage]:
    """Render Codex, DSH, and future adapter traces through one vocabulary."""
    messages: list[ChatMessage] = []
    for event in read_normalized_events(path):
        event_type = event.get("type")
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        update = payload.get("update") if isinstance(payload.get("update"), Mapping) else {}
        item = payload.get("item") if isinstance(payload.get("item"), Mapping) else {}
        value = {**item, **update}
        event_id = str(value.get("id") or f"event-{event.get('seq', 0)}")
        if event_type == "agent_message":
            messages.append(
                ChatMessageAssistant(
                    content=_text_content(value),
                    source="generate",
                    model=str(event.get("agent_id", "agent")),
                    metadata={"normalized_event": event},
                )
            )
        elif event_type == "reasoning":
            messages.append(
                ChatMessageSystem(
                    content=f"Agent reasoning: {_text_content(value)}\n\n",
                    source="generate",
                    metadata={"normalized_event": event},
                )
            )
        elif event_type == "tool_call" or event_type == "command_started":
            function = str(value.get("name") or value.get("tool") or event_type)
            arguments = value.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {"command": value.get("command", "")} if value.get("command") else {}
            messages.append(
                ChatMessageAssistant(
                    content="",
                    source="generate",
                    model=str(event.get("agent_id", "agent")),
                    tool_calls=[ToolCall(id=event_id, function=function, arguments=arguments)],
                    metadata={"normalized_event": event},
                )
            )
        elif event_type in {"tool_result", "command_completed"}:
            messages.append(
                ChatMessageTool(
                    content=_compact(
                        value.get("output", value.get("result", value.get("error", value)))
                    ),
                    source="generate",
                    tool_call_id=event_id,
                    function=str(value.get("name") or value.get("tool") or event_type),
                    metadata={"normalized_event": event},
                )
            )
        else:
            messages.append(
                ChatMessageSystem(
                    content=f"{event_type}: {_compact(value or payload)}\n\n",
                    source="generate",
                    metadata={"normalized_event": event},
                )
            )
    return messages


def _text_content(value: Mapping[str, Any]) -> str:
    text = value.get("text")
    if isinstance(text, str):
        return text
    content = value.get("content")
    if isinstance(content, Mapping) and isinstance(content.get("text"), str):
        return str(content["text"])
    return ""


def _compact(value: Any, limit: int = 2000) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text[:limit]
