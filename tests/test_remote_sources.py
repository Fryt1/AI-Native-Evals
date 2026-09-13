"""Remote resource sources: run a Task without reproducing anyone's layout.

An id in `paths.source_roots` may name a Git URL instead of a local directory,
which is what lets a colleague or a CI job run the same Task. Two properties
carry the weight:

* the resolved commit is recorded, so "which code was evaluated?" stays
  answerable afterwards;
* a cached checkout is reused unless a refresh is explicitly requested, so a
  run's inputs do not change because someone pushed.

These tests never touch the network: `_run_git` is substituted.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ai_native_evals.runs import remotes
from ai_native_evals.runs.remotes import (
    RemoteSourceError,
    cache_key,
    cache_root,
    looks_like_remote,
    resolve_remote,
)


@pytest.mark.parametrize(
    "value",
    [
        "https://github.com/org/repo.git",
        "http://internal/repo.git",
        "git://host/repo",
        "ssh://git@host/repo.git",
        "git@github.com:org/repo.git",
    ],
)
def test_recognised_remote_forms(value: str) -> None:
    assert looks_like_remote(value) is True


@pytest.mark.parametrize(
    "value",
    ["../repo", "sub/dir", "D:/repo", "/abs/repo", "repo", "", "   "],
)
def test_local_paths_are_not_remotes(value: str) -> None:
    """A local path must keep working; only a URL changes behaviour."""
    assert looks_like_remote(value) is False


def test_file_url_is_rejected() -> None:
    """`file://` would let a local path masquerade as a fetched remote.

    Keeping it out means "this run fetched code from the network" stays true
    whenever a remote is involved.
    """
    assert looks_like_remote("file:///tmp/repo") is False


def test_cache_key_is_stable_and_distinct() -> None:
    assert cache_key("https://a/b.git") == cache_key("https://a/b.git")
    assert cache_key("https://a/b.git") != cache_key("https://a/c.git")
    assert len(cache_key("https://a/b.git")) == 16


def test_cache_root_lives_under_gitignored_cache(tmp_path: Path) -> None:
    root = cache_root(tmp_path)

    assert root == (tmp_path / "cache" / "repos").resolve()


class _FakeGit:
    """Records git invocations and answers just enough to drive the flow."""

    def __init__(self, *, clone_creates: bool = True, fail_clone: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.clone_creates = clone_creates
        self.fail_clone = fail_clone

    def __call__(self, args: list[str], *, cwd, timeout):  # noqa: ANN001, ANN202
        self.calls.append(list(args))
        if _subcommand(args) == "clone":
            if self.fail_clone:
                return 1, "fatal: repository not found"
            if self.clone_creates:
                destination = Path(args[-1])
                (destination / ".git").mkdir(parents=True, exist_ok=True)
            return 0, ""
        if args[:2] == ["rev-parse", "HEAD"]:
            return 0, "c0ffee1234567890"
        return 0, ""


def _subcommand(args: list[str]) -> str:
    """The git subcommand, skipping the leading `-c key=value` overrides."""
    index = 0
    while index < len(args) and args[index] == "-c":
        index += 2
    return args[index] if index < len(args) else ""


def _has(args: list[str], subcommand: str) -> bool:
    return _subcommand(args) == subcommand


def test_first_resolution_clones_and_records_the_commit(
    tmp_path: Path, monkeypatch
) -> None:
    """The commit is the answer to "what code was evaluated?"."""
    fake = _FakeGit()
    monkeypatch.setattr(remotes, "_run_git", fake)

    checkout = resolve_remote("https://org/repo.git", repo_root=tmp_path)

    assert checkout.fetched is True
    assert checkout.from_cache is False
    assert checkout.commit == "c0ffee1234567890"
    assert checkout.path.is_dir()
    assert any(_has(call, "clone") for call in fake.calls)


def test_clone_disables_hooks_and_submodules(tmp_path: Path, monkeypatch) -> None:
    """The evaluated code is untrusted, so cloning must not execute any of it."""
    fake = _FakeGit()
    monkeypatch.setattr(remotes, "_run_git", fake)

    resolve_remote("https://org/repo.git", repo_root=tmp_path)

    clone = next(call for call in fake.calls if _has(call, "clone"))
    joined = " ".join(clone)
    assert "core.hooksPath=" in joined
    assert "--no-recurse-submodules" in joined


def test_second_resolution_reuses_the_cache(tmp_path: Path, monkeypatch) -> None:
    """A run must not silently change inputs because the remote moved."""
    fake = _FakeGit()
    monkeypatch.setattr(remotes, "_run_git", fake)

    resolve_remote("https://org/repo.git", repo_root=tmp_path)
    clones_after_first = sum(1 for call in fake.calls if _has(call, "clone"))

    second = resolve_remote("https://org/repo.git", repo_root=tmp_path)

    assert second.from_cache is True
    assert second.fetched is False
    assert sum(1 for call in fake.calls if _has(call, "clone")) == clones_after_first


def test_refresh_moves_the_cache_forward(tmp_path: Path, monkeypatch) -> None:
    fake = _FakeGit()
    monkeypatch.setattr(remotes, "_run_git", fake)
    resolve_remote("https://org/repo.git", repo_root=tmp_path)
    fake.calls.clear()

    refreshed = resolve_remote("https://org/repo.git", repo_root=tmp_path, refresh=True)

    assert refreshed.fetched is True
    assert any(_has(call, "fetch") for call in fake.calls)


def test_omitting_a_ref_reuses_a_checkout_pinned_to_one(
    tmp_path: Path, monkeypatch
) -> None:
    """`ref=None` means "whatever is cached", not "a different request".

    Reading it as a mismatch would force a fetch on every run that omits a ref,
    so a momentary network failure would break a run whose code is already on
    disk.
    """
    fake = _FakeGit()
    monkeypatch.setattr(remotes, "_run_git", fake)
    resolve_remote("https://org/repo.git", repo_root=tmp_path, ref="main")
    fake.calls.clear()

    again = resolve_remote("https://org/repo.git", repo_root=tmp_path)

    assert again.from_cache is True
    assert not any(_has(call, "fetch") for call in fake.calls)


def test_an_unreachable_remote_still_serves_a_cached_checkout(
    tmp_path: Path, monkeypatch
) -> None:
    """Inputs that are already on disk must not be lost to a network blip."""
    fake = _FakeGit()
    monkeypatch.setattr(remotes, "_run_git", fake)
    resolve_remote("https://org/repo.git", repo_root=tmp_path, ref="main")

    def offline(args, *, cwd, timeout):  # noqa: ANN001, ANN202
        if _subcommand(args) == "fetch":
            return 128, "fatal: unable to access remote"
        if args[:2] == ["rev-parse", "HEAD"]:
            return 0, "c0ffee1234567890"
        return 0, ""

    monkeypatch.setattr(remotes, "_run_git", offline)

    # A different ref forces the fetch path, which is now offline.
    checkout = resolve_remote("https://org/repo.git", repo_root=tmp_path, ref="v2")

    assert checkout.from_cache is True
    assert checkout.commit == "c0ffee1234567890"


def test_an_explicit_refresh_that_cannot_reach_the_remote_fails(
    tmp_path: Path, monkeypatch
) -> None:
    """A refresh the caller asked for must not silently serve stale code."""
    fake = _FakeGit()
    monkeypatch.setattr(remotes, "_run_git", fake)
    resolve_remote("https://org/repo.git", repo_root=tmp_path, ref="main")

    def offline(args, *, cwd, timeout):  # noqa: ANN001, ANN202
        if _subcommand(args) in {"fetch", "checkout"}:
            return 128, "fatal: unable to access remote"
        if args[:2] == ["rev-parse", "HEAD"]:
            return 0, "c0ffee1234567890"
        return 0, ""

    monkeypatch.setattr(remotes, "_run_git", offline)

    with pytest.raises(RemoteSourceError):
        resolve_remote("https://org/repo.git", repo_root=tmp_path, ref="v2", refresh=True)


def test_a_different_ref_forces_a_move(tmp_path: Path, monkeypatch) -> None:
    """Asking for another ref with the same URL must not return the old tree."""
    fake = _FakeGit()
    monkeypatch.setattr(remotes, "_run_git", fake)
    resolve_remote("https://org/repo.git", repo_root=tmp_path, ref="main")
    fake.calls.clear()

    other = resolve_remote("https://org/repo.git", repo_root=tmp_path, ref="v2")

    assert other.requested_ref == "v2"
    assert any(call[0] == "fetch" for call in fake.calls)


def test_failed_clone_leaves_no_partial_checkout(tmp_path: Path, monkeypatch) -> None:
    fake = _FakeGit(fail_clone=True)
    monkeypatch.setattr(remotes, "_run_git", fake)

    with pytest.raises(RemoteSourceError, match="clone failed"):
        resolve_remote("https://org/repo.git", repo_root=tmp_path)

    assert not (cache_root(tmp_path) / cache_key("https://org/repo.git")).exists()


def test_clone_timeout_is_a_remote_error(tmp_path: Path, monkeypatch) -> None:
    """A remote that never answers must surface as a remote error, not a hang.

    Patched at `subprocess.run` so the timeout translation inside `_run_git` is
    exercised rather than bypassed.
    """

    def timing_out(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(remotes.subprocess, "run", timing_out)

    with pytest.raises(RemoteSourceError, match="timed out"):
        resolve_remote("https://org/repo.git", repo_root=tmp_path, timeout=1)


def test_missing_git_is_a_remote_error(tmp_path: Path, monkeypatch) -> None:
    """Git absent is reported as such, not as an unhandled OSError."""

    def no_git(*_args, **_kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(remotes.subprocess, "run", no_git)

    with pytest.raises(RemoteSourceError, match="could not run git"):
        resolve_remote("https://org/repo.git", repo_root=tmp_path)


def test_unsupported_scheme_is_rejected_before_any_git_call(
    tmp_path: Path, monkeypatch
) -> None:
    fake = _FakeGit()
    monkeypatch.setattr(remotes, "_run_git", fake)

    with pytest.raises(RemoteSourceError, match="not allowed"):
        resolve_remote("ext::sh -c whoami", repo_root=tmp_path)

    assert fake.calls == []


def test_empty_url_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(RemoteSourceError, match="must not be empty"):
        resolve_remote("   ", repo_root=tmp_path)


def test_marker_records_provenance(tmp_path: Path, monkeypatch) -> None:
    """The provenance file lets a later reader see what was fetched and when."""
    fake = _FakeGit()
    monkeypatch.setattr(remotes, "_run_git", fake)

    checkout = resolve_remote(
        "https://org/repo.git", repo_root=tmp_path, ref="main"
    )
    marker = remotes._read_marker(checkout.path)

    assert marker["url"] == "https://org/repo.git"
    assert marker["requested_ref"] == "main"
    assert marker["resolved_commit"] == "c0ffee1234567890"


# --- environment overrides -------------------------------------------------


def test_environment_points_an_id_at_a_url(monkeypatch) -> None:
    """A CI job supplies the location without editing a file."""
    from ai_native_evals.runs.resolver import _source_roots

    monkeypatch.setenv("AI_NATIVE_EVALS_SOURCE_MY_PROJECT", "https://org/repo.git")
    monkeypatch.setenv("AI_NATIVE_EVALS_SOURCE_REF_MY_PROJECT", "v1.0.0")

    roots = _source_roots({"source_roots": {}})

    assert roots["my_project"] == {"url": "https://org/repo.git", "ref": "v1.0.0"}


def test_environment_overrides_a_configured_path(monkeypatch) -> None:
    from ai_native_evals.runs.resolver import _source_roots

    monkeypatch.setenv("AI_NATIVE_EVALS_SOURCE_MY_PROJECT", "https://org/repo.git")

    roots = _source_roots({"source_roots": {"my_project": "../local-copy"}})

    assert roots["my_project"] == "https://org/repo.git"


def test_environment_keeps_an_existing_ref_when_only_url_is_given(monkeypatch) -> None:
    """Overriding the URL must not silently drop the pinned ref."""
    from ai_native_evals.runs.resolver import _source_roots

    monkeypatch.setenv("AI_NATIVE_EVALS_SOURCE_MY_PROJECT", "https://org/other.git")

    roots = _source_roots(
        {"source_roots": {"my_project": {"url": "https://org/repo.git", "ref": "v1"}}}
    )

    assert roots["my_project"] == {"url": "https://org/other.git", "ref": "v1"}


def test_blank_environment_value_is_ignored(monkeypatch) -> None:
    from ai_native_evals.runs.resolver import _source_roots

    monkeypatch.setenv("AI_NATIVE_EVALS_SOURCE_MY_PROJECT", "   ")

    roots = _source_roots({"source_roots": {"my_project": "../local"}})

    assert roots["my_project"] == "../local"


def test_refresh_flag_is_read_from_environment(monkeypatch) -> None:
    from ai_native_evals.resources import _refresh_requested

    monkeypatch.delenv("AI_NATIVE_EVALS_REFRESH_SOURCES", raising=False)
    assert _refresh_requested() is False
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv("AI_NATIVE_EVALS_REFRESH_SOURCES", value)
        assert _refresh_requested() is True
    monkeypatch.setenv("AI_NATIVE_EVALS_REFRESH_SOURCES", "no")
    assert _refresh_requested() is False


def test_cached_checkout_survives_a_new_process_view(tmp_path: Path, monkeypatch) -> None:
    """The cache is on disk, so a later run in another process still reuses it."""
    monkeypatch.setattr(remotes, "_run_git", _FakeGit())
    first = resolve_remote("https://org/repo.git", repo_root=tmp_path)
    assert first.path.exists()

    monkeypatch.setattr(remotes, "_run_git", _FakeGit())  # a fresh view
    second = resolve_remote("https://org/repo.git", repo_root=tmp_path)

    assert second.from_cache is True
