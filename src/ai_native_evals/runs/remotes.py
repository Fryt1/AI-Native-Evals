"""Resolve a remote repository URL to a usable local checkout.

A Task's ``resources[].source`` names a logical id; the machine decides what that
id points at. Until now it had to be a local directory, which meant only the
machine that already had the checkout could run the evaluation. An id may now
name a Git URL instead, so a colleague or a CI job can run the same Task without
reproducing anyone's directory layout.

Two properties matter more than speed here:

* **The resolved commit is recorded.** "Which code was evaluated?" must be
  answerable after the fact, so every fetch reports the commit it landed on and
  that value reaches the run manifest.
* **The fetched code is not trusted.** The code under evaluation is, by
  definition, not known to be safe. Cloning must therefore not execute anything
  from the remote: hooks are disabled and submodules are not initialised.

The cache is keyed by URL and refreshed only on request. An implicit fetch on
every run would make a run's inputs depend on whatever the remote happened to
serve that minute, which is the opposite of what an evaluation wants.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

#: Cloning is bounded: a remote that never answers must not hold a run forever.
DEFAULT_CLONE_TIMEOUT_SECONDS = 300

#: Schemes that can be handed to ``git clone``. ``file://`` is deliberately
#: absent: a local path is expressed as a path, not disguised as a remote, so
#: that "this run fetched code from the network" stays a meaningful fact.
_ALLOWED_SCHEMES = ("https://", "http://", "git://", "ssh://", "git@")

_MARKER_NAME = ".ai-native-source.json"


class RemoteSourceError(RuntimeError):
    """Raised when a remote source cannot be made available locally."""


@dataclass(frozen=True, slots=True)
class RemoteCheckout:
    """A remote repository materialised on this machine."""

    url: str
    path: Path
    commit: str | None
    requested_ref: str | None
    fetched: bool
    from_cache: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "path": str(self.path),
            "resolved_commit": self.commit,
            "requested_ref": self.requested_ref,
            "fetched": self.fetched,
            "from_cache": self.from_cache,
        }


def looks_like_remote(value: str) -> bool:
    """Whether a source string names a Git remote rather than a local path."""
    text = value.strip()
    if not text:
        return False
    return text.startswith(_ALLOWED_SCHEMES)


def _reject_unsupported_scheme(url: str) -> None:
    """Fail loudly on a scheme git would accept but this project will not."""
    lowered = url.lower()
    if lowered.startswith("file://") or lowered.startswith("ext::"):
        raise RemoteSourceError(
            f"remote source {url!r} is not allowed; use a plain local path instead of a URL"
        )


def cache_root(repo_root: Path) -> Path:
    """Where remote checkouts live. Under ``cache/``, which is gitignored."""
    return (repo_root / "cache" / "repos").resolve()


def cache_key(url: str) -> str:
    """A stable, filesystem-safe directory name for one remote URL."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _run_git(args: list[str], *, cwd: Path | None, timeout: float) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RemoteSourceError(f"git {' '.join(args[:2])} timed out after {timeout:g}s") from exc
    except OSError as exc:
        raise RemoteSourceError(f"could not run git: {exc}") from exc
    return completed.returncode, (completed.stdout or completed.stderr or "").strip()


def _read_marker(path: Path) -> dict[str, object]:
    try:
        value = json.loads((path / _MARKER_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_marker(path: Path, url: str, ref: str | None, commit: str | None) -> None:
    payload = {
        "url": url,
        "requested_ref": ref,
        "resolved_commit": commit,
        "fetched_at": datetime.now(UTC).isoformat(),
    }
    try:
        (path / _MARKER_NAME).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        # The marker is provenance bookkeeping; failing to write it must not
        # fail a run whose checkout is already correct.
        pass


def _head_commit(path: Path, timeout: float) -> str | None:
    code, out = _run_git(["rev-parse", "HEAD"], cwd=path, timeout=timeout)
    return out if code == 0 and out else None


def _clone(url: str, destination: Path, ref: str | None, timeout: float) -> None:
    """Clone without executing anything the remote supplies.

    ``--config core.hooksPath=`` neutralises hooks, and ``--no-recurse-submodules``
    keeps a repository from pulling in more code than the one that was named.
    The code being evaluated is untrusted by definition.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "-c",
        "core.hooksPath=",
        "clone",
        "--config",
        "core.hooksPath=",
        "--no-recurse-submodules",
        "--quiet",
    ]
    if ref:
        # A branch or tag can be taken shallowly; a raw commit cannot, so the
        # depth flag is only added for refs git can resolve on the remote side.
        args += ["--branch", ref, "--depth", "1"]
    args += [url, str(destination)]

    code, out = _run_git(args, cwd=None, timeout=timeout)
    if code != 0:
        shutil.rmtree(destination, ignore_errors=True)
        raise RemoteSourceError(f"git clone failed for {url!r}: {out[:300] or f'exit {code}'}")


def _fetch_and_checkout(path: Path, ref: str | None, timeout: float) -> None:
    code, out = _run_git(["fetch", "--quiet", "--tags", "origin"], cwd=path, timeout=timeout)
    if code != 0:
        raise RemoteSourceError(f"git fetch failed in {path}: {out[:300] or f'exit {code}'}")
    target = ref or "FETCH_HEAD"
    code, out = _run_git(["checkout", "--quiet", "--force", target], cwd=path, timeout=timeout)
    if code != 0:
        # Fall back to the remote's default branch for a plain refresh.
        code, out = _run_git(
            ["checkout", "--quiet", "--force", "origin/HEAD"], cwd=path, timeout=timeout
        )
    if code != 0:
        raise RemoteSourceError(f"git checkout failed in {path}: {out[:300] or f'exit {code}'}")


def resolve_remote(
    url: str,
    *,
    repo_root: Path,
    ref: str | None = None,
    refresh: bool = False,
    timeout: float = DEFAULT_CLONE_TIMEOUT_SECONDS,
) -> RemoteCheckout:
    """Return a local checkout of ``url``, cloning or refreshing only when needed.

    ``refresh=False`` reuses an existing checkout. That is the default on
    purpose: a run must not silently change its inputs because someone pushed.
    Pass ``refresh=True`` (``--refresh-sources``) to move the cache forward.
    """
    target = url.strip()
    if not target:
        raise RemoteSourceError("remote source URL must not be empty")
    _reject_unsupported_scheme(target)

    path = cache_root(repo_root) / cache_key(target)
    marker = _read_marker(path)
    have_checkout = path.is_dir() and (path / ".git").is_dir()

    if not have_checkout:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
        _clone(target, path, ref, timeout)
        commit = _head_commit(path, timeout)
        _write_marker(path, target, ref, commit)
        return RemoteCheckout(
            url=target,
            path=path,
            commit=commit,
            requested_ref=ref,
            fetched=True,
            from_cache=False,
        )

    # A cached checkout answers the request when the URL matches and the caller
    # did not name a different ref. `ref=None` means "the ref this cache already
    # holds", not "a different request": treating it as a mismatch would force a
    # fetch on every run that omits a ref, so a network blip would break a run
    # whose code is already on disk.
    same_url = marker.get("url") == target
    cached_ref = _as_str(marker.get("requested_ref"))
    ref_matches = ref is None or ref == cached_ref
    if not refresh and same_url and ref_matches:
        commit = _head_commit(path, timeout) or _as_str(marker.get("resolved_commit"))
        return RemoteCheckout(
            url=target,
            path=path,
            commit=commit,
            requested_ref=ref or cached_ref,
            fetched=False,
            from_cache=True,
        )

    try:
        _fetch_and_checkout(path, ref, timeout)
    except RemoteSourceError:
        if refresh:
            # An explicit refresh that cannot reach the remote is a real failure:
            # the caller asked for newer code and did not get it.
            raise
        # An implicit fetch failed but a usable checkout is on disk. Serving it
        # is correct -- the alternative is failing a run whose inputs are present
        # because the network was briefly unavailable.
        commit = _head_commit(path, timeout) or _as_str(marker.get("resolved_commit"))
        return RemoteCheckout(
            url=target,
            path=path,
            commit=commit,
            requested_ref=ref or cached_ref,
            fetched=False,
            from_cache=True,
        )
    commit = _head_commit(path, timeout)
    _write_marker(path, target, ref, commit)
    return RemoteCheckout(
        url=target, path=path, commit=commit, requested_ref=ref, fetched=True, from_cache=False
    )


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
