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
