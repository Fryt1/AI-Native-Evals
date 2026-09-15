"""Preflight: is this machine ready to run an evaluation?

``doctor`` answers "is the repository wired correctly?" -- profiles parse, tasks
exist, config loads. This module answers the other half: "can this machine
actually start a run?" Those are different questions with different failure
modes, and conflating them is how a run reaches Docker and dies there.

Every check reports one of three states, and the third is the important one:

``ok``       verified present and usable
``missing``  verified absent -- a hard failure
``unknown``  could not be determined -- never reported as missing

A tool that cannot be checked is not a tool that is absent. Reporting absence
from a failed probe is how a working machine gets told it is broken, so
``unknown`` is a first-class outcome rather than an error.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .runs import docker_cli

OK = "ok"
MISSING = "missing"
UNKNOWN = "unknown"

#: Checks a run cannot start without. A failing one makes the machine unready.
REQUIRED = "required"
#: Checks only some runs need, such as Docker when running inside a sandbox.
OPTIONAL = "optional"


@dataclass(frozen=True, slots=True)
class Check:
    """One environment fact, with the evidence that produced it."""

    name: str
    status: str
    detail: str = ""
    required: bool = True
    hint: str = ""

    @property
    def ok(self) -> bool:
        return self.status == OK

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "ok": self.ok,
        }
        if self.detail:
            payload["detail"] = self.detail
        if not self.ok and self.hint:
            payload["hint"] = self.hint
        return payload


@dataclass(slots=True)
class PreflightReport:
    """The result of checking every prerequisite."""

    checks: list[Check] = field(default_factory=list)

    def add(self, check: Check) -> None:
        self.checks.append(check)

    @property
    def ready(self) -> bool:
        """True when nothing *required* is missing.

        ``unknown`` never blocks, and neither does an optional check: refusing
        to run because a probe failed would make an unreadable environment
        indistinguishable from a broken one. A Console that was never installed
        does not stop an evaluation from running.
        """
        return not any(check.status == MISSING and check.required for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        missing = [c.name for c in self.checks if c.required and c.status == MISSING]
        unknown = [c.name for c in self.checks if c.status == UNKNOWN]
        warnings = [c.name for c in self.checks if not c.required and c.status == MISSING]
        return {
            "ready": self.ready,
            "checks": {check.name: check.to_dict() for check in self.checks},
            "missing_required": missing,
            "warnings": warnings,
            "unknown": unknown,
        }


def _which(name: str, *, required: bool = True, hint: str = "") -> Check:
    """Find an executable, including Windows shim extensions.

    ``shutil.which("pnpm")`` fails on Windows where pnpm installs as
    ``pnpm.CMD``, which would report a working toolchain as missing.
    """
    candidates = [name]
    if os.name == "nt" and not name.lower().endswith((".exe", ".cmd", ".bat")):
        candidates = [f"{name}.exe", f"{name}.cmd", f"{name}.bat", name]
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return Check(name, OK, detail=found, required=required)
    return Check(
        name,
        MISSING,
        detail=f"{name} is not on PATH",
        required=required,
        hint=hint,
    )


def _run(argv: list[str], timeout: float = 30) -> tuple[int, str] | None:
    """Run a probe, returning ``None`` when the answer could not be obtained.

    On Windows a console shim is a batch file, which ``subprocess`` cannot
    execute without a shell; the command is resolved through ``shutil.which``
    first so the same call works for ``node`` and ``pnpm.CMD`` alike.
    """
    resolved = shutil.which(argv[0])
    command = [resolved, *argv[1:]] if resolved else argv
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = (completed.stdout or completed.stderr or "").strip()
    return completed.returncode, output


def check_python() -> Check:
    version = ".".join(str(part) for part in __import__("sys").version_info[:3])
    major, minor = __import__("sys").version_info[:2]
    if (major, minor) >= (3, 11):
        return Check("python", OK, detail=version)
    return Check(
        "python",
        MISSING,
        detail=f"Python {version} is too old",
        hint="install Python 3.11 or newer",
    )


def check_uv() -> Check:
    return _which("uv", hint="install uv from https://docs.astral.sh/uv/")


def check_node() -> Check:
    result = _run(["node", "--version"])
    if result is None:
        return Check(
            "node",
            MISSING,
            detail="node is not on PATH",
            required=False,
            hint="install Node 22 or newer to build the Console",
        )
    _code, version = result
    return Check("node", OK, detail=version.lstrip("v"), required=False)


def check_pnpm() -> Check:
    result = _run(["pnpm", "--version"])
    if result is None:
        return Check(
            "pnpm",
            MISSING,
            detail="pnpm is not on PATH",
            required=False,
            hint="run: corepack enable  (or npm install -g pnpm)",
        )
    _code, version = result
    return Check("pnpm", OK, detail=version, required=False)


def check_console_dependencies(repo_root: Path) -> Check:
    """The Console build needs its node_modules to exist at all."""
    app = repo_root / "apps" / "eval-console"
    if not app.is_dir():
        return Check(
            "console_dependencies",
            UNKNOWN,
            detail="apps/eval-console is absent",
            required=False,
        )
    modules = app / "node_modules"
    if modules.is_dir() and any(modules.iterdir()):
        return Check("console_dependencies", OK, detail=str(modules), required=False)
    return Check(
        "console_dependencies",
        MISSING,
        detail="apps/eval-console/node_modules is missing or empty",
        required=False,
        hint="run: pnpm install",
    )


def check_wsl() -> Check:
    """Whether the platform Docker is reached through is present.

    Windows needs WSL2; Linux and macOS run Docker natively, so there is no
    second thing to check and the answer is not a finding. Reporting it missing
    would mark an ordinary Linux or macOS host unready for a reason that does
    not exist there.
    """
    if not docker_cli.is_windows():
        return Check(
            "wsl",
            OK,
            detail=f"not needed: {docker_cli.describe()}",
            required=False,
        )
    return _which(
        "wsl.exe",
        hint="Docker-backed runs need WSL2; only inline runs work without it",
    )


def check_docker(distro: str | None = None) -> Check:
    """Is Docker reachable, and which server answers?

    Two probes on Windows, one elsewhere, and the difference is deliberate.
    Inside WSL, asking the distro to run ``docker version`` cannot separate "no
    such distro" from "daemon down", and matching Docker's error text is not
    portable: the messages are localized, so a Chinese-locale host answered "no
    such distro" and every English keyword missed. Listing the distros first
    gives an answer that does not depend on prose.

    A native daemon has no distro to get wrong, so the extra probe would only
    add a way to misreport; there, ``docker version`` is the whole question.
    """
    if not docker_cli.is_windows():
        return _check_native_docker()

    if shutil.which("wsl.exe") is None:
        return Check(
            "docker",
            MISSING,
            detail="wsl.exe is not available, so Docker cannot be reached",
            hint="install WSL2 and Docker, or run with an inline sandbox profile",
        )
    target = docker_cli.resolve_distro(distro)

    names = docker_cli.list_distros()
    listed = _run(["wsl.exe", "--list", "--quiet"], timeout=20)
    if listed is not None:
        code, _output = listed
        if code == 0 and names and target not in names:
            return Check(
                "docker",
                MISSING,
                detail=f"no WSL distro named {target!r}; available: {', '.join(names)}",
                hint="set AI_NATIVE_EVALS_WSL_DISTRO to one of the distros above",
            )

    result = _run(
        ["wsl.exe", "-d", target, "--", "docker", "version", "--format", "{{.Server.Version}}"]
    )
    if result is None:
        return Check("docker", UNKNOWN, detail="docker probe could not be run")
    code, output = result
    if code == 0 and output.strip():
        return Check("docker", OK, detail=f"{target}: {output.splitlines()[0].strip()}")
    return Check(
        "docker",
        MISSING,
        detail=f"docker is not usable in {target!r} (exit {code}): {_clean(output)[:160]}",
        hint="start Docker Desktop, or pick a distro with a running daemon",
    )


def _check_native_docker() -> Check:
    """Ask a native daemon for its server version, once."""
    if shutil.which("docker") is None:
        return Check(
            "docker",
            MISSING,
            detail="docker is not on PATH",
            hint="install Docker, or run with an inline sandbox profile",
        )
    result = _run(["docker", "version", "--format", "{{.Server.Version}}"])
    if result is None:
        return Check("docker", UNKNOWN, detail="docker probe could not be run")
    code, output = result
    if code == 0 and output.strip():
        return Check(
            "docker", OK, detail=f"{docker_cli.describe()}: {output.splitlines()[0].strip()}"
        )
    # A non-zero exit here is nearly always "daemon not running" rather than
    # "docker is absent", and the two need different actions.
    return Check(
        "docker",
        MISSING,
        detail=f"docker daemon is not reachable (exit {code}): {_clean(output)[:160]}",
        hint="start the Docker daemon",
    )


def _clean(value: str) -> str:
    """Strip NULs and control characters from WSL output, which is UTF-16LE."""
    readable = value.replace("\x00", "")
    kept = (c for c in readable if c.isprintable() or c == " ")
    return "".join(kept)


def _build_hint() -> str:
    """The build command an operator on this platform should actually run."""
    script = docker_cli.build_hint()
    if docker_cli.is_windows():
        return f"run: pwsh -File {script}"
    return f"run: python {script}"


def check_images(spec: object | None, *, distro: str | None = None) -> Check:
    """The images one specific run would start, once a run has been chosen."""
    if spec is None:
        # Phrased as "nothing chosen yet" rather than a failure: this check is
        # run-specific, and the machine-level answer is `agent_images`.
        return Check(
            "images",
            UNKNOWN,
            detail="未选择 Task，暂无本次运行需要核对的镜像",
            hint="选择 Task 后会核对它需要的全部镜像",
            required=False,
        )
    from .runs.images import image_paths, require_images

    pairs = image_paths(spec)
    if not pairs:
        return Check("images", OK, detail="this run needs no images")
    statuses = require_images(pairs, distro=distro)
    absent = [s for s in statuses.values() if s.present is False]
    unknown = [s for s in statuses.values() if s.present is None]
    if absent:
        return Check(
            "images",
            MISSING,
            detail=", ".join(s.reference for s in absent),
            hint=_build_hint(),
        )
    if unknown:
        return Check(
            "images",
            UNKNOWN,
            detail="could not verify: " + ", ".join(s.reference for s in unknown),
        )
    return Check(
        "images",
        OK,
        detail=", ".join(f"{s.reference} ({s.image_id})" for s in statuses.values()),
    )


def check_agent_images(repo_root: Path, *, distro: str | None = None) -> Check:
    """Every Agent profile's image, whether or not a run has been chosen.

    This belongs to the machine, not to one run: an Agent whose image was never
    built is broken whichever Task you pick, and finding that out should not
    require selecting one first. It is what makes the difference between "you
    have not chosen anything yet" and "this profile cannot start".
    """
    from .runs.images import require_images

    # A missing or unreadable config must not make the check unusable: the
    # default profile location still answers the question, and "cannot read the
    # config" is already its own finding elsewhere.
    try:
        from .runs.resolver import load_config

        config = load_config(repo_root / "config" / "eval.yaml")
    except Exception:  # noqa: BLE001 - fall back to the conventional layout
        config = {}
    agents = _agent_profiles(repo_root, config)

    if not agents:
        return Check(
            "agent_images",
            MISSING,
            detail="no Agent profiles are defined",
            hint="add one under profiles/agents/",
        )

    # Profiles are already resolved objects; nothing here re-reads or
    # re-interprets their fields.
    usable = [(name, profile.image) for name, profile in agents.items() if profile.image]
    if not usable:
        return Check(
            "agent_images",
            MISSING,
            detail="no Agent profile resolves to an image",
            hint="add `image:` or `image_repository:` + `agent_version:` to the profile",
        )

    statuses = require_images(usable, distro=distro)
    ready: list[str] = []
    absent: list[str] = []
    unknown: list[str] = []
    for name, image in usable:
        status = statuses.get(image)
        if status is None or status.present is None:
            unknown.append(f"{name} → {image}")
        elif status.present:
            ready.append(name)
        else:
            absent.append(f"{name} → {image}")

    if absent:
        return Check(
            "agent_images",
            MISSING,
            detail="; ".join(absent),
            hint=_build_hint(),
        )
    if unknown:
        return Check(
            "agent_images",
            UNKNOWN,
            detail=f"could not verify: {'; '.join(unknown)}",
        )
    return Check(
        "agent_images",
        OK,
        detail=f"{len(ready)} 个 Agent 可用（{', '.join(sorted(ready))}）",
    )


def _agent_profiles(repo_root: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Load the repository's Agent profiles through the shared loader.

    This used to parse the YAML itself. Two modules doing that meant a field
    whose meaning changed -- an `image` derived from a version -- was fixed in
    one and silently broken in the other.
    """
    from .agents.profile import load_agent_profiles

    root = repo_root / "profiles" / "agents"
    profile_roots = config.get("profile_roots")
    if isinstance(profile_roots, dict) and profile_roots.get("agents"):
        root = repo_root / str(profile_roots["agents"])
    return load_agent_profiles(root)


def check_provider_credentials(repo_root: Path) -> Check:
    """A run needs an upstream to talk to, and the env file holds it."""
    from .providers import load_provider_profiles, provider_env_file

    config_path = repo_root / "config" / "eval.yaml"
    try:
        from .runs.resolver import load_config

        config = load_config(config_path)
        profiles = load_provider_profiles(repo_root, config)
    except Exception as exc:  # noqa: BLE001 - any failure here is "cannot check"
        return Check("provider_credentials", UNKNOWN, detail=f"could not read providers: {exc}")

    configured: list[str] = []
    for provider_id, profile in profiles.items():
        env_path = provider_env_file(repo_root, profile)
        if env_path and env_path.is_file():
            configured.append(provider_id)
    if configured:
        return Check(
            "provider_credentials", OK, detail="configured: " + ", ".join(sorted(configured))
        )
    return Check(
        "provider_credentials",
        MISSING,
        detail="no provider has an env file",
        hint="copy config/.env.example to config/.env.local and fill in the key",
    )


def check_mcp_hosts(spec: object | None, *, timeout: float = 3.0) -> Check:
    """Can the host services a run declares actually be reached?

    Only host-reachable HTTP servers are probed; a stdio server runs inside the
    Agent container and has nothing to answer from here. A closed port is not
    fatal -- the host application may simply not be started yet -- so this is
    reported as a warning rather than a missing requirement.
    """
    if spec is None:
        return Check(
            "mcp_hosts",
            UNKNOWN,
            detail="未选择 Task，暂无可核对的宿主服务",
            hint="选择 Task 后会探测它声明的 MCP 宿主",
            required=False,
        )
    servers = getattr(spec, "mcp_servers", None)
    if not isinstance(servers, dict) or not servers:
        return Check("mcp_hosts", OK, detail="this run declares no MCP servers", required=False)

    import socket
    from urllib.parse import urlsplit

    reachable: list[str] = []
    unreachable: list[str] = []
    skipped: list[str] = []
    for name, descriptor in servers.items():
        if not isinstance(descriptor, dict):
            continue
        url = descriptor.get("url")
        if not url:
            skipped.append(f"{name}(stdio)")
            continue
        parts = urlsplit(str(url))
        host, port = parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)
        if not host:
            skipped.append(f"{name}(unparsed)")
            continue
        try:
            with socket.create_connection((host, port), timeout=timeout):
                reachable.append(f"{name}({host}:{port})")
        except OSError:
            unreachable.append(f"{name}({host}:{port})")

    if unreachable and not reachable:
        return Check(
            "mcp_hosts",
            UNKNOWN,
            detail=(
                "not reachable: "
                + ", ".join(unreachable)
                + " (start the host application first)"
            ),
            required=False,
        )
    detail = ", ".join(reachable + [f"unreachable: {u}" for u in unreachable] + skipped)
    return Check("mcp_hosts", OK, detail=detail, required=False)


def check_runs_root(repo_root: Path) -> Check:
    """Where runs are written must be creatable and writable."""
    from .runs.resolver import load_config, resolve_runs_root

    try:
        config = load_config(repo_root / "config" / "eval.yaml")
        runs_root = resolve_runs_root(repo_root, config)
    except Exception:  # noqa: BLE001 - fall back to the documented default
        runs_root = repo_root.parent / "EvalRuns"
    try:
        runs_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return Check(
            "runs_root",
            MISSING,
            detail=f"cannot create {runs_root}: {exc}",
            hint="set paths.runs_root in config/eval.yaml to a writable directory",
        )
    probe = runs_root / ".preflight-write-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Check("runs_root", MISSING, detail=f"{runs_root} is not writable: {exc}")
    return Check("runs_root", OK, detail=str(runs_root))


def run_preflight(
    repo_root: Path,
    *,
    spec: object | None = None,
    distro: str | None = None,
    include_optional: bool = True,
) -> PreflightReport:
    """Check everything a run needs, in the order a failure would be hit."""
    report = PreflightReport()
    # Machine facts first: these hold whatever Task you go on to choose.
    report.add(check_python())
    report.add(check_uv())
    report.add(check_runs_root(repo_root))
    report.add(check_provider_credentials(repo_root))
    report.add(check_wsl())
    report.add(check_docker(distro))
    # An Agent whose image is missing is broken for every Task, so this is a
    # machine fact too, not a property of one run.
    report.add(check_agent_images(repo_root, distro=distro))
    # Run-specific facts last: these need a resolved run to say anything.
    report.add(check_images(spec, distro=distro))
    if include_optional:
        report.add(check_node())
        report.add(check_pnpm())
        report.add(check_console_dependencies(repo_root))
        report.add(check_mcp_hosts(spec))
    return report


def render_text(report: PreflightReport) -> str:
    """A compact human-readable rendering; JSON stays the machine contract."""
    width = max((len(check.name) for check in report.checks), default=0)
    lines: list[str] = []
    for check in report.checks:
        marker = {OK: "ok     ", MISSING: "MISSING", UNKNOWN: "unknown"}[check.status]
        if check.status == MISSING and not check.required:
            marker = "warn   "
        suffix = "" if check.required else "  (optional)"
        lines.append(f"  {check.name.ljust(width)}  {marker}  {check.detail}{suffix}")
        if check.status == MISSING and check.hint:
            lines.append(f"  {' ' * width}           -> {check.hint}")
    blocked = [c.name for c in report.checks if c.required and c.status == MISSING]
    lines.append("")
    if report.ready:
        lines.append("  ready: yes")
    else:
        lines.append("  ready: NO  (missing: " + ", ".join(blocked) + ")")
    return "\n".join(lines)


def to_json(report: PreflightReport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)


#: Kept for callers that build a preflight from an already-resolved run.
CheckFactory = Callable[[], Check]
