"""Per-run digest: turn one agent-container.log into a 'did it work + how did it work' report.

The digest never decides pass/fail — that is the verify gate's job. It answers:

- what did the agent actually do (tool/shell/read calls, in order)
- how much did it struggle (failed calls, retries, tool churn)
- what did it spend its time on (context on docs vs. action)
- what was the last thing it claimed
- where the raw evidence lives

Input: a run directory (run-manifest.json + trace/agent-container.log + evidence/verification.json)
Output: digest JSON (metadata + stats + ordered event log)
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class DigestError(RuntimeError):
    """Raised when a run directory cannot be digested."""


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DigestError(f"file does not exist: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise DigestError(f"could not parse {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise DigestError(f"expected a JSON object in {path}")
    return payload


def _iter_log_events(log_path: Path) -> Iterable[Mapping[str, Any]]:
    """Yield parsed JSON objects from a docker log (timestamped lines ok)."""
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            start = line.find("{")
            if start < 0:
                continue
            yield json.loads(line[start:])
        except json.JSONDecodeError:
            continue


def digest_run(run_dir: str | Path) -> dict[str, Any]:
    """Produce the digest dict for one run directory."""
    run_dir = Path(run_dir).resolve()
    manifest_path = run_dir / "run-manifest.json"
    if not manifest_path.is_file():
        raise DigestError(f"not a run directory (no run-manifest.json): {run_dir}")

    manifest = _load_json(manifest_path)
    run = manifest.get("run", {})
    paths = manifest.get("paths", {})
    trace_path = Path(paths.get("trace", run_dir / "trace"))
    log_path = trace_path / "agent-container.log"

    started_at = (run.get("created_at") or "").replace("+00:00", "Z")
    events = list(_iter_log_events(log_path)) if log_path.is_file() else []
    completed = [
        ev.get("item", {})
        for ev in events
        if ev.get("type") == "item.completed" and isinstance(ev.get("item"), dict)
    ]

    # ---- categorize ----
    by_kind: Counter[str] = Counter()
    mcp_calls: list[dict[str, Any]] = []
    shell_commands: list[dict[str, Any]] = []
    reads: list[dict[str, Any]] = []
    agent_messages: list[dict[str, Any]] = []
    reasoning_blocks: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for item in completed:
        kind = item.get("type", "?")
        by_kind[kind] += 1
        if kind == "mcp_tool_call":
            mcp_calls.append(item)
        elif kind == "command_execution":
            shell_commands.append(item)
        elif kind == "file_read" or kind == "text_read":
            reads.append(item)
        elif kind == "agent_message":
            agent_messages.append(item)
        elif kind == "reasoning":
            reasoning_blocks.append(item)
        elif kind == "error":
            errors.append(item)

    # ---- MCP stats ----
    mcp_total = len(mcp_calls)
    mcp_failed = [c for c in mcp_calls if c.get("status") == "failed" or c.get("error")]
    mcp_server_totals: Counter[str] = Counter()
    mcp_server_failed: Counter[str] = Counter()
    mcp_tool_totals: Counter[str] = Counter()
    mcp_tool_failed: Counter[str] = Counter()
    failed_calls: list[dict[str, Any]] = []
    def _real_tool(call: Mapping[str, Any]) -> str:
        """Unwrap the call_tool wrapper used by unreal-mcp into the real tool name."""
        tool = str(call.get("tool", "?"))
        if tool == "call_tool":
            args = call.get("arguments")
            if isinstance(args, dict):
                nested = args.get("tool_name")
                if nested:
                    tool = f"call_tool:{nested}"
        return tool

    for call in mcp_calls:
        server = str(call.get("server", "?"))
        tool = _real_tool(call)
        mcp_server_totals[server] += 1
        mcp_tool_totals[tool] += 1
        if call.get("status") == "failed" or call.get("error"):
            mcp_server_failed[server] += 1
            mcp_tool_failed[tool] += 1
            failed_calls.append(
                {
                    "server": server,
                    "tool": tool,
                    "arguments": call.get("arguments"),
                    "error": _truncate(str(call.get("error")), 300),
                }
            )

    shell_total = len(shell_commands)
    shell_failed = [c for c in shell_commands if c.get("exit_code") not in (0, None)]
    # ---- time spent: classify shell commands as doc-reading vs action ----
    reading_cmds = [
        c for c in shell_commands if _is_doc_command(str(c.get("command", "")))
    ]
    action_cmds = [
        c for c in shell_commands if not _is_doc_command(str(c.get("command", "")))
    ]

    last_message = agent_messages[-1].get("text", "") if agent_messages else ""
    reasoning_chars = sum(len(str(b.get("text", ""))) for b in reasoning_blocks)

    verify = None
    verify_path = Path(paths.get("evidence", run_dir / "evidence")) / "verification.json"
    if verify_path.is_file():
        try:
            v = _load_json(verify_path)
            verify = {
                "passed": v.get("passed"),
                "path": str(verify_path),
            }
        except DigestError:
            verify = {"passed": None, "path": str(verify_path), "error": "unparseable"}

    timeline = [
        {
            "kind": it.get("type"),
            "server": it.get("server"),
            "tool": (
                f"call_tool:{it.get('arguments', {}).get('tool_name')}"
                if it.get("tool") == "call_tool" and isinstance(it.get("arguments"), dict)
                and it.get("arguments", {}).get("tool_name")
                else it.get("tool")
            ),
            "command": _truncate(str(it.get("command", "")), 180),
            "text": _truncate(str(it.get("text", "")), 180),
            "status": it.get("status"),
            "exit_code": it.get("exit_code"),
            "error": _truncate(str(it.get("error", "")), 200),
        }
        for it in completed
    ]

    return {
        "run_id": str(run.get("run_id", run_dir.name)),
        "task_id": str(run.get("task_id", "")),
        "agent": str(run.get("agent", "")),
        "model": str(run.get("model", "")),
        "protocol": str(run.get("protocol", "")),
        "reasoning_effort": str(run.get("reasoning_effort", "")),
        "started_at": started_at,
        "digested_at": datetime.now(UTC).isoformat(),
        "paths": {
            "run_dir": str(run_dir),
            "log": str(log_path),
            "manifest": str(manifest_path),
            "evidence": str(Path(paths.get("evidence", run_dir / "evidence"))),
        },
        "verify": verify,
        "stats": {
            "event_items": by_kind,
            "mcp_calls_total": mcp_total,
            "mcp_calls_failed": len(mcp_failed),
            "mcp_success_rate": (
                round((mcp_total - len(mcp_failed)) / mcp_total, 3)
                if mcp_total
                else None
            ),
            "mcp_calls_by_server": dict(mcp_server_totals),
            "mcp_failed_by_server": dict(mcp_server_failed),
            "mcp_calls_by_tool": dict(mcp_tool_totals),
            "mcp_failed_by_tool": dict(mcp_tool_failed),
            "shell_commands_total": shell_total,
            "shell_commands_failed": len(shell_failed),
            "doc_reading_commands": len(reading_cmds),
            "action_commands": len(action_cmds),
            "agent_messages": len(agent_messages),
            "reasoning_blocks": len(reasoning_blocks),
            "reasoning_chars": reasoning_chars,
            "errors": len(errors),
        },
        "struggle": {
            "failed_tool_calls": len(failed_calls),
            "tool_churn": sorted(mcp_tool_failed.items(), key=lambda kv: kv[1], reverse=True)[:10],
            "top_failed_calls": failed_calls[:10],
        },
        "effort_profile": {
            "doc_reading_commands": len(reading_cmds),
            "action_commands": len(action_cmds),
            "doc_share": round(len(reading_cmds) / shell_total, 3) if shell_total else None,
        },
        "last_agent_message": last_message,
        "timeline": timeline,
    }


def _is_doc_command(command: str) -> bool:
    lowered = command.lower()
    return any(
        marker in lowered
        for marker in ("agents.md", "skills/", "docs/", "sed -n", "cat ", "read ", "find ", "ls ")
    ) and not any(
        marker in lowered
        for marker in ("python", "git ", "blender", "unreal", "codex", "npm", "uv ")
    )


def _truncate(value: str, limit: int) -> str:
    value = value or ""
    return value if len(value) <= limit else value[:limit] + "…"


def render_digest_markdown(digest: dict[str, Any]) -> str:
    """Render the digest as a compact human-readable report."""
    s = digest["stats"]
    st = digest["struggle"]
    ep = digest["effort_profile"]
    lines: list[str] = []
    lines.append(f"# Run digest: {digest['run_id']}")
    lines.append("")
    lines.append(
        f"- task: `{digest['task_id']}`  agent: `{digest['agent']}`  model: `{digest['model']}`"
    )
    lines.append(f"- started: {digest['started_at']}")
    v = digest["verify"]
    if v and v.get("passed") is not None:
        verdict = "PASS" if v["passed"] else "FAIL"
        lines.append(
            f"- **verify: {verdict}** (independent host read-back, {v.get('path')})"
        )
    else:
        lines.append("- verify: not available (no verification.json)")
    lines.append("")
    lines.append("## What the agent did")
    lines.append("")
    rate = s.get("mcp_success_rate")
    rate_txt = f"{rate * 100:.0f}%" if rate is not None else "n/a"
    lines.append(
        f"- MCP tool calls: **{s['mcp_calls_total']}** (success rate {rate_txt})"
    )
    lines.append(f"- shell commands: **{s['shell_commands_total']}**")
    lines.append(
        f"- agent messages: {s['agent_messages']}  reasoning blocks: {s['reasoning_blocks']}"
    )
    lines.append("")
    lines.append("## Struggle signals")
    lines.append("")
    if st["tool_churn"]:
        lines.append("Most failed tools (tool -> #failures):")
        for tool, count in st["tool_churn"]:
            lines.append(f"- `{tool}` x{count}")
    else:
        lines.append("- no failed MCP calls")
    if ep["doc_share"] is not None and ep["doc_share"] > 0.3:
        lines.append(
            f"- ⚠ {ep['doc_share'] * 100:.0f}% of shell commands were doc/context reading "
            f"({ep['doc_reading_commands']}/{s['shell_commands_total']})"
        )
    lines.append("")
    lines.append("## Last thing the agent claimed")
    lines.append("")
    lines.append(f"> {digest['last_agent_message'][:600] or '(none)'}")
    lines.append("")
    lines.append("## Raw evidence")
    lines.append("")
    lines.append(f"- log: `{digest['paths']['log']}`")
    lines.append(f"- manifest: `{digest['paths']['manifest']}`")
    lines.append(f"- evidence: `{digest['paths']['evidence']}`")
    return "\n".join(lines)
