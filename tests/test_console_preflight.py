"""Environment checks exposed to the Console.

The check probes Docker, credentials and host services, so it runs in the
background and is polled. These tests pin the contract the UI depends on:

* starting returns immediately with an id, rather than blocking the request;
* the result survives a page reload;
* an unchanged selector is not re-probed, so opening the page is cheap;
* a failure to resolve the run being checked is reported *alongside* the
  environment findings, never instead of them.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_native_evals_console.api import create_app
from ai_native_evals_console.checks import PreflightManager, _selector_key


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(repo_root=tmp_path, runs_root=tmp_path))


def _wait(client: TestClient, check_id: str, timeout: float = 25.0) -> dict:
    """Poll until the check settles, the way the page does."""
    deadline = time.time() + timeout
    payload: dict = {}
    while time.time() < deadline:
        payload = client.get(f"/api/v1/preflight/{check_id}").json()
        if payload["status"] != "running":
            return payload
        time.sleep(0.2)
    raise AssertionError(f"check did not settle within {timeout}s: {payload}")


def test_starting_a_check_returns_immediately(tmp_path: Path) -> None:
    """Probing takes seconds; the request must not wait for it."""
    client = _client(tmp_path)

    started = time.time()
    response = client.post("/api/v1/preflight", json={})
    elapsed = time.time() - started

    assert response.status_code == 202
    assert elapsed < 5, "starting a check must not block on the probes"
    assert response.json()["status"] in {"running", "completed"}


def test_a_check_reports_every_probe(tmp_path: Path) -> None:
    client = _client(tmp_path)
    check_id = client.post("/api/v1/preflight", json={}).json()["check_id"]

    payload = _wait(client, check_id)

    assert payload["status"] == "completed"
    report = payload["report"]
    assert "ready" in report
    # The machine-level probes are always present, whatever they answer.
    for name in ("python", "runs_root", "docker", "images"):
        assert name in report["checks"], name


def test_every_check_status_is_one_of_three_values(tmp_path: Path) -> None:
    """`unknown` is a first-class outcome, not an error."""
    client = _client(tmp_path)
    check_id = client.post("/api/v1/preflight", json={}).json()["check_id"]

    report = _wait(client, check_id)["report"]

    for name, check in report["checks"].items():
        assert check["status"] in {"ok", "missing", "unknown"}, name
        assert isinstance(check["ok"], bool)


def test_result_survives_a_reload(tmp_path: Path) -> None:
    """Reopening the page must not lose the last answer."""
    client = _client(tmp_path)
    check_id = client.post("/api/v1/preflight", json={}).json()["check_id"]
    _wait(client, check_id)

    latest = client.get("/api/v1/preflight")

    assert latest.status_code == 200
    assert latest.json()["check"]["check_id"] == check_id


def test_unknown_check_id_is_404(tmp_path: Path) -> None:
    """A specific id that does not exist is a genuine 404: it was asked for."""
    assert _client(tmp_path).get("/api/v1/preflight/check-nope").status_code == 404


def test_no_check_yet_is_an_empty_result_not_an_error(tmp_path: Path) -> None:
    """A first visit has nothing to show, and that is not a failure.

    It used to answer 404, which put a red console error on the page for every
    new visitor and made this endpoint disagree with `/agents/checks`, whose
    answer to "what has run so far" is an empty result rather than an error.
    """
    response = _client(tmp_path).get("/api/v1/preflight")

    assert response.status_code == 200
    assert response.json() == {"check": None}


def test_an_identical_selector_is_not_re_probed(tmp_path: Path) -> None:
    """Opening the page repeatedly must not re-probe the machine."""
    client = _client(tmp_path)
    first = client.post("/api/v1/preflight", json={}).json()["check_id"]
    _wait(client, first)

    started = time.time()
    second = client.post("/api/v1/preflight", json={})

    assert second.json()["check_id"] == first
    assert time.time() - started < 2


def test_force_re_probes(tmp_path: Path) -> None:
    """After fixing something, the operator must be able to ask again."""
    client = _client(tmp_path)
    first = client.post("/api/v1/preflight", json={}).json()["check_id"]
    _wait(client, first)

    second = client.post("/api/v1/preflight", json={"force": True}).json()["check_id"]

    assert second != first


def test_a_different_selector_is_a_different_check(tmp_path: Path) -> None:
    client = _client(tmp_path)
    first = client.post("/api/v1/preflight", json={}).json()["check_id"]
    _wait(client, first)

    second = client.post("/api/v1/preflight", json={"task_id": "other-task"}).json()

    assert second["check_id"] != first


def test_an_unresolvable_run_still_reports_the_environment(tmp_path: Path) -> None:
    """A bad Task selection must not hide the machine findings.

    The operator came to learn about their machine; refusing to check it because
    a dropdown was incomplete inverts the purpose of a health check.
    """
    client = _client(tmp_path)
    check_id = client.post(
        "/api/v1/preflight", json={"task_id": "no-such-task"}
    ).json()["check_id"]

    payload = _wait(client, check_id)
    report = payload["report"]

    assert payload["status"] == "completed"
    assert "python" in report["checks"], "machine probes must still run"
    assert "run_spec" in report["checks"]
    assert report["checks"]["run_spec"]["status"] == "missing"
    assert "run_spec" in report.get("warnings", [])


def test_an_unresolvable_run_is_a_warning_not_a_blocker(tmp_path: Path) -> None:
    """The machine can be ready even when this particular selection is not."""
    client = _client(tmp_path)
    check_id = client.post(
        "/api/v1/preflight", json={"task_id": "no-such-task"}
    ).json()["check_id"]

    report = _wait(client, check_id)["report"]

    assert "run_spec" not in report["missing_required"]


def test_selector_key_ignores_empty_values() -> None:
    """Blank selectors describe the same request as absent ones."""
    assert _selector_key({"task_id": "t", "agent": None}) == _selector_key(
        {"task_id": "t", "agent": ""}
    )
    assert _selector_key({"task_id": "t"}) != _selector_key({"task_id": "u"})


def test_history_is_bounded(tmp_path: Path) -> None:
    """A long-lived Console must not accumulate check records without limit."""
    manager = PreflightManager(tmp_path)
    try:
        assert manager.latest() is None
        # Exercise pruning directly: the bound is what matters, not the probes.
        for index in range(40):
            manager._records[f"c{index}"] = _stub_record(f"c{index}")  # noqa: SLF001
        with manager._lock:  # noqa: SLF001
            manager._prune_locked()  # noqa: SLF001
        assert len(manager._records) <= 20  # noqa: SLF001
    finally:
        manager.shutdown()


def _stub_record(check_id: str):
    from ai_native_evals_console.checks import _PreflightRecord  # noqa: PLC0415

    return _PreflightRecord(
        check_id=check_id,
        status="completed",
        selector={},
        created_at=f"2026-01-01T00:00:{int(check_id[1:]):02d}+00:00",
    )


def test_manager_shutdown_is_safe_to_call_twice(tmp_path: Path) -> None:
    manager = PreflightManager(tmp_path)
    manager.shutdown()
    manager.shutdown()


@pytest.mark.parametrize("field", ["agent", "provider", "model", "preset", "distro"])
def test_selector_fields_are_accepted(tmp_path: Path, field: str) -> None:
    """The UI sends whichever selectors the user picked; none may 500."""
    response = _client(tmp_path).post("/api/v1/preflight", json={field: "sample"})

    assert response.status_code == 202


# --- Agent verification -------------------------------------------------------


def _wait_agent(client: TestClient, check_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    payload: dict = {}
    while time.time() < deadline:
        payload = client.get(f"/api/v1/agents/checks/{check_id}").json()
        if payload["status"] != "running":
            return payload
        time.sleep(0.2)
    raise AssertionError(f"agent check did not settle: {payload}")


def test_agent_check_starts_and_reports(tmp_path: Path) -> None:
    """Static verification is cheap, so it must complete promptly."""
    client = _client(tmp_path)
    started = time.time()

    response = client.post("/api/v1/agents/codex/check", json={"level": "static"})

    assert response.status_code == 202
    assert time.time() - started < 5
    payload = _wait_agent(client, response.json()["check_id"])
    assert payload["status"] == "completed"
    assert payload["agent"] == "codex"


def test_an_unknown_agent_is_reported_not_crashed(tmp_path: Path) -> None:
    """A typo in the URL must produce a finding, not a 500."""
    client = _client(tmp_path)
    started = client.post("/api/v1/agents/no-such-agent/check", json={"level": "static"}).json()

    payload = _wait_agent(client, started["check_id"])

    assert payload["status"] == "completed"
    assert payload["report"]["usable"] is False
    assert "profile" in payload["report"]["failed"]


def test_an_invalid_level_is_rejected(tmp_path: Path) -> None:
    response = _client(tmp_path).post("/api/v1/agents/codex/check", json={"level": "deep"})

    assert response.status_code == 422


def test_the_default_level_is_the_cheap_one(tmp_path: Path) -> None:
    """A container is never started by accident."""
    client = _client(tmp_path)
    started = client.post("/api/v1/agents/codex/check", json={}).json()

    assert started["level"] == "static"


def test_agent_results_survive_a_reload(tmp_path: Path) -> None:
    client = _client(tmp_path)
    started = client.post("/api/v1/agents/codex/check", json={"level": "static"}).json()
    _wait_agent(client, started["check_id"])

    listing = client.get("/api/v1/agents/checks").json()

    assert listing["items"]["codex"]["check_id"] == started["check_id"]


def test_agent_checks_are_empty_before_any_run(tmp_path: Path) -> None:
    """A first visit has nothing to show; that is not an error."""
    response = _client(tmp_path).get("/api/v1/agents/checks")

    assert response.status_code == 200
    assert response.json()["items"] == {}


def test_unknown_agent_check_id_is_404(tmp_path: Path) -> None:
    assert _client(tmp_path).get("/api/v1/agents/checks/nope").status_code == 404


def test_a_second_check_replaces_the_first(tmp_path: Path) -> None:
    """A fresh run always happens: a verdict cached from before a rebuild would
    be worse than no verdict at all."""
    client = _client(tmp_path)
    first = client.post("/api/v1/agents/codex/check", json={"level": "static"}).json()
    _wait_agent(client, first["check_id"])

    second = client.post("/api/v1/agents/codex/check", json={"level": "static"}).json()

    assert second["check_id"] != first["check_id"]
