"""Reclaiming Docker resources left behind by a killed run.

A run releases its containers when it finishes, but a process that is killed --
Ctrl-C, a closed terminal, an interrupted experiment -- never reaches that code.
Nothing notices, because the manifest says whatever it last wrote, so the
resources sit there until something looks for them.

The danger in a reclaim command is deleting a live run's containers, so most of
these tests are about what it must *not* touch.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_native_evals.runs import docker_runtime


def _run(
    runs_root: Path,
    run_id: str,
    *,
    status: str = "completed",
    age_seconds: float = 7200,
) -> Path:
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run-manifest.json").write_text(
        json.dumps({"status": status, "run": {"run_id": run_id, "task_id": "t"}}),
        encoding="utf-8",
    )
    stamp = time.time() - age_seconds
    os.utime(run_dir / "run-manifest.json", (stamp, stamp))
    return run_dir


def _docker_fake(resources: list[tuple[str, str]]):
    """A `_docker_raw` stand-in backed by a fixed resource list.

    ``resources`` is ``(kind, name)`` where kind is ``container`` or ``network``.
    Removals mutate the list, so a test can assert on what survived.
    """

    def fake(distro: str, *args: str):  # type: ignore[no-untyped-def]
        if args[:2] == ("ps", "-a"):
            names = [name for kind, name in resources if kind == "container"]
            return SimpleNamespace(returncode=0, stdout="\n".join(names), stderr="")
        if args[:2] == ("network", "ls"):
            names = [name for kind, name in resources if kind == "network"]
            return SimpleNamespace(returncode=0, stdout="\n".join(names), stderr="")
        if args[:1] == ("rm",):
            resources[:] = [item for item in resources if item[1] != args[-1]]
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:2] == ("network", "rm"):
            resources[:] = [item for item in resources if item[1] != args[-1]]
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return fake


@pytest.fixture()
def runs_root(tmp_path: Path) -> Path:
    root = tmp_path / "EvalRuns"
    root.mkdir()
    return root


def test_a_finished_runs_leftover_is_found(
    runs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(runs_root, "codex-file-smoke-aaa1111111")
    resources = [
        ("container", "ai-native-eval-codex-file-smoke-aaa1111111-agent"),
        ("container", "ai-native-eval-codex-file-smoke-aaa1111111-gateway"),
        ("network", "ai-native-eval-codex-file-smoke-aaa1111111"),
    ]
    monkeypatch.setattr(docker_runtime, "_docker_raw", _docker_fake(resources))

    orphans = docker_runtime.list_orphan_resources(runs_root, grace_seconds=0)

    assert {item["name"] for item in orphans} == {name for _, name in resources}
    assert all(item["run_id"] == "codex-file-smoke-aaa1111111" for item in orphans)


def test_an_evaluator_container_is_attributed_to_its_run(
    runs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Evaluator sandboxes add a nonce and a role; ownership must still resolve.

    These are the resources that actually leak, because the evaluator names them
    itself and the run manifest never mentions them.
    """
    _run(runs_root, "codex-file-smoke-aaa1111111")
    resources = [
        (
            "container",
            "ai-native-eval-codex-file-smoke-aaa1111111-5d1a205-quality-judge-x-eval-agent",
        ),
        ("network", "ai-native-eval-codex-file-smoke-aaa1111111-5d1a205-quality-judge-x-eval"),
    ]
    monkeypatch.setattr(docker_runtime, "_docker_raw", _docker_fake(resources))

    orphans = docker_runtime.list_orphan_resources(runs_root, grace_seconds=0)

    assert len(orphans) == 2
    assert all(item["run_id"] == "codex-file-smoke-aaa1111111" for item in orphans)


def test_a_running_run_is_never_touched(
    runs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one mistake that matters: deleting containers out from under a run."""
    _run(runs_root, "codex-file-smoke-bbb2222222", status="running", age_seconds=0)
    resources = [
        ("container", "ai-native-eval-codex-file-smoke-bbb2222222-agent"),
        ("network", "ai-native-eval-codex-file-smoke-bbb2222222"),
    ]
    monkeypatch.setattr(docker_runtime, "_docker_raw", _docker_fake(resources))

    orphans = docker_runtime.list_orphan_resources(runs_root, grace_seconds=0)

    assert orphans == []


def test_a_recently_finished_run_is_left_alone(
    runs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Status turns terminal before the evaluator sandbox has cleaned up.

    Without a quiet period, reclaim would pull containers out from under an
    evaluation that is still running.
    """
    _run(runs_root, "codex-file-smoke-ccc3333333", status="completed", age_seconds=0)
    resources = [
        ("container", "ai-native-eval-codex-file-smoke-ccc3333333-agent"),
    ]
    monkeypatch.setattr(docker_runtime, "_docker_raw", _docker_fake(resources))

    assert docker_runtime.list_orphan_resources(runs_root, grace_seconds=900) == []
    # The same resources are reclaimed once the grace period has passed.
    assert docker_runtime.list_orphan_resources(runs_root, grace_seconds=0) != []


def test_an_unclaimed_resource_is_still_reported(
    runs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No manifest on disk means nothing will ever clean it up."""
    resources = [("container", "ai-native-eval-ghost-9999999999-agent")]
    monkeypatch.setattr(docker_runtime, "_docker_raw", _docker_fake(resources))

    orphans = docker_runtime.list_orphan_resources(runs_root, grace_seconds=0)

    assert len(orphans) == 1
    assert orphans[0]["run_id"] == ""


def test_foreign_resources_are_ignored(
    runs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Everything not ours is out of scope, whatever it looks like."""
    resources = [
        ("container", "searxng"),
        ("container", "redis"),
        ("network", "bridge"),
    ]
    monkeypatch.setattr(docker_runtime, "_docker_raw", _docker_fake(resources))

    assert docker_runtime.list_orphan_resources(runs_root, grace_seconds=0) == []


def test_a_longer_run_id_wins_over_a_prefix(
    runs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ownership goes to the most specific match, not the first one found."""
    _run(runs_root, "task-a")
    _run(runs_root, "task-a-extra")
    resources = [("container", "ai-native-eval-task-a-extra-agent")]
    monkeypatch.setattr(docker_runtime, "_docker_raw", _docker_fake(resources))

    orphans = docker_runtime.list_orphan_resources(runs_root, grace_seconds=0)

    assert len(orphans) == 1
    assert orphans[0]["run_id"] == "task-a-extra"


def test_reclaim_removes_containers_before_networks(
    runs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A network cannot be removed while a container is still attached.

    The fake enforces the ordering by failing a network removal that arrives
    while a container is still present.
    """
    _run(runs_root, "codex-file-smoke-ddd4444444")
    resources = [
        ("container", "ai-native-eval-codex-file-smoke-ddd4444444-agent"),
        ("network", "ai-native-eval-codex-file-smoke-ddd4444444"),
    ]
    order: list[str] = []

    def strict(distro: str, *args: str):  # type: ignore[no-untyped-def]
        if args[:2] == ("network", "rm"):
            order.append("network")
            if any(kind == "container" for kind, _ in resources):
                return SimpleNamespace(
                    returncode=1, stdout="", stderr="network has active endpoints"
                )
            resources[:] = [item for item in resources if item[1] != args[-1]]
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:1] == ("rm",):
            order.append("container")
            resources[:] = [item for item in resources if item[1] != args[-1]]
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return _docker_fake(resources)(distro, *args)

    monkeypatch.setattr(docker_runtime, "_docker_raw", strict)

    result = docker_runtime.reclaim_orphans(runs_root, wsl_distro="Ubuntu-20.04")

    assert order == ["container", "network"]
    assert result["errors"] == []
    assert resources == []


def test_grace_period_is_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_NATIVE_EVALS_RECLAIM_GRACE_SECONDS", "60")
    assert docker_runtime.reclaim_grace_seconds() == 60
    monkeypatch.setenv("AI_NATIVE_EVALS_RECLAIM_GRACE_SECONDS", "not-a-number")
    assert docker_runtime.reclaim_grace_seconds() == 900
