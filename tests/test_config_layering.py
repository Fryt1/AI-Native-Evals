"""Config layering: the tracked config stays machine-independent.

`config/eval.yaml` is committed, so it must not name a host path. Anything that
differs per machine -- where evaluated repositories live -- goes in the
gitignored `config/eval.local.yaml`, which overrides the tracked file key by key.
These tests pin both halves of that contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ai_native_evals.runs.resolver import EvalConfigError, load_config


def _write(path: Path, payload: dict) -> None:
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")


def test_local_overlay_supplies_machine_paths(tmp_path: Path) -> None:
    """The tracked file declares the shape; the local file fills in the paths."""
    _write(
        tmp_path / "eval.yaml",
        {"version": 1, "paths": {"source_roots": {}, "runs_root": "../EvalRuns"}},
    )
    _write(
        tmp_path / "eval.local.yaml",
        {"paths": {"source_roots": {"my_project": "../my-project"}}},
    )

    config = load_config(tmp_path / "eval.yaml")

    assert config["paths"]["source_roots"] == {"my_project": "../my-project"}
    assert config["paths"]["runs_root"] == "../EvalRuns"


def test_local_overlay_wins_key_by_key(tmp_path: Path) -> None:
    """Merging is per key, so overriding one path does not drop the others."""
    _write(
        tmp_path / "eval.yaml",
        {"paths": {"source_roots": {"a": "../a"}, "runs_root": "../EvalRuns"}},
    )
    _write(tmp_path / "eval.local.yaml", {"paths": {"runs_root": "D:/runs"}})

    config = load_config(tmp_path / "eval.yaml")

    assert config["paths"]["runs_root"] == "D:/runs"
    assert config["paths"]["source_roots"] == {"a": "../a"}


def test_absent_overlay_is_not_an_error(tmp_path: Path) -> None:
    """A fresh clone with no local file still loads the tracked config."""
    _write(tmp_path / "eval.yaml", {"version": 1})

    assert load_config(tmp_path / "eval.yaml") == {"version": 1}


def test_local_overlay_must_be_an_object(tmp_path: Path) -> None:
    _write(tmp_path / "eval.yaml", {"version": 1})
    (tmp_path / "eval.local.yaml").write_text("- just\n- a list\n", encoding="utf-8")

    with pytest.raises(EvalConfigError, match="must be a YAML object"):
        load_config(tmp_path / "eval.yaml")


def test_broken_local_overlay_names_the_file(tmp_path: Path) -> None:
    """A malformed local file must say which file to fix."""
    _write(tmp_path / "eval.yaml", {"version": 1})
    (tmp_path / "eval.local.yaml").write_text("paths: [unclosed\n", encoding="utf-8")

    with pytest.raises(EvalConfigError, match="eval.local.yaml"):
        load_config(tmp_path / "eval.yaml")


def test_null_source_root_entries_are_dropped(tmp_path: Path) -> None:
    """A placeholder left as null means "not configured here", not a path."""
    from ai_native_evals.runs.resolver import _source_roots

    assert _source_roots({"source_roots": {"a": "../a", "b": None}}) == {"a": "../a"}


def test_tracked_config_declares_no_host_path() -> None:
    """The committed config must not leak this machine's layout.

    This is the regression guard: a future edit that pastes a real sibling
    repository path back into the tracked file fails here.
    """
    repo = Path(__file__).resolve().parents[1]
    tracked = yaml.safe_load((repo / "config" / "eval.yaml").read_text(encoding="utf-8"))
    paths = tracked.get("paths", {})

    source_roots = paths.get("source_roots")
    assert source_roots in ({}, None), (
        "config/eval.yaml is tracked; machine paths belong in config/eval.local.yaml"
    )
    for key, value in paths.items():
        if isinstance(value, str):
            assert not Path(value).is_absolute(), f"paths.{key} must not be an absolute host path"


def test_tracked_config_has_no_windows_drive_literals() -> None:
    repo = Path(__file__).resolve().parents[1]
    text = (repo / "config" / "eval.yaml").read_text(encoding="utf-8")

    assert "D:\\" not in text
    assert "D:/" not in text
    assert "C:\\" not in text
