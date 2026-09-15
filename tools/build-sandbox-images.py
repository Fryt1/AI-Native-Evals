#!/usr/bin/env python3
"""Build the sandbox images on Linux and macOS.

`tools/build-sandbox-images.ps1` is the Windows entry point and stays the one
Windows users run. It cannot work everywhere, though: it reaches Docker through
`wsl.exe`, translates every path to `/mnt/<drive>/...` and relies on
PowerShell, so on Linux and macOS -- where CI runs and where Docker is a native
daemon -- there was no way to build an image at all.

This script is that missing entry point. It is deliberately the same procedure
as the PowerShell one, not a reimplementation with its own opinions:

* which Agents exist, what to build, and which build arguments they need is read
  from ``profiles/agents/*.yaml`` through the same loader the evaluator uses, so
  the two entry points cannot disagree about what a profile means;
* the gateway image is built first, because an Agent's Dockerfile may use it as
  its Node base;
* a profile that declares no ``build`` is reported as "nothing to build" rather
  than silently skipped.

The platform differences (no `wsl.exe`, no `/mnt/` translation, no PowerShell)
are handled by ``ai_native_evals.runs.docker_cli``, the one module that knows
them, rather than by a second copy of that logic here.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from ai_native_evals.runs import docker_cli  # noqa: E402

#: The container image every Agent build may use as its Node base.
GATEWAY_IMAGE = "ai-native-llm-gateway:local"


class BuildError(RuntimeError):
    """A build step failed; the message is what the operator needs to see."""


def load_builds(agent_filter: list[str]) -> list[dict[str, object]]:
    """Every Agent profile that declares how to build itself.

    Read through the shared profile loader, so an Agent added to
    ``profiles/agents/`` is buildable here without editing this script.
    """
    from ai_native_evals.agents.profile import load_agent_profiles

    builds: list[dict[str, object]] = []
    for profile_id, profile in sorted(load_agent_profiles(REPO_ROOT / "profiles" / "agents").items()):
        build = profile.build if isinstance(profile.build, dict) else {}
        builds.append(
            {
                "id": profile_id,
                "dockerfile": str(build.get("dockerfile") or ""),
                "version_arg": str(build.get("version_arg") or ""),
                "repository": profile.image_repository,
                "version": profile.agent_version,
                "image": profile.image,
                "build_args": {
                    str(key): str(value) for key, value in (build.get("args") or {}).items()
                },
            }
        )
    if not builds:
        raise BuildError("No Agent profiles found under profiles/agents")
    if agent_filter:
        known = {str(item["id"]) for item in builds}
        unknown = [name for name in agent_filter if name not in known]
        if unknown:
            raise BuildError(
                f"Unknown Agent(s): {', '.join(unknown)}. Known: {', '.join(sorted(known))}"
            )
        builds = [item for item in builds if item["id"] in agent_filter]
    return builds


def docker(*args: str, cwd: Path | None = None) -> None:
    """Run one Docker command, raising with its output when it fails."""
    argv = docker_cli.docker_argv(*args)
    completed = subprocess.run(
        argv,
        cwd=str(cwd or REPO_ROOT),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        detail = (completed.stdout or "") + (completed.stderr or "")
        raise BuildError(f"{' '.join(argv)} failed:\n{detail.strip()}")


def host_docker_arch() -> str:
    """The architecture of this machine's Docker daemon, as Codex names it.

    The Codex package is chosen from the daemon's architecture rather than the
    host's: an image built for an emulated platform still needs the package for
    that platform, and the multi-arch base images resolve to the daemon's own by
    default.
    """
    raw = os.environ.get("AI_NATIVE_EVALS_CODEX_PLATFORM", "").strip()
    if raw:
        return raw
    machine = (os.environ.get("DOCKER_DEFAULT_PLATFORM") or "").strip()
    if machine:
        arch = machine.rsplit("/", 1)[-1]
    else:
        try:
            completed = subprocess.run(
                docker_cli.docker_argv("version", "--format", "{{.Server.Arch}}"),
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            arch = completed.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            arch = ""
    if arch in ("x86_64", "amd64"):
        return "linux-x64"
    if arch in ("aarch64", "arm64"):
        return "linux-arm64"
    # An unknown architecture must not silently receive an x64 build, which is
    # the bug this exists to prevent.
    raise BuildError(
        f"cannot determine the Codex platform for Docker architecture {arch!r}; "
        "set AI_NATIVE_EVALS_CODEX_PLATFORM to linux-x64 or linux-arm64"
    )


def prepare_codex_cache_if_needed(
    dockerfile: str, version: str, registry: str, platform: str
) -> None:
    """Fetch the large Codex packages outside Docker, when a Dockerfile COPYs them.

    Which inputs a build needs is visible in its Dockerfile, so that is what is
    read -- a list of Agents here would be a second place to forget.
    """
    path = REPO_ROOT / dockerfile
    if not path.is_file():
        return
    body = path.read_text(encoding="utf-8", errors="replace")
    if "cache/codex/" not in body:
        return
    node = shutil.which("node")
    if node is None:
        raise BuildError("node is required to prepare the Codex package cache")
    print("Ensuring local Codex package cache...")
    completed = subprocess.run(
        [
            node,
            str(REPO_ROOT / "tools" / "prepare-codex-cache.mjs"),
            "--version",
            version,
            "--registry",
            registry,
            "--output-dir",
            str(REPO_ROOT / "cache" / "codex"),
            "--platform",
            platform,
        ],
        cwd=str(REPO_ROOT),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        detail = (completed.stdout or "") + (completed.stderr or "")
        raise BuildError(f"Codex package cache preparation failed:\n{detail.strip()}")


def build(args: argparse.Namespace) -> int:
    root = docker_cli.host_path(REPO_ROOT)
    network_args = ["--network", "none"] if args.offline else []

    if args.offline:
        archive = REPO_ROOT / "cache" / "docker" / "sandbox-images.tar"
        if not archive.is_file():
            raise BuildError(
                f"Offline cache archive does not exist: {archive}. "
                "Run the offline cache preparation first."
            )
        print("Loading cached Docker images...")
        docker("load", "-i", docker_cli.host_path(archive))
        # Verification is a PowerShell script; without it the offline path still
        # loads, but the operator is told the check did not run.
        print("note: offline cache verification lives in tools/verify-cache.ps1 (Windows)")

    if not args.skip_gateway:
        print("Building gateway image...")
        docker(
            "build",
            "--pull=false",
            *network_args,
            "-f",
            f"{root}/gateway/Dockerfile",
            "--build-arg",
            f"NODE_BASE_IMAGE={args.node_base_image}",
            "-t",
            GATEWAY_IMAGE,
            root,
        )

    builds = load_builds(args.agent)
    if args.version and len(builds) != 1:
        raise BuildError("-Version names one Agent's version; pair it with a single --agent.")
    platform = host_docker_arch() if not args.skip_platform_arg else ""

    built: list[str] = []
    skipped: list[str] = []
    for item in builds:
        profile_id = str(item["id"])
        dockerfile = str(item["dockerfile"])
        if not dockerfile:
            skipped.append(f"{profile_id} (nothing to build: no dockerfile declared)")
            continue
        version = args.version or str(item["version"])
        if not version:
            raise BuildError(f"Agent {profile_id} has no version and none was given with --version")
        repository = str(item["repository"])
        tag = f"{repository}:{version}" if repository else str(item["image"])
        if not tag:
            raise BuildError(f"Agent {profile_id} has no image repository; add image_repository")

        prepare_codex_cache_if_needed(
            dockerfile, version, args.npm_registry, platform or "linux-x64"
        )

        command = [
            "build",
            "--pull=false",
            *network_args,
            "-f",
            f"{root}/{dockerfile}",
        ]
        version_arg = str(item["version_arg"])
        if version_arg:
            command += ["--build-arg", f"{version_arg}={version}"]
        python_base = args.python_base_image
        if args.use_mirror and not args.offline:
            python_base = "mirror.gcr.io/library/python:3.12-slim"
        command += [
            "--build-arg",
            f"PYTHON_BASE_IMAGE={python_base}",
            "--build-arg",
            f"PYPI_INDEX_URL={args.pypi_index}",
            "--build-arg",
            f"NPM_REGISTRY={args.npm_registry}",
        ]
        if args.offline:
            command += ["--build-arg", "OFFLINE=1"]
        # The Codex platform package must match the image's own architecture.
        # An Agent that does not declare the ARG simply ignores it.
        if platform:
            command += ["--build-arg", f"CODEX_PLATFORM={platform}"]
        build_args = item["build_args"]
        if isinstance(build_args, dict):
            for key, value in build_args.items():
                command += ["--build-arg", f"{key}={value}"]
        command += ["-t", tag, root]
        print(f"Building Agent {profile_id} as {tag}...")
        docker(*command)
        built.append(tag)

    print()
    if built:
        print(f"Built {len(built)} image(s):")
        for tag in built:
            print(f"  {tag}")
    if skipped:
        print(f"Nothing to build for {len(skipped)} Agent(s):")
        for entry in skipped:
            print(f"  {entry}")
    print(f"Codex platform: {platform or 'unchanged (default linux-x64)'}")
    print("Sandbox images are ready.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the sandbox images on Linux and macOS.",
    )
    parser.add_argument(
        "--agent",
        action="append",
        default=[],
        help="which Agent(s) to build, by profile id; repeatable. Default: every Agent",
    )
    parser.add_argument("--version", default="", help="override the version for a single Agent")
    parser.add_argument("--use-mirror", action="store_true", help="build from a mirror base image")
    parser.add_argument("--offline", action="store_true", help="build with --network none")
    parser.add_argument("--skip-gateway", action="store_true", help="do not build the gateway")
    parser.add_argument("--python-base-image", default="python:3.12-slim")
    parser.add_argument("--node-base-image", default="node:22-bookworm")
    parser.add_argument("--npm-registry", default="https://registry.npmmirror.com")
    parser.add_argument("--pypi-index", default="https://pypi.tuna.tsinghua.edu.cn/simple")
    parser.add_argument(
        "--skip-platform-arg",
        action="store_true",
        help="do not pass CODEX_PLATFORM, leaving the Dockerfile default",
    )
    parser.add_argument("--json", action="store_true", help="report the plan and exit")
    args = parser.parse_args(argv)

    try:
        if args.json:
            print(json.dumps(load_builds(args.agent), ensure_ascii=False, indent=2))
            return 0
        return build(args)
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
