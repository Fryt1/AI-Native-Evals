"""The read-only CLI surface: every discovery command must run.

These commands are what `tools/eval.ps1 check` calls before anything else, and
they are the ones a person runs to find out what a repository defines. They had
no test at all, so when the resolver dropped a parameter that `_profile_catalog`
still passed positionally, `doctor` and all five `* list` commands raised
`TypeError` on every machine -- while the suite stayed green, because the suite
only ever imported the module.

The bug was not the stale argument. It was that nothing executed the code path.
So this file drives the real `main()` against a throwaway repository and asserts
on what an operator would see.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_native_evals import cli

CONFIG = """\
version: 1
task_roots: [tasks]
profile_roots:
  agents: profiles/agents
  models: profiles/models
  mcp: profiles/mcp
  sandboxes: profiles/sandboxes
  presets: config/presets
paths:
  runs_root: ../EvalRuns
defaults:
  agent: codex
  model_profile: default
  mcp_profile: none
  sandbox_profile: docker-default
"""

TASK = """\
id: demo
version: 1
prompt_file: prompt.md
resources: []
test_plan:
  version: 1
  checks:
    - id: exists
      phase: outcome
      evaluator: script.file_exists.v1
      input:
        path: /workspace/output/result.txt
"""


def _profile(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A minimal but complete repository, with the CLI pointed at it."""
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True)
    (root / "config" / "eval.yaml").write_text(CONFIG, encoding="utf-8")
    _profile(root / "tasks" / "demo" / "task.yaml", TASK)
    _profile(root / "tasks" / "demo" / "prompt.md", "Do the thing.")
    _profile(
        root / "profiles" / "agents" / "codex.yaml",
        "id: codex\nadapter: codex\nimage: test-image\n",
    )
    _profile(
        root / "profiles" / "models" / "default.yaml",
        "id: default\nmodel: some-model\n",
    )
    _profile(root / "profiles" / "mcp" / "none.yaml", "id: none\nservers: {}\n")
    _profile(root / "profiles" / "sandboxes" / "docker-default.yaml", "id: docker-default\n")
    _profile(
        root / "config" / "presets" / "codex-default.yaml",
        "id: codex-default\nagent: codex\n",
    )
    monkeypatch.setattr(cli, "_repo_root", lambda: root)
    return root


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    code = cli.main(list(argv))
    return code, capsys.readouterr().out


@pytest.mark.parametrize(
    ("argv", "key"),
    [
        (("agent", "list"), "agents"),
        (("model", "list"), "models"),
        (("mcp", "list"), "mcp"),
        (("sandbox", "list"), "sandboxes"),
        (("preset", "list"), "presets"),
    ],
)
def test_profile_list_commands_resolve_their_catalog(
    repo: Path, capsys: pytest.CaptureFixture[str], argv: tuple[str, ...], key: str
) -> None:
    """Each `* list` prints the profiles of its own kind, from the config roots."""
    code, out = _run(capsys, *argv)

    assert code == 0
    payload = json.loads(out)
    assert payload[key], f"{argv} resolved no profiles"
    assert "Traceback" not in out


def test_doctor_reports_a_wired_repository(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`doctor` is the repository half of `check`; it must both run and pass."""
    code, out = _run(capsys, "doctor")

    payload = json.loads(out)
    assert payload["ok"] is True, payload
    assert payload["checks"]["agents"]["ids"] == ["codex"]
    assert payload["checks"]["models"]["ids"] == ["default"]
    assert code == 0


def test_doctor_checks_only_the_repository(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`doctor` must not assert facts about the machine it runs on.

    It used to require `wsl.exe`, which is a host fact that `preflight` already
    owns and checks properly. The effect was that doctor failed on any machine
    without WSL -- CI included -- while reporting nothing preflight does not
    report better. The two commands are documented as complementary; this keeps
    them that way.
    """
    _code, out = _run(capsys, "doctor")
    checks = json.loads(out)["checks"]

    assert "wsl" not in checks
    # The repository facts are still there, so the check did not simply shrink.
    assert {"config", "tasks", "agents", "models"} <= set(checks)


def test_preflight_still_owns_the_machine_facts() -> None:
    """Removing the WSL check from `doctor` must not remove it entirely."""
    from ai_native_evals import preflight

    names = {
        check.name
        for check in (
            preflight.check_wsl(),
            preflight.check_python(),
            preflight.check_uv(),
        )
    }
    assert any(name.startswith("wsl") for name in names), names


def test_doctor_reports_the_inspect_ai_wiring_with_evidence(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The framework check must report what it actually read.

    It used to be `{"ok": True}` -- a constant. A check that cannot fail is not a
    check, so a suite that drifted off its pinned framework, or whose entry point
    stopped resolving, would still have been told the repository was wired.
    """
    _code, out = _run(capsys, "doctor")

    check = json.loads(out)["checks"]["inspect_ai"]
    assert check["ok"] is True
    assert check["installed"]
    # The entry point is how `inspect eval` finds the tasks; asserting it by name
    # is what makes this a wiring check rather than a version read.
    assert "ai_native_evals" in check["entry_points"]


def test_the_inspect_ai_check_fails_when_the_version_drifts(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A check that cannot fail verifies nothing, so this pins that it can.

    AGENTS.md requires the framework to stay pinned and to be upgraded
    deliberately, so an installed version that disagrees with the pin is a
    repository fault, not a host quirk.
    """
    import importlib.metadata as metadata

    # A manifest is required for there to be a pin to disagree with; the
    # synthetic repository has none by default (see the no-manifest test below).
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["inspect-ai==0.0.1"]\n', encoding="utf-8"
    )
    real = metadata.version
    monkeypatch.setattr(
        metadata, "version", lambda name: "9.9.9" if name == "inspect-ai" else real(name)
    )

    check = cli._check_inspect_ai(repo)

    assert check["ok"] is False
    assert "does not match the pin" in check["error"]


def test_the_inspect_ai_check_fails_when_the_entry_point_is_gone(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the entry point every documented `inspect eval` command fails."""
    import importlib.metadata as metadata

    # A pin is present so the check reaches the entry-point stage rather than
    # returning early for a different reason.
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["inspect-ai==0.3.263"]\n', encoding="utf-8"
    )

    class _None:
        def __iter__(self):  # type: ignore[no-untyped-def]
            return iter(())

    monkeypatch.setattr(metadata, "entry_points", lambda **_kwargs: _None())

    check = cli._check_inspect_ai(repo)

    assert check["ok"] is False
    assert check["entry_points"] == []
    assert "entry point" in check["error"]


def test_the_inspect_ai_check_reports_a_missing_framework(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib.metadata as metadata

    def missing(name: str) -> str:
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "version", missing)

    check = cli._check_inspect_ai(repo)

    assert check["ok"] is False
    assert "not installed" in check["error"]


def test_a_repository_without_a_manifest_has_no_pin_to_compare(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A tree with no `pyproject.toml` must not be reported as broken wiring.

    The distinction is `preflight`'s: a question that could not be asked is not
    an answer of "broken". The synthetic repository the CLI tests drive has no
    manifest, and neither would a checkout of just the tasks.
    """
    check = cli._check_inspect_ai(tmp_path)

    assert check["ok"] is True, check
    assert check["declared"] is None
    assert check["installed"]


def test_a_manifest_that_pins_nothing_is_a_finding(tmp_path: Path) -> None:
    """Present but unpinned is the state AGENTS.md forbids, and it is knowable."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["PyYAML>=6.0"]\n', encoding="utf-8"
    )

    check = cli._check_inspect_ai(tmp_path)

    assert check["ok"] is False
    assert check["declared"] is None
    assert "no inspect-ai requirement" in check["error"]


def test_profile_roots_follow_the_configuration(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A relocated profile root is honored by the listing and by a real run.

    The two used to read different keys: `model list` honored
    `profile_roots.models` while `resolve_run` looked for
    `profile_roots.model_profiles`, so a machine that moved the directory got a
    listing that disagreed with the run it then launched.
    """
    _profile(
        repo / "elsewhere" / "model.yaml",
        "id: relocated\nmodel: relocated-model\n",
    )
    config = (repo / "config" / "eval.yaml").read_text(encoding="utf-8")
    (repo / "config" / "eval.yaml").write_text(
        config.replace("models: profiles/models", "models: elsewhere"), encoding="utf-8"
    )

    code, out = _run(capsys, "model", "list")

    assert code == 0
    assert "relocated" in json.loads(out)["models"]

    from ai_native_evals.runs import resolve_run

    spec = resolve_run(repo, "demo", model_profile="relocated")
    assert spec.model == "relocated-model"


def test_unknown_profile_kind_is_refused(repo: Path) -> None:
    """An unsupported catalog names itself instead of raising KeyError."""
    from ai_native_evals.runs import EvalConfigError

    with pytest.raises(EvalConfigError, match="unsupported profile catalog"):
        cli._profile_catalog(repo, "nonsense")


@pytest.fixture()
def experiment_dir(tmp_path: Path) -> Path:
    root = tmp_path / "experiments"
    root.mkdir()
    (root / "sweep.yaml").write_text(
        "id: sweep\n"
        "task_id: demo\n"
        "vary:\n"
        "  agent: [codex, dsh]\n"
        "  reasoning_effort: [low, high]\n"
        "fixed:\n"
        "  model: m\n"
        "repeats: 3\n",
        encoding="utf-8",
    )
    return root


def test_experiment_list_reads_the_directory(
    experiment_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(capsys, "experiment", "list", "--dir", str(experiment_dir))

    assert code == 0
    payload = json.loads(out)
    assert list(payload["experiments"]) == ["sweep"]
    # 2 agents x 2 efforts x 3 repeats.
    assert payload["experiments"]["sweep"]["attempt_count"] == 12


def test_experiment_show_prints_the_cells(
    experiment_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The review surface: what varies, what is frozen, and the cells produced."""
    code, out = _run(capsys, "experiment", "show", "sweep", "--dir", str(experiment_dir))

    assert code == 0
    assert "4 cell(s) x 3 = 12 run(s)" in out
    assert "agent=codex · reasoning_effort=low" in out
    assert "fixed      : model=m" in out


def test_experiment_show_as_json_is_machine_readable(
    experiment_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(capsys, "experiment", "show", "sweep", "--dir", str(experiment_dir), "--json")

    assert code == 0
    payload = json.loads(out)
    assert len(payload["cells"]) == 4
    assert payload["fixed"] == {"model": "m"}


def test_unknown_experiment_names_the_known_ones(
    experiment_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The error says which ids do exist, instead of only that this one does not."""
    code = cli.main(["experiment", "show", "nope", "--dir", str(experiment_dir)])

    assert code == 2
    assert "known: sweep" in capsys.readouterr().err


def test_experiment_run_dry_run_starts_nothing(
    experiment_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--dry-run` must show the plan without touching Docker."""
    code, out = _run(
        capsys, "experiment", "run", "sweep", "--dir", str(experiment_dir), "--dry-run"
    )

    assert code == 0
    assert "4 cell(s) x 3 = 12 run(s)" in out
