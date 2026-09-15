"""The Linux/macOS image builder.

`tools/build-sandbox-images.ps1` is the Windows entry point; it is a PowerShell
script that reaches Docker through `wsl.exe`, so on Linux and macOS -- where CI
runs, and where Docker is a native daemon -- there was no way to build an image
at all. `tools/build-sandbox-images.py` is that missing entry point.

These tests pin the decisions it makes without building anything: which Agents
it discovers, how it refuses input it cannot honour, and how it picks the Codex
package for the machine's architecture. The last one is the bug the builder
exists to prevent -- an arm64 image receiving an x64 Codex binary.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BUILDER = REPO / "tools" / "build-sandbox-images.py"


def _load_builder():  # type: ignore[no-untyped-def]
    """Import the script as a module.

    It is a `tools/` entry point rather than an installed module, so it is
    loaded by path; importing it is also the check that it has no syntax error
    or import-time side effect.
    """
    spec = importlib.util.spec_from_file_location("build_sandbox_images", BUILDER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_sandbox_images"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def builder():  # type: ignore[no-untyped-def]
    return _load_builder()


# --- discovery ---------------------------------------------------------------


def test_it_reads_the_agents_from_profiles(builder) -> None:
    """The plan comes from `profiles/agents/`, not from a list in the script.

    A list here would be the enumeration the PowerShell script was refactored
    away from: adding an Agent would mean editing the build tool.
    """
    builds = builder.load_builds([])
    ids = {str(item["id"]) for item in builds}

    assert {"codex", "dsh-release", "pi"} <= ids


def test_it_carries_the_fields_a_build_needs(builder) -> None:
    codex = {str(item["id"]): item for item in builder.load_builds([])}["codex"]

    assert codex["dockerfile"] == "docker/codex-agent/Dockerfile"
    assert codex["version_arg"] == "CODEX_VERSION"
    assert codex["repository"] == "ai-native-codex-agent"
    assert codex["version"]


def test_an_agent_without_a_dockerfile_is_discovered_not_dropped(builder) -> None:
    """`example-cli` has no build block and must be *reported*, not silently lost.

    Dropping it would make "every Agent was built" indistinguishable from "one
    Agent was skipped", which is the kind of silence that leaves a profile
    pointing at an image nobody can build.
    """
    builds = {str(item["id"]): item for item in builder.load_builds([])}

    assert "example-cli" in builds
    assert builds["example-cli"]["dockerfile"] == ""


def test_asking_for_one_agent_builds_only_that_one(builder) -> None:
    assert [str(item["id"]) for item in builder.load_builds(["codex"])] == ["codex"]


def test_an_unknown_agent_is_refused_by_name(builder) -> None:
    """Silently building nothing would look like success."""
    with pytest.raises(builder.BuildError, match="Unknown Agent"):
        builder.load_builds(["no-such-agent"])


def test_the_native_build_hint_names_this_file(builder) -> None:
    """The seam promises a build script per platform; the promise must be a file.

    Asserted through the native branch explicitly (`powershell=False`) rather
    than through `build_hint()`, which answers for the *host*: on Windows it
    correctly names the PowerShell script, and asserting otherwise would make
    this test pass or fail depending on where it runs.
    """
    assert BUILDER.is_file()
    assert Path(builder.docker_cli.build_hint(powershell=False)).name == BUILDER.name


# --- architecture selection --------------------------------------------------


@pytest.mark.parametrize(
    ("arch", "expected"),
    [
        ("x86_64", "linux-x64"),
        ("amd64", "linux-x64"),
        ("aarch64", "linux-arm64"),
        ("arm64", "linux-arm64"),
    ],
)
def test_it_maps_the_daemon_architecture_to_a_codex_package(
    builder, monkeypatch, arch: str, expected: str
) -> None:
    """An arm64 image must receive the arm64 package.

    Hard-coded `linux-x64` meant an arm64 image got an x64 binary and `codex`
    died on its first exec -- the failure this mapping exists to prevent.
    """
    monkeypatch.delenv("AI_NATIVE_EVALS_CODEX_PLATFORM", raising=False)
    monkeypatch.delenv("DOCKER_DEFAULT_PLATFORM", raising=False)

    class _Result:
        stdout = arch + "\n"

    monkeypatch.setattr(builder.subprocess, "run", lambda *a, **k: _Result())

    assert builder.host_docker_arch() == expected


def test_an_explicit_platform_override_wins(builder, monkeypatch) -> None:
    monkeypatch.setenv("AI_NATIVE_EVALS_CODEX_PLATFORM", "linux-arm64")

    assert builder.host_docker_arch() == "linux-arm64"


def test_an_unknown_architecture_is_refused_rather_than_defaulted(
    builder, monkeypatch
) -> None:
    """Defaulting to x64 is exactly how the wrong binary got shipped.

    An architecture the builder does not recognise must stop the build, not
    produce an image whose Agent cannot start.
    """
    monkeypatch.delenv("AI_NATIVE_EVALS_CODEX_PLATFORM", raising=False)

    class _Result:
        stdout = "riscv64\n"

    monkeypatch.setattr(builder.subprocess, "run", lambda *a, **k: _Result())

    with pytest.raises(builder.BuildError, match="cannot determine"):
        builder.host_docker_arch()


# --- which inputs a Dockerfile needs -----------------------------------------


def test_codex_cache_is_prepared_only_when_the_dockerfile_copies_it(
    builder, monkeypatch
) -> None:
    """Read from the Dockerfile, so a new Agent needs no change here.

    A list of Agents in the builder would be a second place to forget.
    """
    called: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        called.append(list(argv))

        class _Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Result()

    monkeypatch.setattr(builder.subprocess, "run", fake_run)

    # A Dockerfile that COPYs the Codex cache must trigger preparation.
    builder.prepare_codex_cache_if_needed(
        "docker/codex-agent/Dockerfile", "0.153.4", "https://example.invalid", "linux-x64"
    )
    assert called, "a Dockerfile COPYing the Codex cache must trigger preparation"
    assert "--platform" in called[0]
    assert "linux-x64" in called[0]

    # One that does not must not shell out at all.
    called.clear()
    builder.prepare_codex_cache_if_needed(
        "docker/pi-agent/Dockerfile", "1.0.0", "https://example.invalid", "linux-x64"
    )
    assert called == [], "an Agent that copies no Codex cache must not prepare one"


# --- the offline path --------------------------------------------------------


def test_the_offline_path_reports_what_it_cannot_verify(builder, monkeypatch, tmp_path) -> None:
    """Offline cache verification is a PowerShell script, so on Linux it cannot run.

    Saying so is the point: the images still load, but the operator must know the
    check did not happen rather than assuming it passed.

    `REPO_ROOT` is redirected to an empty directory so the archive is genuinely
    absent. Pointing at the real repository made this test depend on whether a
    2.6 GB cache happened to be on the machine -- and on a machine that had one,
    it proceeded to build instead of reaching the assertion.
    """
    monkeypatch.setattr(builder, "REPO_ROOT", tmp_path)

    class _Args:
        offline = True
        skip_gateway = True
        agent: list[str] = []
        version = ""
        use_mirror = False
        python_base_image = "python:3.12-slim"
        node_base_image = "node:22-bookworm"
        npm_registry = "https://registry.npmmirror.com"
        pypi_index = "https://pypi.tuna.tsinghua.edu.cn/simple"
        skip_platform_arg = True

    with pytest.raises(builder.BuildError, match="Offline cache archive"):
        builder.build(_Args())


def test_offline_never_pulls(builder, monkeypatch, tmp_path) -> None:
    """`--offline` must pass `--network none`; a silent pull defeats the flag.

    The archive is stubbed at a temporary path so this test does not depend on a
    2.6 GB cache being present, and the real profiles are still read -- the
    command shape is what is under test, not the plan.
    """
    archive = tmp_path / "cache" / "docker" / "sandbox-images.tar"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"not a real archive")

    monkeypatch.setattr(builder, "REPO_ROOT", tmp_path)
    commands: list[list[str]] = []
    monkeypatch.setattr(builder, "docker", lambda *a, **k: commands.append(list(a)))
    # The plan is read from the real tree -- the command shape is what is under
    # test -- while every filesystem path the build touches stays in `tmp_path`.
    plan = builder.load_builds(["codex"], root=REPO)
    monkeypatch.setattr(builder, "load_builds", lambda agents, root=None: plan)
    monkeypatch.setattr(builder, "prepare_codex_cache_if_needed", lambda *a, **k: None)

    class _Args:
        offline = True
        skip_gateway = True
        agent = ["codex"]
        version = ""
        use_mirror = False
        python_base_image = "python:3.12-slim"
        node_base_image = "node:22-bookworm"
        npm_registry = "https://registry.npmmirror.com"
        pypi_index = "https://pypi.tuna.tsinghua.edu.cn/simple"
        skip_platform_arg = True

    builder.build(_Args())

    assert commands, "the offline path must at least load and build"
    build_commands = [c for c in commands if c and c[0] == "build"]
    assert build_commands, "the offline path must still invoke docker build"
    for command in build_commands:
        assert "--network" in command and "none" in command


def test_a_dry_run_reports_the_plan_without_building(builder, capsys) -> None:
    """`--json` is how the plan is inspected without a Docker daemon."""
    exit_code = builder.main(["--json"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "ai-native-codex-agent" in out


def test_an_unknown_agent_exits_nonzero(builder, capsys) -> None:
    """A build tool that reports success while building nothing is worse than one
    that fails."""
    assert builder.main(["--agent", "no-such-agent", "--json"]) == 1
