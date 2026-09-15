"""The one place that knows which operating system Docker is reached through.

Every run of this suite happens inside Docker, but *how* a host reaches that
Docker is not the same everywhere:

* **Windows** runs Docker Desktop inside a WSL2 distro, so a command is
  ``wsl.exe -d <distro> -- docker ...`` and a host path must be translated to
  ``/mnt/<drive>/...`` before Docker can bind-mount it.
* **Linux and macOS** run Docker natively, so a command *is* ``docker ...`` and
  a host path is already the path Docker sees.

That difference used to be spelled out at every call site -- twenty of them
building ``wsl.exe`` argument lists by hand -- which meant the suite could only
ever run on the machine it was written on, even though CI runs on Linux. This
module is the single seam: the rest of the package asks for an argv, a path, or
a gateway address and never learns which platform answered.

Nothing here is Windows-only, and nothing here loses the Windows behaviour:
on Windows the emitted argv is exactly what the call sites used to build.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

#: The WSL distro used when neither a profile nor the environment names one.
#: Only consulted on Windows; ignored everywhere else.
DEFAULT_WSL_DISTRO = "Ubuntu-20.04"

#: Environment variable that overrides the distro, as the runtime always allowed.
DISTRO_ENV = "AI_NATIVE_EVALS_WSL_DISTRO"


def is_windows() -> bool:
    """Whether Docker is reached through WSL rather than a native daemon."""
    return sys.platform == "win32"


def host_platform() -> str:
    """``windows``, ``macos`` or ``linux`` -- for messages, never for logic."""
    if is_windows():
        return "windows"
    return "macos" if sys.platform == "darwin" else "linux"


def default_distro() -> str:
    """The WSL distro to use, honouring the environment override.

    Meaningless off Windows, where no distro is involved; callers that store it
    in a manifest still get a stable value so a run record stays comparable.
    """
    return (os.environ.get(DISTRO_ENV) or DEFAULT_WSL_DISTRO).strip() or DEFAULT_WSL_DISTRO


def resolve_distro(explicit: str | None = None) -> str:
    """A distro name for callers that record or display one.

    Off Windows there is no distro, so the answer is purely informational and
    must not be used to build a command -- :func:`docker_argv` is what builds
    commands, and it does not need a distro at all.
    """
    if explicit and explicit.strip():
        return explicit.strip()
    return default_distro()


def docker_argv(*args: str, distro: str | None = None) -> list[str]:
    """The argv that runs ``docker`` with ``args`` on this platform.

    This is the seam. A caller never builds ``wsl.exe`` itself: doing so is what
    pinned the suite to Windows. ``distro`` is accepted so an existing call site
    keeps its signature, and is used only on Windows.
    """
    if is_windows():
        return ["wsl.exe", "-d", resolve_distro(distro), "--", "docker", *args]
    return ["docker", *args]


def windows_mount_path(path: Path) -> str:
    """Map a Windows path to the path WSL Docker sees for it (``D:\\x`` -> ``/mnt/d/x``).

    Split out from :func:`host_path` as a pure function of an already-resolved
    path, because it can then be tested on *every* host. Left inline, the drive
    translation was only ever executed on Windows -- so Linux CI, which is where
    this suite actually runs, could not have caught a regression in it.

    Only drive-letter paths have an answer. A UNC path (``\\\\server\\share\\dir``)
    reports its whole share as the drive, and taking the first character of that
    produced ``/mnt/\\/dir`` -- a mount source that does not exist, silently, so
    the container started with nothing mounted. WSL agrees there is no answer:
    only drive letters are mounted under ``/mnt``, and ``wslpath`` refuses a UNC
    path outright. Raising here turns that into an operator-visible error.

    Output matches ``wslpath -a``, including the trailing separator on a drive
    root, which is verified in ``tests/test_docker_cli.py``.
    """
    drive = path.drive
    if drive:
        if len(drive) != 2 or not drive.endswith(":"):
            raise ValueError(
                f"cannot mount {path}: only drive-letter paths have a WSL mount, "
                "and this is a UNC path; copy the data to a local drive first"
            )
        # `relative_to` on a drive root yields ".", which would produce
        # `/mnt/d/.`. The trailing separator is kept instead, matching
        # `wslpath -a "D:\"`, which answers `/mnt/d/`.
        relative = path.relative_to(path.anchor).as_posix()
        if relative in ("", "."):
            return f"/mnt/{drive[0].lower()}/"
        return f"/mnt/{drive[0].lower()}/{relative}"
    return path.as_posix()


def host_path(path: Path | str) -> str:
    """Translate a host path into one Docker can bind-mount.

    Windows has to cross the WSL boundary (``D:\\x`` -> ``/mnt/d/x``). Linux and
    macOS already share the mount namespace with their daemon, so the path is
    returned unchanged -- rewriting it there would invent a directory that does
    not exist and break every mount.
    """
    resolved = Path(path).resolve()
    if not is_windows():
        return resolved.as_posix()
    return windows_mount_path(resolved)


def host_gateway(distro: str | None = None) -> str:
    """The host address a container should use to reach services on the host.

    On Windows the WSL default gateway is the host, and older Docker Desktop
    builds do not implement ``host-gateway``, so it is resolved by asking the
    distro for its route. On Linux and macOS ``host-gateway`` is understood by
    Docker itself and is the correct answer -- probing for a route there would
    return the container bridge, which is not the host.
    """
    if not is_windows():
        return "host-gateway"
    result = _run_probe(
        [
            "wsl.exe",
            "-d",
            resolve_distro(distro),
            "--",
            "sh",
            "-lc",
            "ip route | sed -n 's/^default via \\([^ ]*\\).*/\\1/p'",
        ]
    )
    if result is not None:
        output = (result.stdout or "").strip()
        candidate = output.splitlines()[0].strip() if output else ""
        if candidate:
            return candidate
    # Docker Desktop configurations that do understand the keyword still work.
    return "host-gateway"


def list_distros() -> list[str]:
    """The WSL distros on this machine, or ``[]`` off Windows.

    ``wsl.exe`` writes UTF-16LE, so its output arrives with interleaved NUL
    bytes; they are stripped rather than decoded, because the byte order has
    varied across Windows builds.
    """
    if not is_windows():
        return []
    result = _run_probe(["wsl.exe", "--list", "--quiet"])
    if result is None or result.returncode != 0:
        return []
    raw = (result.stdout or "").replace("\x00", "")
    return [name.strip() for name in raw.splitlines() if name.strip()]


def shared_temp_root() -> Path:
    """A directory the Docker *daemon* can bind-mount, not just this process.

    On Windows the daemon lives inside WSL, so the system temporary directory is
    reached by crossing the boundary and is still a valid host path. On Linux
    and macOS the daemon shares the filesystem, but ``/tmp`` is often a private
    per-process namespace (systemd ``PrivateTmp``) or a symlink to ``/private``,
    and a path from there can resolve differently inside the container. A
    directory under the user's cache is stable on every platform, so a probe
    that mounts a temporary file works for the same reason a real run does.
    """
    if is_windows():
        return Path(tempfile.gettempdir())
    override = os.environ.get("XDG_CACHE_HOME")
    base = Path(override) if override else Path.home() / ".cache"
    return base / "ai-native-evals" / "tmp"


def build_hint(*, powershell: bool | None = None) -> str:
    """The command that builds the sandbox images on this platform.

    Used in operator-facing messages, so a Linux user is not told to run a
    ``.ps1`` that needs PowerShell, and a Windows user keeps the entry point
    that already worked.
    """
    if powershell is None:
        powershell = is_windows()
    script = "build-sandbox-images.ps1" if powershell else "build-sandbox-images.py"
    return f"tools/{script}"


def build_command(*, powershell: bool | None = None) -> str:
    """The runnable form of :func:`build_hint`, for a message to copy.

    One function so the hint a user reads and the command they can paste cannot
    disagree, and so no module has to re-derive which interpreter to name.
    """
    script = build_hint(powershell=powershell)
    resolved = is_windows() if powershell is None else powershell
    return f"pwsh -File {script}" if resolved else f"python {script}"


def _run_probe(argv: list[str], timeout: float = 20) -> subprocess.CompletedProcess[str] | None:
    """Run a platform probe, returning ``None`` when it cannot be run at all."""
    try:
        return subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def describe() -> str:
    """A one-line description of how Docker is reached, for `doctor` output.

    Derived from :func:`host_platform`, which reads ``sys.platform`` -- the same
    single source every other function here branches on. An earlier version read
    ``platform.system()`` instead, so a test that forced ``sys.platform`` to
    check the macOS path got a string still saying "linux": two sources of truth
    for one fact, and the display disagreed with the behaviour it described.
    """
    if is_windows():
        return f"WSL2 ({resolve_distro()})"
    return f"native Docker on {host_platform()}"
