"""An Agent's version is part of its identity.

Before this, every Codex build was tagged `:local`, so a second version
overwrote the first and two versions could not be compared. The version now
names the tag, is recorded in the image itself, and reaches the run profile.

These tests pin the rules that keep the version and the tag from drifting apart,
which is the failure mode that made the old scheme unusable.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from ai_native_evals.agents.profile import AgentProfile, AgentProfileError

REPO = Path(__file__).resolve().parents[1]


def _profile(**overrides: object) -> dict:
    base = {
        "adapter": "codex",
        "image_repository": "ai-native-codex-agent",
        "agent_version": "1.2.3",
    }
    base.update(overrides)
    return base


# --- the version names the tag -----------------------------------------------


def test_the_tag_is_derived_from_the_version() -> None:
    profile = AgentProfile.from_mapping("p", _profile())

    assert profile.image == "ai-native-codex-agent:1.2.3"
    assert profile.agent_version == "1.2.3"


def test_a_bare_image_still_works() -> None:
    """Profiles that name no version must keep loading."""
    profile = AgentProfile.from_mapping("p", {"adapter": "codex", "image": "some:tag"})

    assert profile.image == "some:tag"
    assert profile.agent_version == ""


def test_a_repository_without_a_version_is_refused() -> None:
    """A repository alone cannot name an image, and guessing a tag would be worse."""
    with pytest.raises(AgentProfileError, match="agent_version"):
        AgentProfile.from_mapping("p", {"adapter": "codex", "image_repository": "x"})


def test_an_image_that_contradicts_its_version_is_refused() -> None:
    """The two spellings must agree, or the run would use a different image than
    the manifest reports."""
    with pytest.raises(AgentProfileError, match="does not end in"):
        AgentProfile.from_mapping("p", _profile(image="ai-native-codex-agent:9.9.9"))


def test_a_matching_image_and_version_are_accepted() -> None:
    profile = AgentProfile.from_mapping(
        "p", _profile(image="ai-native-codex-agent:1.2.3")
    )

    assert profile.image == "ai-native-codex-agent:1.2.3"


def test_the_version_survives_into_the_manifest_snapshot() -> None:
    """A run must be traceable to the Agent build it used."""
    profile = AgentProfile.from_mapping("p", _profile())

    assert profile.to_dict()["agent_version"] == "1.2.3"


# --- the repository's own profiles -------------------------------------------


def _agent_profiles() -> list[tuple[str, dict]]:
    return [
        (path.name, yaml.safe_load(path.read_text(encoding="utf-8")))
        for path in sorted((REPO / "profiles" / "agents").glob("*.yaml"))
    ]


def test_every_shipped_profile_declares_a_version() -> None:
    """An Agent whose build is unnamed cannot be reproduced."""
    missing = [name for name, data in _agent_profiles() if not data.get("agent_version")]

    assert missing == []


def test_every_shipped_profile_resolves_its_image() -> None:
    for name, data in _agent_profiles():
        profile = AgentProfile.from_mapping(str(data.get("id") or name), data)
        assert profile.image.endswith(f":{profile.agent_version}"), name


def test_no_shipped_profile_uses_a_mutable_tag() -> None:
    """`:local` and friends name no version, which is what made two builds
    indistinguishable."""
    offenders = [
        name
        for name, data in _agent_profiles()
        if str(data.get("image", "")).endswith((":local", ":latest", ":release"))
    ]

    assert offenders == []


# --- the build script --------------------------------------------------------


def test_the_build_script_names_no_agent() -> None:
    """The script builds whatever the profiles declare, and knows no Agent.

    It used to hold a list: `-CodexVersion`, `-DshVersion`, `-IncludeDshRelease`
    and a hard-coded Dockerfile path each. Adding an Agent meant editing this
    script, which is what made it an enumeration rather than an abstraction.

    Both entry points are checked. The Python builder runs on Linux and macOS,
    where the PowerShell one cannot; if only one of them were guarded, an Agent
    name could be reintroduced through the unguarded one.
    """
    for script in ("build-sandbox-images.ps1", "build-sandbox-images.py"):
        text = (REPO / "tools" / script).read_text(encoding="utf-8")
        for name in ("codex", "dsh", "example-cli"):
            assert f"ai-native-{name}-agent" not in text, f"{script} names {name!r}"
            assert f"docker/{name}-agent" not in text, f"{script} names {name!r}"


def test_every_build_entry_point_derives_its_plan_from_the_profiles() -> None:
    """A cross-platform builder must read profiles, not carry its own list.

    The PowerShell script once enumerated Agents through switches like
    `-CodexVersion` and `-IncludeDshRelease`, which is what made it an
    enumeration rather than an abstraction. The Python entry point is held to the
    same rule, so it cannot reintroduce that shape on the other platform.
    """
    for script in ("build-sandbox-images.ps1", "build-sandbox-images.py"):
        text = (REPO / "tools" / script).read_text(encoding="utf-8")
        assert "load_agent_profiles" in text, script


def test_the_build_script_derives_the_tag_from_the_profile() -> None:
    """The tag is `<image_repository>:<version>`, read from the profile."""
    text = (REPO / "tools" / "build-sandbox-images.ps1").read_text(encoding="utf-8")

    assert "$build.repository" in text
    assert "$build.version" in text
    assert "load_agent_profiles" in text


def test_the_build_script_does_not_impose_a_base_image_on_every_agent() -> None:
    """A Dockerfile's ARG default is its own business.

    Passing `NODE_BASE_IMAGE` to every Agent broke one: the gateway image runs as
    an unprivileged user, so `npm install -g` inside a Dockerfile that expected
    the plain Node base failed with EACCES. An Agent that genuinely differs
    declares the override in its own profile.
    """
    text = (REPO / "tools" / "build-sandbox-images.ps1").read_text(encoding="utf-8")

    assert '"--build-arg", "NODE_BASE_IMAGE=ai-native-llm-gateway:local"' not in text
    assert "$build.build_args" in text


def test_the_build_script_no_longer_writes_a_versionless_tag() -> None:
    """A leftover `:local` would recreate the overwriting problem.

    Asserted against the *repository* rather than one script's quoting style: the
    literal tag must not be spelled in either entry point, since a versionless
    tag is what made two builds of different versions overwrite each other.
    """
    for script in ("build-sandbox-images.ps1", "build-sandbox-images.py"):
        text = (REPO / "tools" / script).read_text(encoding="utf-8")

        assert "ai-native-codex-agent:local" not in text, script
        assert "ai-native-dsh-agent:release" not in text, script


def test_the_images_carry_a_version_label() -> None:
    """The tag is a claim; the label is inside the image and cannot be re-pointed."""
    codex = (REPO / "docker" / "codex-agent" / "Dockerfile").read_text(encoding="utf-8")
    dsh = (REPO / "docker" / "dsh-agent" / "Dockerfile.release").read_text(encoding="utf-8")

    assert "ai.native.agent.version" in codex
    assert "ai.native.agent.version" in dsh


def test_the_offline_scripts_derive_the_tag_rather_than_spelling_it() -> None:
    """A literal tag in three scripts is three places to forget after a bump."""
    for name in ("prepare-offline-cache.ps1", "verify-cache.ps1"):
        text = (REPO / "tools" / name).read_text(encoding="utf-8")
        assert "ai-native-codex-agent:local" not in text, name


# --- what is actually on this machine ----------------------------------------


def _docker_images() -> set[str]:
    """The images on this machine, reached the way this platform reaches Docker.

    Built through the platform seam rather than with a literal `wsl.exe`: on
    Linux and macOS there is no WSL, so the hardcoded form returned nothing and
    the version check below silently skipped instead of running.
    """
    from ai_native_evals.runs import docker_cli

    result = subprocess.run(
        docker_cli.docker_argv("images", "--format", "{{.Repository}}:{{.Tag}}"),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


@pytest.mark.skipif(
    "ai-native-codex-agent" not in " ".join(_docker_images()),
    reason="no Agent image built on this machine",
)
def test_every_resolvable_agent_image_exists() -> None:
    """The profiles and the built images must agree, or every run through a
    profile dies at container start."""
    images = _docker_images()
    missing = [
        AgentProfile.from_mapping(str(data.get("id") or name), data).image
        for name, data in _agent_profiles()
        if AgentProfile.from_mapping(str(data.get("id") or name), data).image not in images
    ]

    assert missing == []
