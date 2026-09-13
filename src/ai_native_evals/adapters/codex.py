"""Codex process adapter behind the provider-neutral Agent seam."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..contracts import AgentLaunchSpec, AgentRunResult, AgentRunStatus
from .events import normalize_log_file

if TYPE_CHECKING:
    from ..agents.profile import AgentProfile


@dataclass(frozen=True, slots=True)
class CodexConfig:
    """Process-level options for a non-interactive Codex run."""

    executable: str = "codex"
    sandbox: str = "workspace-write"
    ask_for_approval: str = "never"
    ephemeral: bool = True
    skip_git_repo_check: bool = True

    @classmethod
    def from_profile(cls, profile: AgentProfile) -> CodexConfig:
        """Project profile options into Codex-only flags at the adapter seam."""
        options = profile.options
        return cls(
            executable=str(options.get("executable", "codex")),
            sandbox=str(options.get("sandbox", "workspace-write")),
            ask_for_approval=str(options.get("ask_for_approval", "never")),
            ephemeral=bool(options.get("ephemeral", True)),
            skip_git_repo_check=bool(options.get("skip_git_repo_check", True)),
        )


class CodexAdapter:
    """Run Codex locally and persist raw plus normalized execution evidence."""

    adapter_id = "codex"

    def __init__(self, config: CodexConfig | None = None) -> None:
        self.config = config or CodexConfig()

    def _resolve_executable(self) -> str:
        """Resolve the configured executable using the current PATH."""
        executable = shutil.which(self.config.executable)
        if executable is None:
            raise FileNotFoundError(
                f"Codex executable was not found on PATH: {self.config.executable}"
            )
        return executable

    def build_command(self, spec: AgentLaunchSpec, last_message_path: Path) -> list[str]:
        """Build the argv used for one Codex ``exec`` invocation.

        Global Codex flags intentionally precede the ``exec`` subcommand. The
        current Windows CLI parses sandbox and approval policy at that level.
        """
        command = [
            self._resolve_executable(),
            "--sandbox",
            self.config.sandbox,
            "--ask-for-approval",
            self.config.ask_for_approval,
        ]
        if spec.model:
            command.extend(["--model", spec.model])
        command.extend(["exec", "--json"])
        if self.config.ephemeral:
            command.append("--ephemeral")
        if self.config.skip_git_repo_check:
            command.append("--skip-git-repo-check")
        command.extend(
            [
                "--cd",
                str(spec.workspace_dir or spec.run_dir),
                "--output-last-message",
                str(last_message_path),
                spec.task_text,
            ]
        )
        return command

    async def run(self, spec: AgentLaunchSpec) -> AgentRunResult:
        """Run Codex and persist JSONL events, stderr, last message, and manifest."""
        run_dir = spec.run_dir.resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        events_path = _option_path(spec, "events_path", run_dir / "codex-events.jsonl")
        stderr_path = _option_path(spec, "stderr_path", run_dir / "codex-stderr.log")
        last_message_path = _option_path(
            spec, "last_message_path", run_dir / "codex-last-message.txt"
        )
        normalized_events_path = _option_path(
            spec, "normalized_events_path", run_dir / "normalized-events.jsonl"
        )
        manifest_path = run_dir / "agent-run.json"
        started = _utc_now()
        exit_code: int | None = None
        status: AgentRunStatus = "failed"
        failure_message: str | None = None
        stdout_text = ""
        stderr_text = ""

        try:
            command = self.build_command(spec, last_message_path)
            environment = os.environ.copy()
            environment.update({str(key): str(value) for key, value in spec.environment.items()})
            if spec.model:
                environment.setdefault("EVAL_MODEL", spec.model)
            if spec.reasoning_effort:
                environment.setdefault("EVAL_REASONING_EFFORT", spec.reasoning_effort)
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(spec.workspace_dir or run_dir),
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=spec.timeout_seconds
                )
                stdout_text = stdout.decode("utf-8", errors="replace")
                stderr_text = stderr.decode("utf-8", errors="replace")
                exit_code = process.returncode
                if exit_code == 0:
                    status = "completed"
                else:
                    status = "failed"
                    failure_message = _tail(stderr_text) or f"Codex exited with {exit_code}"
            except TimeoutError:
                status = "timed_out"
                failure_message = f"Codex exceeded timeout of {spec.timeout_seconds} seconds"
                await _terminate_process_tree(process.pid)
                await process.communicate()
        except (FileNotFoundError, OSError) as exc:
            status = "failed"
            failure_message = str(exc)
            stderr_text = str(exc)

        events_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        events_path.write_text(stdout_text, encoding="utf-8")
        stderr_path.write_text(stderr_text, encoding="utf-8")
        normalize_log_file(
            events_path,
            normalized_events_path,
            adapter=self.adapter_id,
            agent_id=spec.agent_id,
        )
        finished = _utc_now()
        result = AgentRunResult(
            agent_id=spec.agent_id,
            task_id=spec.task_id,
            run_dir=run_dir,
            status=status,
            exit_code=exit_code,
            started_at=started,
            finished_at=finished,
            events_path=events_path,
            stderr_path=stderr_path,
            last_message_path=last_message_path,
            manifest_path=manifest_path,
            model=spec.model,
            failure_message=failure_message,
            normalized_events_path=normalized_events_path,
            adapter=self.adapter_id,
            protocol=spec.protocol,
        )
        manifest_path.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return result


def _option_path(spec: AgentLaunchSpec, key: str, default: Path) -> Path:
    value = spec.options.get(key)
    return Path(str(value)).resolve() if value else default


async def _terminate_process_tree(pid: int) -> None:
    """Terminate a timed-out process and its descendants."""
    if os.name == "nt":
        completed = await asyncio.to_thread(
            subprocess.run,
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        if completed.returncode == 0:
            return
    try:
        os.kill(pid, 9)
    except ProcessLookupError:
        pass


def _utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(UTC).isoformat()


def _tail(text: str, limit: int = 2000) -> str:
    """Keep failure diagnostics bounded in the run manifest."""
    return text[-limit:].strip()
