"""Version-tolerant readers for EvalRuns artifacts."""

from __future__ import annotations

import hashlib
import json
import mimetypes
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_EVENT_PAYLOAD = 64_000


def load_json(path: Path) -> dict[str, Any] | list[Any] | None:
    """Read JSON without allowing malformed data to break the Console."""
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, (dict, list)) else None


def run_from_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    value = manifest.get("run")
    return dict(value) if isinstance(value, Mapping) else dict(manifest)


def runtime_from_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    value = manifest.get("runtime")
    return dict(value) if isinstance(value, Mapping) else {}


def safe_run_path(run_dir: Path, candidate: Path | str | None, fallback: Path) -> Path:
    """Resolve a manifest path only when it stays inside the Run directory."""
    value = Path(candidate) if candidate else fallback
    if not value.is_absolute():
        value = run_dir / value
    try:
        resolved = value.resolve()
        resolved.relative_to(run_dir.resolve())
    except (OSError, ValueError):
        safe_fallback = fallback
        try:
            safe_fallback = fallback.resolve()
            safe_fallback.relative_to(run_dir.resolve())
        except (OSError, ValueError):
            return run_dir.resolve() / ".missing"
        return safe_fallback
    return resolved


def read_manifest(run_dir: Path) -> dict[str, Any] | None:
    value = load_json(run_dir / "run-manifest.json")
    return value if isinstance(value, dict) else None


def evidence_dir(run_dir: Path, manifest: Mapping[str, Any] | None = None) -> Path:
    paths = manifest.get("paths") if isinstance(manifest, Mapping) else None
    configured = paths.get("evidence") if isinstance(paths, Mapping) else None
    return safe_run_path(run_dir, configured, run_dir / "workspace" / "evidence")


def trace_dir(run_dir: Path, manifest: Mapping[str, Any] | None = None) -> Path:
    paths = manifest.get("paths") if isinstance(manifest, Mapping) else None
    configured = paths.get("trace") if isinstance(paths, Mapping) else None
    return safe_run_path(run_dir, configured, run_dir / "workspace" / "trace")


def workspace_dir(run_dir: Path) -> Path:
    return run_dir / "workspace"


def read_evaluation(
    run_dir: Path, manifest: Mapping[str, Any] | None = None
) -> dict[str, Any] | None:
    value = load_json(evidence_dir(run_dir, manifest) / "evaluation.json")
    return value if isinstance(value, dict) else None


def read_verdict(run_dir: Path, manifest: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    value = load_json(evidence_dir(run_dir, manifest) / "verdict.json")
    return value if isinstance(value, dict) else None


def read_digest(run_dir: Path, manifest: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    value = load_json(trace_dir(run_dir, manifest) / "digest.json")
    return value if isinstance(value, dict) else None


def events_path(run_dir: Path, manifest: Mapping[str, Any] | None = None) -> Path:
    return trace_dir(run_dir, manifest) / "normalized-events.jsonl"


_SENSITIVE_KEY_TOKENS = ("key", "token", "secret", "password", "authorization", "cookie")


def _redact(value: Any, *, key: str = "", limit: int = MAX_EVENT_PAYLOAD) -> Any:
    """Bound and redact provider payloads before they reach a UI or log viewer."""
    lowered = key.lower()
    if any(token in lowered for token in _SENSITIVE_KEY_TOKENS):
        return "<redacted>"
    if isinstance(value, str):
        if len(value) > limit:
            return value[:limit] + "…"
        return value
    if isinstance(value, Mapping):
        return {
            str(name): _redact(item, key=str(name), limit=limit) for name, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, key=key, limit=limit) for item in value[:200]]
    return value


def _trim(value: Any, limit: int = MAX_EVENT_PAYLOAD) -> Any:
    return _redact(value, limit=limit)


def _value_text(value: Mapping[str, Any]) -> str:
    for key in ("summary", "text", "command", "name", "tool", "message"):
        item = value.get(key)
        if isinstance(item, str) and item:
            return item
    content = value.get("content")
    if isinstance(content, Mapping) and isinstance(content.get("text"), str):
        return str(content["text"])
    return ""


def normalize_event(raw: Mapping[str, Any], *, run_id: str, seq: int) -> dict[str, Any]:
    """Project legacy and v1 events into the Console Event DTO."""
    payload = raw.get("payload") if isinstance(raw.get("payload"), Mapping) else {}
    item = payload.get("item") if isinstance(payload.get("item"), Mapping) else {}
    update = payload.get("update") if isinstance(payload.get("update"), Mapping) else {}
    merged: dict[str, Any] = {**item, **update, **payload}
    event_type = str(raw.get("type") or "provider_event")
    actor = raw.get("actor") if isinstance(raw.get("actor"), Mapping) else {}
    actor_id = str(raw.get("agent_id") or actor.get("id") or "agent")
    source = raw.get("source") if isinstance(raw.get("source"), Mapping) else {}
    status = raw.get("status") or merged.get("status")
    if not isinstance(status, str):
        status = (
            "completed"
            if event_type.endswith("_completed") or event_type == "agent_message"
            else None
        )
    event_id = str(raw.get("event_id") or raw.get("id") or merged.get("id") or f"evt_{seq:06d}")
    summary = str(raw.get("summary") or _value_text(merged) or event_type.replace("_", " "))
    return {
        "schema_version": int(raw.get("schema_version") or 1),
        "event_id": event_id,
        "run_id": str(raw.get("run_id") or run_id),
        "seq": int(raw.get("seq") if isinstance(raw.get("seq"), int) else seq),
        "occurred_at": raw.get("occurred_at"),
        "type": event_type,
        "actor": {"kind": str(actor.get("kind") or "subject_agent"), "id": actor_id},
        "turn_id": raw.get("turn_id"),
        "step_id": raw.get("step_id"),
        "parent_event_id": raw.get("parent_event_id"),
        "call_id": raw.get("call_id") or merged.get("call_id") or merged.get("tool_call_id"),
        "status": status,
        "duration_ms": raw.get("duration_ms"),
        "summary": summary[:1000],
        "payload": _redact(raw.get("payload", raw)),
        "artifact_refs": list(raw.get("artifact_refs") or []),
        "source": {
            "adapter": str(source.get("adapter") or raw.get("adapter") or actor_id),
            "source_type": str(source.get("source_type") or raw.get("source_type") or "legacy"),
        },
        "legacy": not bool(raw.get("schema_version")),
    }


def _digest_event(item: Mapping[str, Any], *, run_id: str, seq: int) -> dict[str, Any]:
    kind = str(item.get("kind") or "provider_event")
    type_map = {
        "mcp_tool_call": "tool_call",
        "mcp_tool_result": "tool_result",
        "command_execution": "command_completed"
        if item.get("status") == "completed"
        else "command_started",
        "agent_message": "agent_message",
        "reasoning": "reasoning",
        "error": "error",
    }
    event_type = type_map.get(
        kind,
        kind
        if kind in {"run_started", "run_completed", "turn_started", "turn_completed"}
        else "provider_event",
    )
    status = item.get("status")
    if isinstance(status, str) and status.lower() == "none":
        status = None
    summary = (
        item.get("text")
        or item.get("tool")
        or item.get("command")
        or item.get("error")
        or kind.replace("_", " ")
    )
    return {
        "schema_version": 1,
        "event_id": f"digest_{seq:06d}",
        "run_id": run_id,
        "seq": seq,
        "occurred_at": None,
        "type": event_type,
        "actor": {"kind": "subject_agent", "id": "agent"},
        "turn_id": None,
        "step_id": None,
        "parent_event_id": None,
        "call_id": None,
        "status": status,
        "duration_ms": None,
        "summary": str(summary)[:1000],
        "payload": {"digest": dict(item)},
        "artifact_refs": [],
        "source": {"adapter": "digest", "source_type": "digest.timeline"},
        "legacy": True,
    }


def _event_matches(
    event: Mapping[str, Any],
    *,
    after_seq: int,
    types: set[str] | None,
    actor_kind: str | None,
    turn_id: str | None,
    query: str | None,
) -> bool:
    seq = int(event["seq"])
    if seq <= after_seq:
        return False
    if types and event["type"] not in types:
        return False
    if actor_kind and event["actor"]["kind"] != actor_kind:
        return False
    if turn_id and event.get("turn_id") != turn_id:
        return False
    return not query or query.lower() in json.dumps(event, ensure_ascii=False).lower()


def read_events(
    run_dir: Path,
    manifest: Mapping[str, Any] | None = None,
    *,
    after_seq: int = -1,
    limit: int = 100,
    types: set[str] | None = None,
    actor_kind: str | None = None,
    turn_id: str | None = None,
    query: str | None = None,
    events_file: Path | None = None,
    event_run_id: str | None = None,
) -> tuple[list[dict[str, Any]], bool, int]:
    """Read a bounded event page without loading an entire trace into memory."""
    path = events_file or events_path(run_dir, manifest)
    event_identity = event_run_id or run_dir.name
    events: list[dict[str, Any]] = []
    last_seq = after_seq
    page_limit = max(1, min(limit, 500))
    if not path.is_file():
        digest = read_digest(run_dir, manifest) or {}
        timeline = digest.get("timeline") if isinstance(digest.get("timeline"), list) else []
        for seq, item in enumerate(timeline):
            if not isinstance(item, Mapping):
                continue
            event = _digest_event(item, run_id=event_identity, seq=seq)
            last_seq = max(last_seq, seq)
            if not _event_matches(
                event,
                after_seq=after_seq,
                types=types,
                actor_kind=actor_kind,
                turn_id=turn_id,
                query=query,
            ):
                continue
            events.append(event)
            if len(events) >= page_limit:
                break
        return events, bool(timeline and events and last_seq < len(timeline) - 1), last_seq

    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return events, False, last_seq
    with handle:
        for index, line in enumerate(handle):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, Mapping):
                continue
            event = normalize_event(raw, run_id=event_identity, seq=index)
            seq = int(event["seq"])
            last_seq = max(last_seq, seq)
            if not _event_matches(
                event,
                after_seq=after_seq,
                types=types,
                actor_kind=actor_kind,
                turn_id=turn_id,
                query=query,
            ):
                continue
            events.append(event)
            if len(events) < page_limit:
                continue
            has_more = False
            for remaining_index, remaining_line in enumerate(handle, start=index + 1):
                if not remaining_line.strip():
                    continue
                try:
                    remaining_raw = json.loads(remaining_line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(remaining_raw, Mapping):
                    continue
                remaining_event = normalize_event(
                    raw=remaining_raw, run_id=event_identity, seq=remaining_index
                )
                if _event_matches(
                    remaining_event,
                    after_seq=after_seq,
                    types=types,
                    actor_kind=actor_kind,
                    turn_id=turn_id,
                    query=query,
                ):
                    has_more = True
                    break
            return events, has_more, last_seq
    return events, False, last_seq


def _artifact_id(relative_path: str, size: int, mtime_ns: int) -> str:
    raw = f"{relative_path}:{size}:{mtime_ns}".encode()
    return "artifact_" + hashlib.sha256(raw).hexdigest()[:16]


def _artifact_is_allowed(path: Path) -> bool:
    lowered = path.name.lower()
    if lowered in {".env", ".env.local", ".env.production", "credentials.json"}:
        return False
    return not any(
        token in lowered for token in ("secret", "token", "password", "apikey", "api_key")
    )


def _artifact_record(
    run_dir: Path, path: Path, *, role: str, created_by: str = "unknown"
) -> dict[str, Any] | None:
    if not _artifact_is_allowed(path):
        return None
    try:
        relative = path.resolve().relative_to(workspace_dir(run_dir).resolve()).as_posix()
        stat = path.stat()
    except (OSError, ValueError):
        return None
    kind = path.suffix.lower().lstrip(".") or "file"
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    previewable = mime.startswith(("text/", "image/")) or kind in {
        "json",
        "jsonl",
        "md",
        "yaml",
        "yml",
        "log",
        "txt",
        "diff",
    }
    return {
        "artifact_id": _artifact_id(relative, stat.st_size, stat.st_mtime_ns),
        "role": role,
        "relative_path": relative,
        "kind": kind,
        "mime_type": mime,
        "size": stat.st_size,
        "sha256": None,
        "created_by": created_by,
        "previewable": previewable,
    }


def read_artifacts(run_dir: Path) -> list[dict[str, Any]]:
    """Read declared artifacts; legacy fallback never enters game-engine."""
    workspace = workspace_dir(run_dir)
    manifest = load_json(workspace / "artifacts" / "manifest.json")
    if isinstance(manifest, dict) and isinstance(manifest.get("artifacts"), list):
        result = []
        for item in manifest["artifacts"]:
            if not isinstance(item, Mapping):
                continue
            relative = item.get("relative_path") or item.get("path")
            if not isinstance(relative, str):
                continue
            path = safe_run_path(run_dir, workspace / relative, workspace / relative)
            if not path.is_file():
                continue
            record = _artifact_record(
                run_dir,
                path,
                role=str(item.get("role") or "subject_output"),
                created_by=str(item.get("created_by") or "unknown"),
            )
            if record:
                record.update(
                    {
                        key: item[key]
                        for key in ("artifact_id", "kind", "mime_type", "sha256", "previewable")
                        if key in item
                    }
                )
                result.append(record)
        return result
    result: list[dict[str, Any]] = []
    for relative_root, role in (
        ("output", "subject_output"),
        ("evidence", "evaluation_evidence"),
        ("trace", "diagnostic"),
    ):
        base = workspace / relative_root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or not _artifact_is_allowed(path):
                continue
            try:
                if path.stat().st_size > 25_000_000:
                    continue
            except OSError:
                continue
            record = _artifact_record(run_dir, path, role=role)
            if record:
                result.append(record)
    return sorted(result, key=lambda item: item["relative_path"])


def artifact_path(run_dir: Path, artifact_id: str) -> tuple[dict[str, Any], Path] | None:
    for artifact in read_artifacts(run_dir):
        if artifact.get("artifact_id") == artifact_id:
            path = safe_run_path(
                run_dir,
                run_dir / "workspace" / artifact["relative_path"],
                run_dir / "workspace" / artifact["relative_path"],
            )
            if path.is_file():
                return artifact, path
    return None


def relative_run_path(run_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(run_dir.resolve()).as_posix()
    except ValueError:
        return path.name


def iso_duration_ms(start: Any, end: Any) -> int | None:
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        left = datetime.fromisoformat(start.replace("Z", "+00:00"))
        right = datetime.fromisoformat(end.replace("Z", "+00:00"))
        if left.tzinfo is None:
            left = left.replace(tzinfo=UTC)
        if right.tzinfo is None:
            right = right.replace(tzinfo=UTC)
        return max(0, int((right - left).total_seconds() * 1000))
    except ValueError:
        return None
