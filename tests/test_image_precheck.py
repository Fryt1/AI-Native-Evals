"""Docker image availability: a missing image must fail before a run starts.

Two silent failures motivate these tests:

* An Agent profile can name an image nobody built. Nothing checked, so the run
  previewed fine and failed only once Docker tried to start it.
* A missing MCP binary inside a present image is worse than missing image: the
  Agent runs without the servers the task required and still reports success.

The distinction these tests protect is ``missing`` versus ``cannot check``. A
machine without Docker must never be told its images are absent.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_native_evals.runs import images
from ai_native_evals.runs.images import (
    ImageStatus,
    image_paths,
    inspect_image,
    missing_images,
)


def _completed(
    returncode: int, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["docker"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _fake_run(monkeypatch, result) -> None:
    def fake(*_args, **_kwargs):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(images.subprocess, "run", fake)


def test_present_image_reports_its_immutable_id(monkeypatch) -> None:
    """A tag is not an identity; the ID is what a manifest can be trusted with."""
    _fake_run(monkeypatch, _completed(0, "sha256:abc123\n"))

    status = inspect_image("ai-native-codex-agent:local")

    assert status.present is True
    assert status.image_id == "sha256:abc123"
    assert status.known is True


@pytest.mark.parametrize(
    "stderr",
    [
        "Error response from daemon: No such image: ai-native-codex-agent:local",
        "Error: No such object: nope",
    ],
)
def test_absent_image_is_reported_as_absent(monkeypatch, stderr: str) -> None:
    _fake_run(monkeypatch, _completed(1, "", stderr))

    status = inspect_image("ai-native-codex-agent:local")

    assert status.present is False


def test_a_bare_failure_without_output_counts_as_absent(monkeypatch) -> None:
    """`docker image inspect` can exit non-zero silently for a missing image."""
    _fake_run(monkeypatch, _completed(1))

    assert inspect_image("some-image:tag").present is False


def test_docker_unavailable_is_unknown_not_absent(monkeypatch) -> None:
    """A machine without Docker must not be told its images are missing.

    This is the distinction that makes the precheck safe to run by default:
    silence is not evidence.
    """
    _fake_run(monkeypatch, _completed(1, "", "Cannot connect to the Docker daemon"))

    status = inspect_image("ai-native-codex-agent:local")

    assert status.present is None
    assert status.known is False
    assert status.error


def test_timeout_is_unknown_not_absent(monkeypatch) -> None:
    _fake_run(monkeypatch, subprocess.TimeoutExpired(cmd="docker", timeout=60))

    status = inspect_image("ai-native-codex-agent:local")

    assert status.present is None
    assert "timed out" in (status.error or "")


def test_missing_wsl_is_unknown_not_absent(monkeypatch) -> None:
    _fake_run(monkeypatch, FileNotFoundError("wsl.exe"))

    status = inspect_image("ai-native-codex-agent:local")

    assert status.present is None


def test_only_a_definite_absence_is_returned_as_missing(monkeypatch) -> None:
    """`missing_images` drives a hard failure, so unknown must not qualify."""
    results = {
        "present:tag": _completed(0, "sha256:aaa\n"),
        "absent:tag": _completed(1, "", "No such image: absent:tag"),
        "unknown:tag": _completed(1, "", "Cannot connect to the Docker daemon"),
    }

    def fake(args, **_kwargs):
        return results[args[-1]]

    monkeypatch.setattr(images.subprocess, "run", fake)

    pairs = [("agent", "present:tag"), ("agent", "absent:tag"), ("gateway", "unknown:tag")]
    assert [status.reference for status in missing_images(pairs)] == ["absent:tag"]


def test_image_paths_covers_every_image_a_run_starts() -> None:
    """Agent, evaluator and gateway are all started, so all three are checked."""
    spec = SimpleNamespace(
        agent_image="agent:tag",
        evaluator_agent_profile=SimpleNamespace(image="evaluator:tag"),
        sandbox={"gateway_image": "gateway:tag"},
    )

    assert image_paths(spec) == [
        ("agent", "agent:tag"),
        ("evaluator", "evaluator:tag"),
        ("gateway", "gateway:tag"),
    ]


def test_image_paths_does_not_duplicate_a_shared_image() -> None:
    spec = SimpleNamespace(
        agent_image="same:tag",
        evaluator_agent_profile=SimpleNamespace(image="same:tag"),
        sandbox={"gateway_image": "gateway:tag"},
    )

    assert image_paths(spec) == [("agent", "same:tag"), ("gateway", "gateway:tag")]


def test_image_paths_tolerates_a_spec_without_sandbox() -> None:
    spec = SimpleNamespace(agent_image="agent:tag", evaluator_agent_profile=None, sandbox=None)

    assert image_paths(spec) == [("agent", "agent:tag")]


def test_reference_is_passed_to_docker_untouched(monkeypatch) -> None:
    """The check must ask about the exact reference the run will use."""
    seen: list[list[str]] = []

    def fake(args, **_kwargs):
        seen.append(list(args))
        return _completed(0, "sha256:aaa\n")

    monkeypatch.setattr(images.subprocess, "run", fake)
    inspect_image("ai-native-dsh-agent:local", distro="Ubuntu-20.04")

    assert seen
    assert seen[0][-1] == "ai-native-dsh-agent:local"
    assert "Ubuntu-20.04" in seen[0]


def test_module_stays_importable_without_docker(monkeypatch) -> None:
    """Importing the precheck must not itself require a Docker host."""
    assert "ai_native_evals.runs.images" in sys.modules
    assert Path(images.__file__).is_file()
    assert ImageStatus(reference="x", present=None).known is False
