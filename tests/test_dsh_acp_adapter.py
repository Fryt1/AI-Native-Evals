"""Protocol-level tests for the standard DSH ACP adapter."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from ai_native_evals.adapters.dsh_acp import DshAcpAdapter, DshConfig
from ai_native_evals.contracts import AgentLaunchSpec


def test_dsh_acp_adapter_drives_one_session_and_normalizes_updates(tmp_path: Path) -> None:
    fake_server = tmp_path / "fake_acp.py"
    fake_server.write_text(
        """
import json
import sys

for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    request_id = request.get("id")
    if method == "session/prompt":
        update = {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": "s1",
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "done"},
                },
            },
        }
        print(json.dumps(update), flush=True)
        result = {"stopReason": "end_turn"}
    elif method == "session/new":
        result = {"sessionId": "s1", "configOptions": []}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)
""",
        encoding="utf-8",
    )
    spec = AgentLaunchSpec(
        agent_id="dsh",
        task_id="acp-001",
        run_dir=tmp_path / "run",
        workspace_dir=tmp_path / "run",
        task_text="do the task",
        model="test-model",
    )
    adapter = DshAcpAdapter(
        DshConfig(command=(sys.executable, "-u", str(fake_server)))
    )

    result = asyncio.run(adapter.run(spec))

    assert result.completed
    assert result.adapter == "dsh-acp"
    assert result.normalized_events_path is not None
    assert "agent_message" in result.normalized_events_path.read_text(encoding="utf-8")
    assert result.last_message_path.read_text(encoding="utf-8").strip() == "done"
