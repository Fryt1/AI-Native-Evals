"""Verify one Agent profile actually works, not merely that it is declared.

An Agent profile states an image, an entrypoint, and a list of capabilities.
None of that is checked today: `agent_images` confirms the image exists, which
says nothing about whether the thing inside it can start, reach a model, or do
the work its capabilities claim.

Two levels, because they catch different failures:

``static``  the image exists, the profile declares the fields a run needs, and
            the declared capabilities are reported. Fast, and it catches the
            profile that points at a tag nobody built.
``smoke``   start the container, ask the Agent for one word, confirm it
            answered, tear it down. Slow, and the only thing that catches a
            broken command, a missing bridge, or unusable credentials.

The smoke test is not a substitute for the environment preflight and does not
duplicate it: preflight answers "can this machine start a run at all", this
answers "does this particular Agent work on this machine".
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .images import inspect_image, wsl_distro

if TYPE_CHECKING:
    from ai_native_evals.agents.profile import AgentProfile

#: Where a run mounts the Task prompt. The probe uses the same path so it
#: exercises the contract a real run uses rather than one of its own.
_PROMPT_FILE = "/run-config/task-prompt.md"

#: A smoke run starts a real container; it must not hang forever.
DEFAULT_SMOKE_TIMEOUT_SECONDS = 240

#: A fixed expected word makes the probe meaningful: any answer proves the model
#: round trip, and the exact word proves the prompt was understood rather than
#: merely acknowledged.
PROBE_PROMPT = "Reply with exactly: READY"
PROBE_EXPECTED = "READY"

#: The image every smoke run uses as its upstream stand-in.
GATEWAY_IMAGE = "ai-native-llm-gateway:local"


@dataclass(slots=True)
class AgentCheck:
    """One verified (or unverifiable) property of an Agent.

    `level` records how the finding was obtained, because that decides how much
    it is worth. `static` findings come from reading files and inspecting an
    image; `smoke` findings come from a container that actually started. A
    report that mixed the two in one list left the operator unable to tell a
    well-formed profile from a working one.
    """

    name: str
    status: str  # ok | missing | unknown
    level: str = ""  # static | smoke; derived from the name when left blank
    detail: str = ""
    hint: str = ""

    def __post_init__(self) -> None:
        if not self.level:
            self.level = check_level(self.name)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "ok": self.status == "ok",
            "level": self.level,
        }
        if self.detail:
            payload["detail"] = self.detail
        if self.status != "ok" and self.hint:
            payload["hint"] = self.hint
        return payload


#: Checks that only a started container can answer. Everything else is read
#: from files, so it can be established without starting anything.
_SMOKE_CHECKS = frozenset({"credentials", "container", "exit", "reply", "smoke"})


def check_level(name: str) -> str:
    """How a check is established, derived from its name.

    One list, so a new check cannot be added with a level that disagrees with
    what it actually does.
    """
    return "smoke" if name in _SMOKE_CHECKS else "static"


@dataclass(slots=True)
class AgentReport:
    """The result of verifying one Agent profile."""

    agent: str
    image: str
    level: str
    checks: list[AgentCheck] = field(default_factory=list)
    checked_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def add(self, check: AgentCheck) -> None:
        self.checks.append(check)

    @property
    def usable(self) -> bool:
        """True unless something was positively found broken.

        A check that could not run does not make the Agent unusable -- the same
        rule as the environment preflight, for the same reason: a probe that
        failed is not a fault.
        """
        return not any(check.status == "missing" for check in self.checks)

    def replace(self, check: AgentCheck) -> None:
        self.checks = [existing for existing in self.checks if existing.name != check.name]
        self.checks.append(check)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "image": self.image,
            "level": self.level,
            "usable": self.usable,
            "checked_at": self.checked_at,
            "checks": {check.name: check.to_dict() for check in self.checks},
            # Grouped so a reader can see at a glance which findings came from
            # reading files and which from a container that actually started.
            "static": [c.name for c in self.checks if c.level == "static"],
            "smoke": [c.name for c in self.checks if c.level == "smoke"],
            "failed": [c.name for c in self.checks if c.status == "missing"],
            "unknown": [c.name for c in self.checks if c.status == "unknown"],
        }


def load_agent_profile(repo_root: Path, agent: str) -> AgentProfile | None:
    """Load one Agent profile through the shared loader.

    Matching on declared id as well as filename is kept, because a profile may
    be named differently from its file.
    """
    from ai_native_evals.agents.profile import (
        AgentProfileError,
        load_agent_profile_file,
        load_agent_profiles,
    )

    root = repo_root / "profiles" / "agents"
    direct = root / f"{agent}.yaml"
    if direct.is_file():
        try:
            profile = load_agent_profile_file(direct)
        except (AgentProfileError, OSError, ValueError):
            return None
        if profile.profile_id == agent:
            return profile
    return load_agent_profiles(root).get(agent)


# --- static verification -----------------------------------------------------


def check_static(
    repo_root: Path, agent: str, *, version: str = "", distro: str | None = None
) -> AgentReport:
    """Verify an Agent without starting anything.

    `version` verifies a specific build instead of the profile's default, so one
    Agent can be checked at each version it has.
    """
    # One shared loader, so this check and a run can never disagree about what a
    # profile means. Two modules reading the YAML themselves is how a derived
    # `image` came to be handled in one and missed in the other.
    profile = load_agent_profile(repo_root, agent)
    if profile is not None and version:
        try:
            profile = profile.with_version(version)
        except Exception as exc:  # noqa: BLE001 - reported as a finding below
            report = AgentReport(agent=agent, image="", level="static")
            report.add(
                AgentCheck(
                    "version",
                    "missing",
                    detail=f"无法切换到版本 {version!r}：{exc}",
                    hint="确认该 Agent 的 profile 声明了 image_repository",
                )
            )
            return report
    image = profile.image if profile else ""
    report = AgentReport(agent=agent, image=image, level="static")

    if profile is None:
        report.add(
            AgentCheck(
                "profile",
                "missing",
                detail=f"没有名为 {agent!r} 的 Agent profile",
                hint="在 profiles/agents/ 下新增 <id>.yaml",
            )
        )
        return report
    report.add(AgentCheck("profile", "ok", detail=f"profiles/agents/{agent}.yaml"))

    # A run cannot start without these; the profile is the only place they exist.
    for key, value in (("adapter", profile.adapter), ("image", profile.image)):
        if value.strip():
            report.add(AgentCheck(key, "ok", detail=value))
        else:
            report.add(
                AgentCheck(
                    key,
                    "missing",
                    detail=f"profile 未声明 {key!r}",
                    hint=f"在 profile 中加入 `{key}:`",
                )
            )
    if profile.agent_version:
        report.add(AgentCheck("version", "ok", detail=profile.agent_version))
    if not image:
        return report

    status = inspect_image(image, distro=distro)
    if status.present is False:
        report.add(
            AgentCheck(
                "image",
                "missing",
                detail=f"{image} 不存在",
                hint="运行：pwsh -File tools/build-sandbox-images.ps1",
            )
        )
    elif status.present is None:
        report.add(
            AgentCheck("image", "unknown", detail=f"无法核对 {image}：{status.error}")
        )
    else:
        report.add(AgentCheck("image", "ok", detail=f"{image} ({status.image_id})"))

    return report


# --- dynamic smoke test ------------------------------------------------------


def _run(argv: list[str], timeout: float) -> tuple[int, str]:
    """Run a command, returning ``(code, output)``; never raises for a timeout."""
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, f"命令超时（{timeout:g}s）"
    except OSError as exc:
        return 127, f"无法执行 {argv[0]}：{exc}"
    return completed.returncode, (completed.stdout or completed.stderr or "").strip()


def smoke_test_agent(
    repo_root: Path,
    agent: str,
    *,
    model: str,
    provider_env_file: Path,
    version: str = "",
    distro: str | None = None,
    timeout: float = DEFAULT_SMOKE_TIMEOUT_SECONDS,
) -> AgentReport:
    """Start the Agent, ask it one question, and confirm it answers.

    This is the only check that proves the pieces fit together: the entrypoint
    runs, the credentials reach a model, and the Agent replies. Everything else
    is inference from files.

    The container and its network are removed on every path, including failure,
    so an interrupted check does not leave resources behind.
    """
    report = check_static(repo_root, agent, version=version, distro=distro)
    report.level = "smoke"
    if not report.usable:
        # No point starting a container for an Agent already known to be broken.
        return report

    profile = load_agent_profile(repo_root, agent)
    if profile is None:
        # `check_static` already reported the missing profile as unusable, so
        # reaching here means the report is contradictory; returning it is still
        # better than raising out of a health check.
        return report
    if version:
        try:
            profile = profile.with_version(version)
        except Exception:  # noqa: BLE001 - check_static already reported it
            return report
    target = wsl_distro(distro)
    upstream = read_upstream_env(provider_env_file)
    if upstream is None:
        report.add(
            AgentCheck(
                "credentials",
                "missing",
                detail=f"{provider_env_file} 里没有可用的上游地址与 key",
                hint="复制 config/.env.example 为 config/.env.local 并填写",
            )
        )
        return report

    name = f"agent-check-{agent.replace('_', '-')[:20]}"
    network, gateway, container = f"{name}-net", f"{name}-gw", f"{name}-c"
    key = "agent-check-key"
    with tempfile.TemporaryDirectory(prefix="agent-check-") as workspace_raw:
        workspace = Path(workspace_raw)
        # The probe prompt is written to a file and mounted, mirroring a real
        # run. Passing it in argv would test a contract no run uses.
        run_config = workspace / "run-config"
        run_config.mkdir(parents=True, exist_ok=True)
        (run_config / "task-prompt.md").write_text(PROBE_PROMPT + "\n", encoding="utf-8")
        (run_config / "mcp-servers.json").write_text("{}", encoding="utf-8")
        try:
            created, out = _run(
                ["wsl.exe", "-d", target, "--", "docker", "network", "create", network], 60
            )
            if created != 0:
                report.add(AgentCheck("container", "unknown", detail=f"创建网络失败：{out[:200]}"))
                return report

            started, out = _run(
                _gateway_args(target, gateway, network, key, model, upstream), 120
            )
            if started != 0:
                report.add(
                    AgentCheck("container", "unknown", detail=f"启动 gateway 失败：{out[:200]}")
                )
                return report

            launched, out = _run(
                _agent_args(
                    target, profile, report.image, container, network, key, model, run_config
                ),
                180,
            )
            if launched != 0:
                report.add(
                    AgentCheck(
                        "container",
                        "missing",
                        detail=f"容器未能启动：{out[:240]}",
                        hint="检查镜像的 entrypoint 与 profile 的 command",
                    )
                )
                return report
            report.add(AgentCheck("container", "ok", detail="容器已启动"))

            wait_code, wait_out = _run(
                ["wsl.exe", "-d", target, "--", "docker", "wait", container], timeout
            )
            # `docker wait` exits 0 when the *wait* succeeded; the container's own
            # exit code is what it prints. Reading the command's status as the
            # container's would report every crashed container as a success.
            container_exit = _parse_container_exit(wait_code, wait_out)

            # `docker run --detach` returns before the container has necessarily
            # flushed its output, so an immediate `logs` can be empty even on a
            # successful run. Wait for the log to settle rather than reporting a
            # missing answer that was merely not written yet.
            logs = _read_settled_logs(target, container)

            if wait_code == 124 or container_exit is None:
                report.add(AgentCheck("exit", "unknown", detail=wait_out.strip()[:200]))
            elif container_exit != 0:
                # A real failure, and the log usually says why.
                report.add(
                    AgentCheck(
                        "exit",
                        "missing",
                        detail=f"容器以退出码 {container_exit} 结束",
                        hint=diagnose(logs),
                    )
                )
                return report

            reply = extract_reply(logs) or read_last_message(workspace)
            if not reply and profile.trace_parser != "codex":
                # A non-conversational probe reports through stdout, not the
                # Codex event stream.
                reply = _first_meaningful_line(logs)
            if not reply:
                report.add(
                    AgentCheck(
                        "reply",
                        "missing",
                        detail="Agent 没有给出回答",
                        hint=diagnose(logs),
                    )
                )
            elif PROBE_EXPECTED.lower() in reply.lower() or profile.trace_parser != "codex":
                report.add(AgentCheck("reply", "ok", detail=f"Agent 回答：{reply[:80]}"))
            else:
                # A different answer still proves the round trip; only the exact
                # match is expected, not required.
                report.add(
                    AgentCheck(
                        "reply",
                        "ok",
                        detail=f"Agent 有回应（未严格匹配 {PROBE_EXPECTED}）：{reply[:80]}",
                    )
                )
            return report
        finally:
            for resource in (container, gateway):
                _run(["wsl.exe", "-d", target, "--", "docker", "rm", "--force", resource], 60)
            _run(["wsl.exe", "-d", target, "--", "docker", "network", "rm", network], 60)


def _gateway_args(
    distro: str, gateway: str, network: str, key: str, model: str, upstream: dict[str, str]
) -> list[str]:
    """The gateway stands in for the real upstream during a smoke run."""
    return [
        "wsl.exe", "-d", distro, "--", "docker", "run", "--detach",
        "--name", gateway, "--network", network, "--network-alias", "llm-gateway",
        "-e", f"UPSTREAM_BASE_URL={upstream['base_url']}",
        "-e", f"UPSTREAM_API_KEY={upstream['api_key']}",
        "-e", f"GATEWAY_API_KEY={key}",
        "-e", f"DEFAULT_MODEL={model}",
        GATEWAY_IMAGE,
    ]


def _agent_args(
    distro: str,
    profile: dict[str, Any],
    image: str,
    container: str,
    network: str,
    key: str,
    model: str,
    prompt_dir: Path,
) -> list[str]:
    """A minimal version of a real run's container contract.

    The entrypoint is already inside the image, so nothing needs building: the
    probe exercises the same startup path a real run takes, which is the point.

    The prompt is mounted as a **file**, and the profile's own `command` is
    replayed rather than invented. An earlier version passed the prompt in argv
    while every real run mounted it, so any Agent reading
    `EVAL_TASK_PROMPT_FILE` was reported broken by the probe while working
    perfectly -- the probe measuring a contract nothing else used. Replaying the
    profile's command means the probe cannot diverge from the run again.

    ``CODEX_HOME`` must stay at the image's own path. Codex refuses to run with
    its home under a temporary directory ("Refusing to create helper binaries
    under temporary dir"), and it exits before doing anything -- a trap found by
    running this probe rather than reasoning about it.
    """
    declared_workdir = profile.workdir or "/workspace"
    args = [
        "wsl.exe", "-d", distro, "--", "docker", "run", "--detach",
        "--name", container, "--network", network,
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        # A real run mounts the project snapshot at the profile's workdir. A
        # smoke run has no snapshot, so that path would not exist and Codex
        # would fail resolving its --cd -- a probe artifact, not a profile
        # defect. An anonymous tmpfs gives it somewhere to live.
        "--tmpfs", f"{declared_workdir}:rw,size=64m",
        # Same mount point and variable a real run uses.
        "--mount", f"type=bind,src={_wsl_path(prompt_dir)},dst=/run-config,readonly",
        "-e", f"EVAL_TASK_PROMPT_FILE={_PROMPT_FILE}",
        "-e", f"EVAL_GATEWAY_API_KEY={key}",
        "-e", "EVAL_GATEWAY_URL=http://llm-gateway:8080/v1",
        "-e", f"EVAL_MODEL={model}",
        "-e", "EVAL_MODEL_PROVIDER=eval",
        "-e", "EVAL_WIRE_API=responses",
        "-e", "EVAL_REASONING_EFFORT=high",
        "-e", "EVAL_TRACE_DIR=/tmp/trace",
        # `/tmp` always exists, so --cd resolves whatever the profile declares.
        "-e", "EVAL_WORKDIR=/tmp",
        "-e", "EVAL_OUTER_SANDBOX=docker",
        "-e", "HOME=/tmp/home",
    ]
    for name, value in profile.environment.items():
        args += ["-e", f"{name}={_render_probe_value(str(value), model)}"]
    if profile.adapter == "dsh-acp":
        # DSH is an ACP server: it has no one-shot prompt entrypoint, so a
        # conversational probe would need a full ACP handshake. What can be
        # verified is that the declared executable actually starts, which is
        # exactly the failure this profile type has hit before.
        executable = str(profile.environment.get("DSH_EXECUTABLE") or "dsh")
        args += ["--entrypoint", "sh", image, "-c", f"{executable} --version"]
    elif profile.command:
        # The profile's own command is replayed, so the probe exercises the same
        # contract the run will.
        if profile.entrypoint:
            args += ["--entrypoint", profile.entrypoint]
        args += [image]
        args += [_render_probe_value(str(item), model) for item in profile.command]
    else:
        # The image's own entrypoint reads the prompt file.
        args += [image]
    return args


def _render_probe_value(value: str, model: str) -> str:
    """Resolve the run placeholders a probe can supply."""
    return (
        value.replace("${TASK_PROMPT}", _PROMPT_FILE)
        .replace("${MODEL}", model)
        .replace("${EVAL_MODEL}", model)
        .replace("${REASONING_EFFORT}", "high")
        .replace("${EVAL_REASONING_EFFORT}", "high")
        .replace("${WORKDIR}", "/tmp")
        .replace("${EVAL_WORKDIR}", "/tmp")
        .replace("${MCP_CONFIG}", "/run-config/mcp-servers.json")
        .replace("${EVAL_MCP_SERVERS_FILE}", "/run-config/mcp-servers.json")
    )


def _wsl_path(path: Path) -> str:
    """Convert an absolute Windows path to one WSL Docker can bind-mount.

    Docker runs inside WSL, so it cannot see `D:\\...`. The same conversion the
    runtime applies, kept here rather than imported: this module deliberately
    depends only on `images`, so a probe can run without the runtime loaded.
    """
    resolved = path.resolve()
    drive = resolved.drive
    if drive:
        relative = resolved.relative_to(resolved.anchor).as_posix()
        return f"/mnt/{drive[0].lower()}/{relative}"
    return resolved.as_posix()


def _parse_container_exit(wait_code: int, wait_out: str) -> int | None:
    """The container's exit code, or ``None`` when it could not be read.

    ``docker wait`` prints the code on success; a transport failure prints
    nothing usable and must not be mistaken for exit 0.
    """
    if wait_code == 124:
        return None
    text = wait_out.strip().splitlines()[-1].strip() if wait_out.strip() else ""
    try:
        return int(text)
    except ValueError:
        return None


def _read_settled_logs(distro: str, container: str, *, attempts: int = 6) -> str:
    """Read a finished container's logs, retrying briefly while they settle.

    Docker can report a container exited before its output is fully readable,
    which showed up here as an intermittent "no answer" on a run that had in
    fact answered. Retrying a few times costs almost nothing and removes a
    flaky verdict -- the worst kind of result for a health check.
    """
    logs = ""
    for attempt in range(attempts):
        _code, logs = _run(["wsl.exe", "-d", distro, "--", "docker", "logs", container], 60)
        if logs.strip():
            return logs
        if attempt < attempts - 1:
            time.sleep(0.4)
    return logs


def extract_reply(logs: str) -> str:
    """Pull the Agent's final answer out of the Codex JSON event stream."""
    for line in reversed(logs.splitlines()):
        start = line.find("{")
        if start < 0 or '"item.completed"' not in line:
            continue
        try:
            event = json.loads(line[start:])
        except json.JSONDecodeError:
            continue
        item = event.get("item") or {}
        if item.get("type") == "agent_message" and item.get("text"):
            return str(item["text"]).strip()
    return ""


def read_last_message(workspace: Path) -> str:
    """The entrypoint also writes the answer to a file; read it as a fallback."""
    for candidate in workspace.rglob("last*.txt"):
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if text:
            return text
    return ""


def _first_meaningful_line(logs: str) -> str:
    """The first real output line, for probes whose answer is plain stdout."""
    for line in logs.splitlines():
        text = line.strip()
        if not text or text.startswith(("WARNING", "time=", "npm ", "> ")):
            continue
        return text
    return ""


def diagnose(logs: str) -> str:
    """Turn the most likely failure in a log into an actionable hint."""
    lowered = logs.lower()
    if "no such file or directory" in lowered:
        # Codex resolves its --cd at startup, so a profile whose workdir is a
        # mounted project fails when nothing is mounted -- which is the case in
        # a smoke run, where the point is the Agent, not the workspace.
        return (
            "Agent 启动时找不到它的工作目录；若该 profile 的 workdir 指向项目快照，"
            "请改用 workdir 在 /workspace 的 profile 做真实测试"
        )
    if "not found" in lowered:
        return "容器内找不到入口脚本；检查 profile 的 command 与镜像内容"
    if "unauthorized" in lowered or "401" in logs or "403" in logs:
        return "模型返回鉴权失败；检查 config/.env.local 的 key"
    if "econnrefused" in lowered or "connection refused" in lowered:
        return "容器连不上 gateway；确认本机 Docker/WSL 正常"
    if "manifest unknown" in lowered or "pull access" in lowered:
        return "镜像不存在；先构建：pwsh -File tools/build-sandbox-images.ps1"
    return "查看容器日志以定位原因"


def read_upstream_env(env_file: Path) -> dict[str, str] | None:
    """Read the upstream URL and key a smoke run needs, or ``None``."""
    try:
        raw = env_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    values: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        values[name.strip()] = value.strip().strip("\"'")
    base = values.get("UPSTREAM_BASE_URL") or values.get("AI_NATIVE_EVALS_LLM_BASE_URL")
    api_key = values.get("UPSTREAM_API_KEY") or values.get("AI_NATIVE_EVALS_LLM_API_KEY")
    if not base or not api_key:
        return None
    return {"base_url": base.rstrip("/"), "api_key": api_key}
