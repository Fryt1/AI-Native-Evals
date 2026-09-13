"""Resource source resolution after machine paths moved out of tracked config.

`config/eval.yaml` ships with `source_roots: {}`, so the first thing a fresh
clone hits is a Task whose `resources[].source` names an id that this machine has
not configured. That must say so. Treating the bare name as a relative directory
instead would resolve to a path that silently does not exist, and the operator
would go looking for a missing directory instead of a missing config entry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_native_evals.resources import _looks_like_source_id, _resolve_source

REPO = Path("/repo")


def test_configured_id_resolves_to_its_path() -> None:
    resolved = _resolve_source(
        "my_project", repo_root=REPO, source_roots={"my_project": "../my-project"}
    )

    assert resolved is not None
    assert resolved.name == "my-project"


def test_unconfigured_id_names_the_file_to_edit() -> None:
    """The message must point at config/eval.local.yaml, not at a missing dir."""
    with pytest.raises(Exception) as excinfo:
        _resolve_source("my_project", repo_root=REPO, source_roots={})

    message = str(excinfo.value)
    assert "my_project" in message
    assert "eval.local.yaml" in message
    assert "source_roots" in message


def test_unconfigured_id_lists_what_is_configured() -> None:
    with pytest.raises(Exception) as excinfo:
        _resolve_source("wanted", repo_root=REPO, source_roots={"other": "../other"})

    assert "other" in str(excinfo.value)


@pytest.mark.parametrize(
    "value",
    ["../project", "sub/dir", "./local", "D:/abs/path", "C:\\abs\\path", ".hidden", ".."],
)
def test_real_paths_are_still_treated_as_paths(value: str) -> None:
    """Only a bare identifier can be a misconfigured id; paths must keep working."""
    resolved = _resolve_source(value, repo_root=REPO, source_roots={})

    assert resolved is not None


@pytest.mark.parametrize("value", ["my_project", "project2", "_private", "a"])
def test_bare_identifiers_look_like_ids(value: str) -> None:
    assert _looks_like_source_id(value) is True


@pytest.mark.parametrize(
    "value",
    ["../x", "a/b", "a\\b", "D:/x", ".", "..", ".hidden", "", "with space"],
)
def test_paths_do_not_look_like_ids(value: str) -> None:
    assert _looks_like_source_id(value) is False


def test_none_source_is_allowed() -> None:
    """A host_service resource declares no source at all."""
    assert _resolve_source(None, repo_root=REPO, source_roots={}) is None


def test_empty_source_is_rejected() -> None:
    with pytest.raises(Exception, match="non-empty string"):
        _resolve_source("   ", repo_root=REPO, source_roots={})


def test_paths_prefixed_form_still_resolves() -> None:
    """`paths.<id>` is the legacy spelling of the same lookup."""
    resolved = _resolve_source(
        "paths.my_project", repo_root=REPO, source_roots={"my_project": "../p"}
    )

    assert resolved is not None
    assert resolved.name == "p"
