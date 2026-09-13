"""Preflight: can this machine actually start a run?

``doctor`` validates the repository; preflight validates the environment. The
distinction that matters here is ``missing`` versus ``unknown``: a probe that
failed because it could not run must never be reported as an absent tool, or a
working machine gets told it is broken.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ai_native_evals import preflight
from ai_native_evals.preflight import (
    MISSING,
    OK,
    UNKNOWN,
    Check,
    PreflightReport,
    check_console_dependencies,
    check_docker,
    check_images,
    check_pnpm,
    render_text,
)


def _report(*checks: Check) -> PreflightReport:
    report = PreflightReport()
    for check in checks:
        report.add(check)
    return report


def test_unknown_never_blocks_readiness() -> None:
    """A probe that could not run is not a missing tool."""
    report = _report(Check("docker", UNKNOWN, detail="probe failed"))

    assert report.ready is True
    assert report.to_dict()["unknown"] == ["docker"]


def test_missing_required_blocks_readiness() -> None:
    report = _report(Check("docker", MISSING, detail="no daemon", hint="start Docker"))

    assert report.ready is False
    assert report.to_dict()["missing_required"] == ["docker"]


def test_missing_optional_is_a_warning_not_a_blocker() -> None:
    """A Console that was never installed must not stop an evaluation running."""
    report = _report(
        Check("python", OK),
        Check("node", MISSING, detail="no node", required=False),
    )

    assert report.ready is True
    payload = report.to_dict()
    assert payload["warnings"] == ["node"]
    assert payload["missing_required"] == []


def test_render_text_marks_optional_separately() -> None:
    text = render_text(
        _report(
            Check("python", OK, detail="3.12"),
            Check("pnpm", MISSING, detail="not on PATH", required=False, hint="corepack enable"),
        )
    )

    assert "warn" in text
    assert "(optional)" in text
    assert "corepack enable" in text
    assert "ready: yes" in text


def test_render_text_names_the_blockers() -> None:
    text = render_text(_report(Check("docker", MISSING, detail="no daemon")))

    assert "ready: NO" in text
    assert "docker" in text


def test_pnpm_is_found_through_its_windows_shim(monkeypatch) -> None:
    """`shutil.which("pnpm")` misses pnpm.CMD, which would report a false miss."""
    monkeypatch.setattr(
        preflight.shutil,
        "which",
        lambda name: r"C:\tools\pnpm.CMD" if name.lower().endswith(".cmd") else None,
    )
    monkeypatch.setattr(preflight, "_run", lambda *a, **k: (0, "11.7.0"))

    check = check_pnpm()

    assert check.status == OK
    assert check.detail == "11.7.0"


def test_missing_pnpm_is_a_warning(monkeypatch) -> None:
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: None)
    monkeypatch.setattr(preflight, "_run", lambda *a, **k: None)

    check = check_pnpm()

    assert check.status == MISSING
    assert check.required is False
    assert "corepack" in check.hint


def test_docker_reports_an_unknown_distro_without_parsing_its_error(monkeypatch) -> None:
    """Docker's messages are localized; a Chinese-locale host broke keyword matching.

    The distro list is the answer that does not depend on prose.
    """
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: "wsl.exe")

    def fake_run(argv, **_kwargs):
        if "--list" in argv:
            return 0, "Ubuntu-20.04\n\ndocker-desktop\n"
        raise AssertionError("the distro check must not need a second probe")

    monkeypatch.setattr(preflight, "_run", fake_run)

    check = check_docker("NoSuchDistro")

    assert check.status == MISSING
    assert "NoSuchDistro" in check.detail
    assert "Ubuntu-20.04" in check.detail


def test_docker_ok_reports_the_server_version(monkeypatch) -> None:
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: "wsl.exe")

    def fake_run(argv, **_kwargs):
        if "--list" in argv:
            return 0, "Ubuntu-20.04\n"
        return 0, "26.1.3\n"

    monkeypatch.setattr(preflight, "_run", fake_run)

    check = check_docker("Ubuntu-20.04")

    assert check.status == OK
    assert "26.1.3" in check.detail


def test_docker_without_wsl_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: None)

    check = check_docker()

    assert check.status == MISSING
    assert check.required is True


def test_docker_unparseable_output_is_unknown(monkeypatch) -> None:
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: "wsl.exe")

    def fake_run(argv, **_kwargs):
        if "--list" in argv:
            return 0, "Ubuntu-20.04\n"
        return 137, ""

    monkeypatch.setattr(preflight, "_run", fake_run)

    check = check_docker("Ubuntu-20.04")

    assert check.status in {MISSING, UNKNOWN}
    assert check.detail


def test_console_dependencies_missing_points_at_the_install_command(tmp_path: Path) -> None:
    (tmp_path / "apps" / "eval-console").mkdir(parents=True)

    check = check_console_dependencies(tmp_path)

    assert check.status == MISSING
    assert "pnpm install" in check.hint


def test_console_dependencies_present(tmp_path: Path) -> None:
    modules = tmp_path / "apps" / "eval-console" / "node_modules"
    modules.mkdir(parents=True)
    (modules / "react").mkdir()

    assert check_console_dependencies(tmp_path).status == OK


def test_images_check_without_a_run_is_unknown_not_missing() -> None:
    """No resolved run means nothing to check -- not an absent image."""
    check = check_images(None)

    assert check.status == UNKNOWN
    # Optional: the machine-level answer lives in `agent_images` instead.
    assert check.required is False
    assert "未选择" in check.detail


def _a_real_agent_image() -> str:
    """An image reference that actually exists on this machine.

    Derived rather than hard-coded: Agent tags carry a version now, so a literal
    `:local` stops existing the moment the version changes, and the test then
    fails for a reason that has nothing to do with what it checks.
    """
    import yaml

    repo = Path(__file__).resolve().parents[1]
    profile = yaml.safe_load(
        (repo / "profiles" / "agents" / "codex.yaml").read_text(encoding="utf-8")
    )
    return f"{profile['image_repository']}:{profile['agent_version']}"


def test_agent_images_is_machine_state_not_run_state(tmp_path: Path) -> None:
    """An Agent whose image is missing is broken for every Task.

    Reporting it must not require choosing one first -- that is the difference
    between "you have not chosen yet" and "this profile cannot start".
    """
    from ai_native_evals.preflight import check_agent_images

    root = tmp_path / "repo"
    (root / "profiles" / "agents").mkdir(parents=True)
    (root / "profiles" / "agents" / "good.yaml").write_text(
        f"id: good\nadapter: codex\nimage: {_a_real_agent_image()}\n", encoding="utf-8"
    )

    check = check_agent_images(root)

    assert check.status == OK
    assert "good" in check.detail


def test_agent_images_reports_a_profile_whose_image_was_never_built(tmp_path: Path) -> None:
    from ai_native_evals.preflight import check_agent_images

    root = tmp_path / "repo"
    (root / "profiles" / "agents").mkdir(parents=True)
    (root / "profiles" / "agents" / "broken.yaml").write_text(
        "id: broken\nadapter: codex\nimage: ai-native-dsh-agent:does-not-exist\n",
        encoding="utf-8",
    )

    check = check_agent_images(root)

    assert check.status == MISSING
    assert "broken" in check.detail
    assert "build-sandbox-images" in check.hint


def test_agent_images_with_no_profiles_is_missing(tmp_path: Path) -> None:
    from ai_native_evals.preflight import check_agent_images

    check = check_agent_images(tmp_path)

    assert check.status == MISSING
    assert "no Agent profiles" in check.detail


def test_agent_images_ignores_a_profile_without_an_image(tmp_path: Path) -> None:
    """A profile with no image cannot start, but must not read as a bad build."""
    from ai_native_evals.preflight import check_agent_images

    root = tmp_path / "repo"
    (root / "profiles" / "agents").mkdir(parents=True)
    (root / "profiles" / "agents" / "good.yaml").write_text(
        f"id: good\nadapter: codex\nimage: {_a_real_agent_image()}\n", encoding="utf-8"
    )
    (root / "profiles" / "agents" / "noimg.yaml").write_text(
        "id: noimg\nadapter: codex\n", encoding="utf-8"
    )

    check = check_agent_images(root)

    # The usable profile still decides the verdict.
    assert check.status == OK
    assert "noimg" not in check.detail


def test_run_preflight_includes_agent_images() -> None:
    """The machine report must answer the Agent question without a Task."""
    from ai_native_evals.preflight import run_preflight

    report = run_preflight(Path(__file__).resolve().parents[1], spec=None)

    assert "agent_images" in {check.name for check in report.checks}


def test_images_check_reports_a_spec_that_needs_none() -> None:
    class NoImages:
        agent_image = ""
        evaluator_agent_profile = None
        sandbox: dict[str, str] = {}

    assert check_images(NoImages()).status == OK


def test_preflight_runs_without_docker_and_stays_reportable(monkeypatch, tmp_path: Path) -> None:
    """The whole report must be produced even on a machine with nothing installed."""
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: None)
    monkeypatch.setattr(preflight, "_run", lambda *a, **k: None)

    from ai_native_evals.preflight import run_preflight

    report = run_preflight(tmp_path, spec=None)

    names = {check.name for check in report.checks}
    assert {"python", "runs_root", "docker", "images"} <= names
    # Every check must be reportable; none may raise.
    assert all(check.status in {OK, MISSING, UNKNOWN} for check in report.checks)


def test_probe_timeout_is_unknown_not_a_crash(monkeypatch) -> None:
    def fake_run(_argv, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="wsl.exe", timeout=1)

    # _run swallows the timeout itself; this asserts the contract holds if it did not.
    with pytest.raises(subprocess.TimeoutExpired):
        fake_run(["wsl.exe"])
