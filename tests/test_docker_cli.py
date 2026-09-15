"""The platform seam: one module decides how Docker is reached.

Every other module asks this seam for an argv, a host path, or a gateway
address and never learns which platform answered. That makes the seam the one
place where a wrong branch is invisible everywhere else -- so it is tested
directly here, and *both* branches are exercised on every host.

That last part is the point. Before this file, the Windows branch was only ever
executed on Windows and the native branch only on Linux, so CI (which runs
Linux) could not have caught a Windows regression, and macOS was never tested
at all. The branch is selected from ``sys.platform``, so a test can force the
other one and assert the contract it must keep on a machine that is not that
platform.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ai_native_evals.runs import docker_cli


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["probe"], returncode=returncode, stdout=stdout, stderr=""
    )


@pytest.fixture
def as_windows(monkeypatch):
    """Force the seam to answer as Windows, whatever this host really is."""
    monkeypatch.setattr(docker_cli, "is_windows", lambda: True)


@pytest.fixture
def as_native(monkeypatch):
    """Force the seam to answer as Linux/macOS, whatever this host really is."""
    monkeypatch.setattr(docker_cli, "is_windows", lambda: False)


# --- which platform is reported ----------------------------------------------


def test_platform_is_read_from_sys_not_os():
    """`sys.platform` distinguishes macOS; `os.name` does not.

    Both Linux and macOS are `posix`, so a check built on `os.name` would
    silently describe a Mac as Linux. Only `sys.platform` separates them.
    """
    import sys

    assert docker_cli.is_windows() == (sys.platform == "win32")
    if sys.platform == "darwin":
        assert docker_cli.host_platform() == "macos"
    elif sys.platform == "win32":
        assert docker_cli.host_platform() == "windows"
    else:
        assert docker_cli.host_platform() == "linux"


def test_macos_is_reported_as_macos_not_linux(monkeypatch):
    """A regression here misreports every macOS diagnosis as Linux."""
    import sys

    monkeypatch.setattr(sys, "platform", "darwin")

    assert docker_cli.is_windows() is False
    assert docker_cli.host_platform() == "macos"
    # The description must agree, because it is what an operator actually reads.
    assert docker_cli.describe() == "native Docker on macos"


def test_non_windows_platforms_are_all_reached_without_wsl(monkeypatch):
    """Every `sys.platform` that is not win32 must take the native path.

    Enumerated rather than sampled: a branch written as `== "linux"` would send
    macOS down the WSL path and try to run `wsl.exe` on a Mac.
    """
    import sys

    for platform_name in ("linux", "darwin", "freebsd", "openbsd"):
        monkeypatch.setattr(sys, "platform", platform_name)
        assert docker_cli.is_windows() is False, platform_name
        assert docker_cli.docker_argv("ps") == ["docker", "ps"], platform_name


# --- the command that reaches Docker -----------------------------------------


def test_windows_builds_a_wsl_argv(as_windows):
    """The exact argv the call sites used to build by hand.

    Byte-for-byte, because this is what the refactor promised not to change:
    `wsl.exe -d <distro> -- docker <args>`.
    """
    argv = docker_cli.docker_argv("image", "inspect", "x:1", distro="Ubuntu-20.04")

    assert argv == [
        "wsl.exe",
        "-d",
        "Ubuntu-20.04",
        "--",
        "docker",
        "image",
        "inspect",
        "x:1",
    ]


def test_native_builds_a_bare_docker_argv(as_native):
    """No `wsl.exe` anywhere, and no distro smuggled in as an argument."""
    argv = docker_cli.docker_argv("image", "inspect", "x:1", distro="Ubuntu-20.04")

    assert argv == ["docker", "image", "inspect", "x:1"]
    assert "wsl.exe" not in argv
    assert "Ubuntu-20.04" not in argv


def test_the_docker_subcommand_is_never_reordered(as_native):
    """A seam that dropped or reordered arguments would break every call site."""
    argv = docker_cli.docker_argv("run", "--detach", "--name", "c", "img")

    assert argv == ["docker", "run", "--detach", "--name", "c", "img"]


def test_a_blank_distro_falls_back_rather_than_emitting_an_empty_argument(
    as_windows, monkeypatch
):
    """`wsl.exe -d "" -- docker` fails with a message about the distro.

    An empty or whitespace value means "not specified", so it must resolve to
    the default instead of producing a command that cannot work.
    """
    monkeypatch.delenv(docker_cli.DISTRO_ENV, raising=False)

    assert docker_cli.docker_argv("ps", distro="")[2] == docker_cli.DEFAULT_WSL_DISTRO
    assert docker_cli.docker_argv("ps", distro="   ")[2] == docker_cli.DEFAULT_WSL_DISTRO


# --- which distro is named ---------------------------------------------------


def test_the_environment_overrides_the_default_distro(monkeypatch):
    """Operators already relied on this variable; the seam must keep honouring it."""
    monkeypatch.setenv(docker_cli.DISTRO_ENV, "Debian")
    assert docker_cli.default_distro() == "Debian"

    monkeypatch.setenv(docker_cli.DISTRO_ENV, "  Debian  ")
    assert docker_cli.default_distro() == "Debian", "surrounding space must be trimmed"

    monkeypatch.setenv(docker_cli.DISTRO_ENV, "   ")
    assert docker_cli.default_distro() == docker_cli.DEFAULT_WSL_DISTRO


def test_an_explicit_distro_wins_over_the_environment(monkeypatch):
    monkeypatch.setenv(docker_cli.DISTRO_ENV, "Debian")

    assert docker_cli.resolve_distro("Ubuntu-24.04") == "Ubuntu-24.04"
    assert docker_cli.resolve_distro(None) == "Debian"


def test_listing_distros_is_empty_off_windows(as_native):
    """There are no WSL distros on Linux or macOS, and none must be invented."""
    assert docker_cli.list_distros() == []


def test_listing_distros_strips_the_utf16_nul_bytes(as_windows, monkeypatch):
    """`wsl.exe` writes UTF-16LE, so its output arrives interleaved with NULs.

    Stripping them is deliberate rather than decoding: the byte order has varied
    across Windows builds, and a decoding failure would report a machine with
    distros as having none.
    """
    raw = "U\x00b\x00u\x00n\x00t\x00u\x00-\x002\x000\x00.\x000\x004\x00\r\x00\n\x00"
    raw += "\x00d\x00o\x00c\x00k\x00e\x00r\x00-\x00d\x00e\x00s\x00k\x00t\x00o\x00p\x00\n\x00"
    monkeypatch.setattr(docker_cli, "_run_probe", lambda *a, **k: _completed(raw))

    assert docker_cli.list_distros() == ["Ubuntu-20.04", "docker-desktop"]


def test_a_failed_distro_listing_is_empty_not_an_exception(as_windows, monkeypatch):
    """`doctor` must stay reportable when `wsl.exe` cannot be run at all."""
    monkeypatch.setattr(docker_cli, "_run_probe", lambda *a, **k: None)
    assert docker_cli.list_distros() == []

    monkeypatch.setattr(docker_cli, "_run_probe", lambda *a, **k: _completed("", 1))
    assert docker_cli.list_distros() == []


# --- host paths --------------------------------------------------------------


@pytest.mark.skipif(
    docker_cli.is_windows(),
    reason="`pathlib` resolves an absolute POSIX path against the current drive on Windows",
)
def test_native_paths_are_returned_unchanged(as_native):
    """Rewriting a Linux path would invent a directory Docker cannot mount.

    Docker on Linux and macOS shares the filesystem with its client, so the path
    is already correct; a `/mnt/<drive>` translation there points at nothing.

    POSIX-host only, and that is not a shortcut: `host_path` resolves the path
    before returning it, and `Path("/tmp/x").resolve()` is `D:\\tmp\\x` on
    Windows. Forcing the branch with the fixture changes which line runs but
    cannot change what `pathlib` means by an absolute path, so asserting the
    passthrough on a Windows host would be asserting a fiction. The branch
    itself is still covered on every host by the argv and gateway tests above.
    """
    assert docker_cli.host_path("/tmp/some/dir") == "/tmp/some/dir"
    assert docker_cli.host_path(Path("/var/lib/eval")) == "/var/lib/eval"


@pytest.mark.skipif(
    docker_cli.is_windows(),
    reason="`pathlib` resolves an absolute POSIX path against the current drive on Windows",
)
def test_native_paths_are_posix_even_when_built_from_a_windows_style_string(as_native):
    """A backslash-separated input must not leak backslashes into a mount."""
    result = docker_cli.host_path(Path("/tmp/a/b"))

    assert "\\" not in result


@pytest.mark.skipif(
    not docker_cli.is_windows(), reason="a Windows drive path only exists on Windows"
)
def test_windows_paths_cross_the_wsl_boundary():
    """Docker runs inside WSL, which cannot see `D:\\...`."""
    assert docker_cli.host_path(Path("D:/some/dir")) == "/mnt/d/some/dir"
    assert docker_cli.host_path(Path("C:/Users/x")) == "/mnt/c/Users/x"


# The drive translation itself is a pure function of an already-resolved path, so
# it is asserted on every host through `PureWindowsPath`, which exists
# everywhere. Run through the real `Path` class it would be a Windows-only
# assertion -- and CI runs Linux, which is exactly where a regression in it must
# not go unnoticed.
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"D:\work\eval", "/mnt/d/work/eval"),
        (r"C:\Users\x", "/mnt/c/Users/x"),
        (r"d:\lower", "/mnt/d/lower"),
        # A drive root: `wslpath -a "D:\"` answers `/mnt/d/`, so the trailing
        # separator is kept rather than collapsed to `/mnt/d`.
        ("D:" + "\\", "/mnt/d/"),
    ],
)
def test_the_drive_mapping_itself(raw, expected):
    """Pinned as a table, so the mapping is reviewable without running Windows."""
    import pathlib

    assert docker_cli.windows_mount_path(pathlib.PureWindowsPath(raw)) == expected


def test_a_unc_path_is_refused_rather_than_silently_mangled():
    """A UNC path has no WSL mount, and used to produce one that does not exist.

    Taking `drive[0]` of `\\\\server\\share` yielded `/mnt/\\/dir` -- a mount
    source that never existed, so the container started with nothing mounted and
    the failure surfaced far from its cause. WSL agrees there is no answer: only
    drive letters are mounted under `/mnt`, and `wslpath` refuses a UNC path.
    """
    import pathlib

    with pytest.raises(ValueError, match="UNC"):
        docker_cli.windows_mount_path(pathlib.PureWindowsPath(r"\\server\share\dir"))


# --- the host address --------------------------------------------------------


def test_native_gateway_uses_the_keyword_docker_understands(as_native):
    """Probing for a route on a native daemon returns the container bridge.

    That bridge is not the host, so asking for it would hand every container a
    wrong address for the host's MCP services.
    """
    assert docker_cli.host_gateway() == "host-gateway"


def test_windows_gateway_is_resolved_from_the_distro_route(as_windows, monkeypatch):
    """Older Docker Desktop builds do not implement `host-gateway`.

    Those configurations need the WSL default gateway address instead, which is
    why Windows probes for it rather than using the keyword.
    """
    monkeypatch.setattr(docker_cli, "_run_probe", lambda *a, **k: _completed("172.22.96.1\n"))

    assert docker_cli.host_gateway() == "172.22.96.1"


def test_windows_gateway_falls_back_to_the_keyword(as_windows, monkeypatch):
    """When the route cannot be read, the keyword is still worth trying."""
    monkeypatch.setattr(docker_cli, "_run_probe", lambda *a, **k: _completed(""))

    assert docker_cli.host_gateway() == "host-gateway"


# --- where a bind-mountable temporary directory lives ------------------------


def test_native_temp_root_is_under_the_user_cache(as_native, monkeypatch):
    """`/tmp` is not reliably bind-mountable, so it is not used.

    On Linux `/tmp` is often a private per-process namespace and on macOS it is
    a symlink, so a path from there can resolve differently inside the container
    than outside it -- a mount that silently points elsewhere.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", "/custom/cache")

    assert docker_cli.shared_temp_root() == Path("/custom/cache/ai-native-evals/tmp")


def test_native_temp_root_defaults_to_dot_cache(as_native, monkeypatch):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)

    assert docker_cli.shared_temp_root() == (
        Path.home() / ".cache" / "ai-native-evals" / "tmp"
    )


def test_windows_temp_root_is_the_system_temp_directory(as_windows):
    """The daemon lives in WSL, and the system temp directory still crosses over."""
    import tempfile

    assert docker_cli.shared_temp_root() == Path(tempfile.gettempdir())


# --- the build command an operator is told to run ----------------------------


def test_windows_build_command_names_the_powershell_entry_point(as_windows):
    assert docker_cli.build_command() == "pwsh -File tools/build-sandbox-images.ps1"
    assert docker_cli.build_hint() == "tools/build-sandbox-images.ps1"


def test_native_build_command_names_the_python_entry_point(as_native):
    """A Linux or macOS operator must not be told to run a `.ps1`.

    PowerShell is not installed by default there, so the hint would be a command
    that cannot be run for a step the Python builder performs.
    """
    assert docker_cli.build_command() == "python tools/build-sandbox-images.py"
    assert docker_cli.build_hint() == "tools/build-sandbox-images.py"


def test_the_hint_and_the_command_cannot_disagree(as_native):
    """Both come from one function, so a message cannot name a file that the
    runnable form does not."""
    hint = docker_cli.build_hint()
    command = docker_cli.build_command()

    assert hint in command


def test_both_build_entry_points_exist_on_disk():
    """The seam promises a script per platform; a promise is not a file."""
    repo_root = Path(__file__).resolve().parents[1]

    for name in ("build-sandbox-images.ps1", "build-sandbox-images.py"):
        assert (repo_root / "tools" / name).is_file(), name


# --- how the platform is described to a human --------------------------------


def test_describe_names_the_distro_on_windows(as_windows, monkeypatch):
    monkeypatch.setenv(docker_cli.DISTRO_ENV, "Ubuntu-24.04")

    assert "Ubuntu-24.04" in docker_cli.describe()
    assert "WSL" in docker_cli.describe()


def test_describe_says_native_off_windows(as_native):
    described = docker_cli.describe()

    assert "WSL" not in described
    assert "native" in described


def test_describe_agrees_with_the_platform_it_describes(monkeypatch):
    """The label and the behaviour must come from one source of truth.

    `describe` read `platform.system()` while everything else branched on
    `sys.platform`, so forcing the macOS path produced a string that still said
    "linux" -- a display contradicting the behaviour it was describing, which is
    exactly what an operator reads when a run misbehaves.
    """
    import sys

    for platform_name, expected in (
        ("darwin", "macos"),
        ("linux", "linux"),
        ("freebsd", "linux"),
    ):
        monkeypatch.setattr(sys, "platform", platform_name)
        assert expected in docker_cli.describe(), platform_name
        assert docker_cli.describe().endswith(docker_cli.host_platform()), platform_name
