"""Unit tests for the Codex process adapter."""

import sys
from pathlib import Path

from ai_native_evals.adapters.codex import CodexAdapter, CodexConfig
from ai_native_evals.contracts import AgentLaunchSpec


def test_build_command_uses_codex_exec_and_isolated_directory(tmp_path: Path) -> None:
    # `sys.executable` rather than the literal "python": the adapter resolves the
    # executable on PATH, and a bare `python` does not exist on macOS or on many
    # Linux installs, so the test failed there for a reason that has nothing to
    # do with command construction.
    adapter = CodexAdapter(CodexConfig(executable=sys.executable))
    spec = AgentLaunchSpec(
        agent_id="codex",
        task_id="file-001",
        run_dir=tmp_path,
        task_text="Create hello.txt",
        model="test-model",
    )

    command = adapter.build_command(spec, tmp_path / "last-message.txt")

    assert command[1:5] == [
        "--sandbox",
        "workspace-write",
        "--ask-for-approval",
        "never",
    ]
    assert command[5] == "--model"
    assert command[7] == "exec"
    assert "--json" in command
    assert "--ephemeral" in command
    assert "--skip-git-repo-check" in command
    assert str(tmp_path) in command
    assert command[-1] == "Create hello.txt"
