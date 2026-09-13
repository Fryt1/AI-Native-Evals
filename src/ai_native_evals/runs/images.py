"""Docker image availability and identity.

Two failures motivated this module, and both were silent:

* An Agent profile can name an image that was never built. Nothing checked, so
  the run previewed happily and died only when Docker tried to start it -- or,
  when the missing thing was an MCP binary rather than the image, did not fail
  at all: the Agent ran without the MCP servers its task required and still
  reported success.
* An image tag alone still does not say which build ran. Agent tags now carry
  the Agent's version, so two versions coexist instead of overwriting, but a
  rebuild of the same version keeps its tag. The manifest therefore records the
  resolved image ID as well, which is the only value that identifies a build.

The check is deliberately separate from the Docker runtime: a preview must be
able to answer "can this run start?" without creating containers, and an
operator on a machine without Docker should get "cannot check" rather than a
crash.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

# A tag or digest never needs a network round trip; only a genuinely absent
# image costs a pull attempt, and we never pull implicitly.
_DEFAULT_INSPECT_TIMEOUT_SECONDS = 60
_DEFAULT_WSL_DISTRO = "Ubuntu-20.04"


@dataclass(frozen=True, slots=True)
class ImageStatus:
    """What is known about one Docker image reference on this machine."""

    reference: str
    #: ``True`` when the image exists, ``False`` when Docker said so, ``None``
    #: when the question could not be asked (no Docker, no WSL, timeout).
    present: bool | None
    image_id: str | None = None
    error: str | None = None

    @property
    def known(self) -> bool:
        return self.present is not None


def wsl_distro(explicit: str | None = None) -> str:
    """Resolve the WSL distro that hosts Docker, matching the runtime's default."""
    return (
        explicit
        or os.environ.get("AI_NATIVE_EVALS_WSL_DISTRO")
        or _DEFAULT_WSL_DISTRO
    ).strip() or _DEFAULT_WSL_DISTRO


def inspect_image(
    reference: str,
    *,
    distro: str | None = None,
    timeout: float = _DEFAULT_INSPECT_TIMEOUT_SECONDS,
) -> ImageStatus:
    """Ask Docker whether ``reference`` exists, and capture its immutable ID.

    Never pulls. A missing image is reported as missing; an unusable Docker
    environment is reported as unknown, because "I could not check" and "it is
    not there" must not look the same to someone about to launch a run.
    """
    target = reference.strip()
    if not target:
        return ImageStatus(reference=reference, present=None, error="empty image reference")
    try:
        completed = subprocess.run(
            [
                "wsl.exe",
                "-d",
                wsl_distro(distro),
                "--",
                "docker",
                "image",
                "inspect",
                "--format",
                "{{.Id}}",
                target,
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ImageStatus(
            reference=target,
            present=None,
            error=f"docker image inspect timed out after {timeout:g}s",
        )
    except OSError as exc:
        return ImageStatus(reference=target, present=None, error=f"could not run wsl.exe: {exc}")

    output = (completed.stdout or "").strip()
    if completed.returncode == 0 and output:
        return ImageStatus(reference=target, present=True, image_id=output.splitlines()[0].strip())
    if completed.returncode == 0:
        # Inspect succeeded but printed nothing usable; treat as unknown rather
        # than inventing a verdict.
        return ImageStatus(
            reference=target, present=None, error="docker image inspect returned no ID"
        )
    detail = _combined(completed).strip()
    if _looks_like_missing(detail):
        return ImageStatus(reference=target, present=False, error=detail or None)
    return ImageStatus(
        reference=target,
        present=None,
        error=detail or f"docker image inspect exited {completed.returncode}",
    )


def _looks_like_missing(detail: str) -> bool:
    """Distinguish "no such image" from "Docker itself is unavailable"."""
    lowered = detail.lower()
    if not lowered:
        # A bare non-zero exit with no output is how `docker image inspect`
        # reports an absent image in some shells.
        return True
    markers = (
        "no such image",
        "no such object",
        "not found",
        "unable to find image",
    )
    return any(marker in lowered for marker in markers)


def _combined(completed: subprocess.CompletedProcess[str]) -> str:
    return "".join(part for part in (completed.stdout, completed.stderr) if part)


def require_images(
    references: list[tuple[str, str]],
    *,
    distro: str | None = None,
    timeout: float = _DEFAULT_INSPECT_TIMEOUT_SECONDS,
) -> dict[str, ImageStatus]:
    """Check several ``(label, reference)`` pairs, keeping every result.

    Returns one entry per unique reference. Callers report the misses
    themselves so the message can name which profile asked for what.
    """
    statuses: dict[str, ImageStatus] = {}
    for _label, reference in references:
        target = reference.strip()
        if not target or target in statuses:
            continue
        statuses[target] = inspect_image(target, distro=distro, timeout=timeout)
    return statuses


def available_versions(
    repository: str,
    *,
    distro: str | None = None,
    timeout: float = _DEFAULT_INSPECT_TIMEOUT_SECONDS,
) -> list[str]:
    """The tags of a repository that exist on this machine.

    A version is only usable if its image was built, so the images are the
    registry of what can actually be run -- not a list kept in a file that
    drifts the moment someone builds or prunes one.

    Returns an empty list when Docker cannot be asked, so a caller shows "no
    other versions" rather than inventing some.
    """
    name = repository.strip()
    if not name:
        return []
    target = wsl_distro(distro)
    try:
        completed = subprocess.run(
            [
                "wsl.exe", "-d", target, "--",
                "docker", "images", name, "--format", "{{.Tag}}",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []
    tags = {
        line.strip()
        for line in (completed.stdout or "").splitlines()
        if line.strip() and line.strip() != "<none>"
    }
    return sorted(tags, reverse=True)


def missing_images(
    references: list[tuple[str, str]],
    *,
    distro: str | None = None,
    timeout: float = _DEFAULT_INSPECT_TIMEOUT_SECONDS,
) -> list[ImageStatus]:
    """Return only the references Docker positively reported as absent.

    Unknown results are excluded on purpose: a machine without Docker must not
    be told its images are missing.
    """
    return [
        status
        for status in require_images(references, distro=distro, timeout=timeout).values()
        if status.present is False
    ]


def image_paths(spec: object) -> list[tuple[str, str]]:
    """Collect every image one run will start, as ``(role, reference)`` pairs."""
    pairs: list[tuple[str, str]] = []
    agent_image = str(getattr(spec, "agent_image", "") or "").strip()
    if agent_image:
        pairs.append(("agent", agent_image))
    evaluator_profile = getattr(spec, "evaluator_agent_profile", None)
    evaluator_image = str(getattr(evaluator_profile, "image", "") or "").strip()
    if evaluator_image and evaluator_image != agent_image:
        pairs.append(("evaluator", evaluator_image))
    sandbox = getattr(spec, "sandbox", None)
    if isinstance(sandbox, dict):
        gateway_image = str(sandbox.get("gateway_image") or "").strip()
        if gateway_image:
            pairs.append(("gateway", gateway_image))
    return pairs
