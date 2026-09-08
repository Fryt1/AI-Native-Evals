"""Standard ACP adapter for the real DeepSeek Harness (DSH) runtime."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..contracts import AgentLaunchSpec, AgentRunResult, AgentRunStatus
from ..mcp import project_dsh_mcp_servers
from .events import normalize_log_file

if TYPE_CHECKING:
    from ..agents.profile import AgentProfile


@dataclass(frozen=True, slots=True)
class DshConfig:
    """Process and ACP policy for one DSH Agent profile."""

    executable: str = "dsh"
    profile: str = "acp"
    permission_option_id: str = "allow-once"
    close_after_prompt: bool = True
    command: tuple[str, ...] = ()

    @classmethod
    def from_profile(cls, profile: AgentProfile) -> DshConfig:
        options = profile.options
        return cls(
            executable=str(options.get("executable", "dsh")),
            profile=str(options.get("profile", "acp")),
            permission_option_id=str(options.get("permission_option_id", "allow-once")),
            close_after_prompt=bool(options.get("close_after_prompt", True)),
            command=tuple(str(item) for item in options.get("command", ()) or ()),
        )


class DshAcpError(RuntimeError):
    """Raised when the DSH ACP process violates the expected wire contract."""


class DshAcpAdapter:
    """Drive the real DSH ``--profile acp`` process over standard ACP JSON-RPC."""

    adapter_id = "dsh-acp"

    def __init__(self, config: DshConfig | None = None) -> None:
        self.config = config or DshConfig()

    def _resolve_executable(self) -> str:
        executable = shutil.which(self.config.executable)
        if executable is None:
            raise FileNotFoundError(
                f"DSH executable was not found on PATH: {self.config.executable}"
            )
        return executable

    def build_command(self, _spec: AgentLaunchSpec) -> list[str]:
        """Build the configured DSH ACP command."""
        if self.config.command:
            return list(self.config.command)
        return [self._resolve_executable(), "--profile", self.config.profile]

    async def run(self, spec: AgentLaunchSpec) -> AgentRunResult:
        """Run one ACP session and persist protocol plus normalized traces."""
        run_dir = spec.run_dir.resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        events_path = _option_path(spec, "events_path", run_dir / "dsh-acp-events.jsonl")
        stderr_path = _option_path(spec, "stderr_path", run_dir / "dsh-stderr.log")
        last_message_path = _option_path(
            spec, "last_message_path", run_dir / "dsh-last-message.txt"
        )
        normalized_events_path = _option_path(
            spec, "normalized_events_path", run_dir / "normalized-events.jsonl"
        )
        manifest_path = run_dir / "agent-run.json"
        started = _utc_now()
        status: AgentRunStatus = "failed"
        exit_code: int | None = None
        failure_message: str | None = None
        raw_messages: list[dict[str, Any]] = []
        stdout_noise: list[str] = []
        stderr_text = ""
        last_message_parts: list[str] = []
        process: asyncio.subprocess.Process | None = None
        stderr_task: asyncio.Task[bytes] | None = None

        try:
            command = self.build_command(spec)
            environment = os.environ.copy()
            environment.update({str(key): str(value) for key, value in spec.environment.items()})
            if spec.model:
                environment.setdefault("DSH_MODEL", spec.model)
            if spec.reasoning_effort:
                environment.setdefault("DSH_REASONING_EFFORT", spec.reasoning_effort)
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(spec.workspace_dir or spec.run_dir),
                env=environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            if process.stdin is None or process.stdout is None or process.stderr is None:
                raise DshAcpError("DSH ACP process did not expose stdio streams")
            stderr_task = asyncio.create_task(process.stderr.read())
            result = await asyncio.wait_for(
                self._run_session(
                    process,
                    spec,
                    raw_messages,
                    stdout_noise,
                    last_message_parts,
                ),
                timeout=spec.timeout_seconds,
            )
            status = "completed"
            exit_code = result.get("exit_code")
            if result.get("stop_reason") in {"cancelled", "error"}:
                status = "cancelled" if result.get("stop_reason") == "cancelled" else "failed"
                failure_message = f"DSH ACP stop reason: {result['stop_reason']}"
            if self.config.close_after_prompt:
                await _close_process(process)
            stderr_text = (await stderr_task).decode("utf-8", errors="replace")
        except TimeoutError:
            status = "timed_out"
            failure_message = f"DSH ACP exceeded timeout of {spec.timeout_seconds} seconds"
            if process is not None:
                await _terminate_process(process)
        except (FileNotFoundError, OSError, DshAcpError, ValueError) as exc:
            status = "failed"
            failure_message = str(exc)
            if process is not None:
                await _terminate_process(process)
        finally:
            if process is not None and process.returncode is None:
                await _terminate_process(process)
            if process is not None:
                exit_code = process.returncode if exit_code is None else exit_code
            if stderr_task is not None and not stderr_text:
                stderr_text = (await stderr_task).decode("utf-8", errors="replace")

        if stdout_noise:
            stderr_text = (stderr_text + "\n" + "\n".join(stdout_noise)).strip()
        events_path.parent.mkdir(parents=True, exist_ok=True)
        raw_messages.append(
            {
                "type": "agent.lifecycle",
                "event": "run_completed" if status == "completed" else "error",
                "stop_reason": failure_message,
            }
        )
        events_path.write_text(
            "".join(
                json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
                for message in raw_messages
            ),
            encoding="utf-8",
        )
        stderr_path.write_text(stderr_text, encoding="utf-8")
        message_text = "".join(last_message_parts).strip()
        last_message_path.write_text(
            message_text + ("\n" if message_text else ""), encoding="utf-8"
        )
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
            protocol="acp",
        )
        manifest_path.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return result

    async def _run_session(
        self,
        process: asyncio.subprocess.Process,
        spec: AgentLaunchSpec,
        raw_messages: list[dict[str, Any]],
        stdout_noise: list[str],
        last_message_parts: list[str],
    ) -> dict[str, Any]:
        next_id = 1

        async def request(method: str, params: dict[str, Any]) -> dict[str, Any]:
            nonlocal next_id
            request_id = next_id
            next_id += 1
            await _write_jsonrpc(
                process,
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
                raw_messages,
            )
            while True:
                message = await _read_jsonrpc(process, raw_messages, stdout_noise)
                if message.get("method") == "session/request_permission":
                    await self._answer_permission(process, message, raw_messages)
                    continue
                if message.get("method") == "session/update":
                    _collect_message_text(message, last_message_parts)
                    continue
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    error = message.get("error")
                    raise DshAcpError(f"DSH ACP {method} failed: {error}")
                result = message.get("result", {})
                return result if isinstance(result, dict) else {}

        await request(
            "initialize",
            {"protocolVersion": 1, "clientCapabilities": {}},
        )
        await request("authenticate", {"methodId": "evaluation"})
        created = await request(
            "session/new",
            {
                "cwd": spec.working_directory,
                "mcpServers": project_dsh_mcp_servers(dict(spec.mcp_servers)),
            },
        )
        session_id = created.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise DshAcpError("DSH ACP session/new returned no sessionId")
        prompted = await request(
            "session/prompt",
            {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": spec.task_text}],
            },
        )
        stop_reason = prompted.get("stopReason")
        if self.config.close_after_prompt:
            try:
                await request("session/close", {"sessionId": session_id})
            except DshAcpError:
                # A server that already tore down the session is still a valid
                # completed ACP turn; the prompt result remains authoritative.
                pass
        return {"stop_reason": stop_reason, "exit_code": 0}

    async def _answer_permission(
        self,
        process: asyncio.subprocess.Process,
        message: dict[str, Any],
        raw_messages: list[dict[str, Any]],
    ) -> None:
        request_id = message.get("id")
        params = message.get("params")
        options = params.get("options", []) if isinstance(params, dict) else []
        option_id = self.config.permission_option_id
        if isinstance(options, list):
            known = {
                item.get("optionId")
                for item in options
                if isinstance(item, dict) and isinstance(item.get("optionId"), str)
            }
            if option_id not in known:
                option_id = next(iter(known), option_id)
        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"outcome": {"outcome": "selected", "optionId": option_id}},
        }
        await _write_jsonrpc(process, response, raw_messages)


async def _write_jsonrpc(
    process: asyncio.subprocess.Process,
    message: dict[str, Any],
    raw_messages: list[dict[str, Any]],
) -> None:
    if process.stdin is None:
        raise DshAcpError("DSH ACP stdin is closed")
    raw_messages.append({"type": "acp.message", "direction": "out", "message": message})
    process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
    await process.stdin.drain()


async def _read_jsonrpc(
    process: asyncio.subprocess.Process,
    raw_messages: list[dict[str, Any]],
    stdout_noise: list[str],
) -> dict[str, Any]:
    if process.stdout is None:
        raise DshAcpError("DSH ACP stdout is closed")
    while True:
        line = await process.stdout.readline()
        if not line:
            raise DshAcpError("DSH ACP exited before completing the request")
        text = line.decode("utf-8", errors="replace").strip()
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            stdout_noise.append(text)
            continue
        if not isinstance(value, dict):
            continue
        raw_messages.append({"type": "acp.message", "direction": "in", "message": value})
        return value


def _collect_message_text(message: dict[str, Any], output: list[str]) -> None:
    params = message.get("params")
    if not isinstance(params, dict):
        return
    update = params.get("update")
    update = update if isinstance(update, dict) else params
    if update.get("sessionUpdate") != "agent_message_chunk":
        return
    content = update.get("content")
    if isinstance(content, dict) and isinstance(content.get("text"), str):
        output.append(content["text"])


async def _close_process(process: asyncio.subprocess.Process) -> None:
    if process.stdin is not None and not process.stdin.is_closing():
        process.stdin.close()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        await _terminate_process(process)


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.kill()
    try:
        await process.wait()
    except OSError:
        pass


def _option_path(spec: AgentLaunchSpec, key: str, default: Path) -> Path:
    value = spec.options.get(key)
    return Path(str(value)).resolve() if value else default


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
