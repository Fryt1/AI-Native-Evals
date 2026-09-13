"""Resolve Console paths without importing the evaluation runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def default_repo_root() -> Path:
    """Return the source repository root in a checkout."""
    return Path(__file__).resolve().parents[2]


def load_eval_config(repo_root: Path, config_path: Path | None = None) -> dict[str, Any]:
    """Load the global evaluator configuration."""
    path = config_path or repo_root / "config" / "eval.yaml"
    if not path.is_file():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def resolve_runs_root(
    repo_root: Path,
    *,
    config_path: Path | None = None,
    runs_root: Path | None = None,
) -> Path:
    """Resolve the configured EvalRuns directory."""
    if runs_root is not None:
        return runs_root.expanduser().resolve()
    config_file = (config_path or repo_root / "config" / "eval.yaml").resolve()
    config = load_eval_config(repo_root, config_file)
    paths = config.get("paths")
    configured = paths.get("runs_root") if isinstance(paths, dict) else None
    if isinstance(configured, str) and configured:
        candidate = Path(configured)
        if not candidate.is_absolute():
            candidate = repo_root / candidate
        return candidate.resolve()
    return (repo_root / ".." / "EvalRuns").resolve()


def resolve_profile_root(repo_root: Path, kind: str) -> Path:
    """Return a profile directory used by the Registry page."""
    config = load_eval_config(repo_root)
    roots = config.get("profile_roots")
    configured = roots.get(kind) if isinstance(roots, dict) else None
    candidate = (
        Path(configured) if isinstance(configured, str) and configured else Path("profiles") / kind
    )
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    return candidate.resolve()
