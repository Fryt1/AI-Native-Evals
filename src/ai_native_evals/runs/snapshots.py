"""Create reproducible repository snapshots for evaluation runs."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

_EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
    "artifacts",
    "runs",
}
_EXCLUDED_FILES = {".env", ".env.local"}


def snapshot_repository(source: Path, destination: Path, ref: str | None = None) -> dict[str, Any]:
    """Snapshot a Git ref or the current working tree without modifying source."""
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"repository path does not exist: {source}")
    destination.mkdir(parents=True, exist_ok=True)

    sha = _git(source, "rev-parse", "HEAD")
    dirty = bool(sha) and bool(_git(source, "status", "--porcelain"))
    requested_ref = ref or "working-tree"
    mode = "working_tree"

    if ref and _git(source, "rev-parse", "--verify", f"{ref}^{{commit}}"):
        resolved_sha = _git(source, "rev-parse", "--verify", f"{ref}^{{commit}}")
        archive = destination.parent / f".{destination.name}.tar"
        try:
            subprocess.run(
                ["git", "-C", str(source), "archive", "--format=tar", "-o", str(archive), ref],
                check=True,
                capture_output=True,
                text=True,
            )
            shutil.unpack_archive(str(archive), str(destination), format="tar")
            mode = "git_ref"
            sha = resolved_sha
            dirty = False
        finally:
            archive.unlink(missing_ok=True)
    else:
        _copy_tree(source, destination)

    return {
        "source": str(source),
        "requested_ref": requested_ref,
        "resolved_commit": sha or None,
        "dirty": dirty,
        "mode": mode,
        "snapshot": str(destination),
    }


def _copy_tree(source: Path, destination: Path) -> None:
    """Copy a working tree while excluding local build/runtime state."""
    for item in source.iterdir():
        if item.name in _EXCLUDED_DIRS or item.name in _EXCLUDED_FILES:
            continue
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(
                item,
                target,
                ignore=shutil.ignore_patterns(*_EXCLUDED_DIRS, *_EXCLUDED_FILES),
            )
        else:
            shutil.copy2(item, target)


def _git(cwd: Path, *args: str) -> str:
    """Run Git and return stdout, or an empty string when unavailable."""
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""
