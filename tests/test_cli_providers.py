"""The `provider` CLI surface: what is configured, and what it actually serves.

The `--model` help text pointed at `provider models` before that command
existed, so these tests pin the seam that was missing: one place where an
operator can see the models a provider really serves.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from ai_native_evals import cli, providers
from ai_native_evals.runs import EvalConfigError


@pytest.fixture(autouse=True)
def _clear_provider_cache():
    providers.clear_cache()
    yield
    providers.clear_cache()


def _repo(tmp_path: Path, *, configured: bool = True) -> Path:
    root = tmp_path / "repo"
    (root / "config" / "providers").mkdir(parents=True)
    (root / "profiles" / "providers").mkdir(parents=True)
    (root / "profiles" / "agents").mkdir(parents=True)
    (root / "profiles" / "providers" / "relay.yaml").write_text(
        "id: relay\n"
        "name: Relay\n"
        "env_file: config/providers/relay.env\n"
        "wire_api: [responses]\n"
        "reasoning_levels: [none, high, max]\n"
        "default_reasoning: high\n"
        "models:\n"
        "  - id: declared-only\n"
        "    name: Declared Only\n",
        encoding="utf-8",
    )
    (root / "profiles" / "agents" / "codex.yaml").write_text(
        "id: codex\nadapter: codex\nimage: test-image\n", encoding="utf-8"
    )
    (root / "config" / "eval.yaml").write_text(
        "version: 1\n"
        "profile_roots:\n  providers: profiles/providers\n"
        "paths:\n  runs_root: runs\ndefaults:\n  agent: codex\n",
        encoding="utf-8",
    )
    if configured:
        (root / "config" / "providers" / "relay.env").write_text(
            "UPSTREAM_BASE_URL=http://localhost:1\nUPSTREAM_API_KEY=k\n", encoding="utf-8"
        )
    return root


def _use_repo(monkeypatch, root: Path) -> None:
    monkeypatch.setattr(cli, "_repo_root", lambda: root)


def test_provider_list_answers_without_dialling_the_endpoint(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Opening the list must never wait on a remote endpoint."""
    root = _repo(tmp_path)
    _use_repo(monkeypatch, root)

    def _explode(*_args, **_kwargs):
        raise AssertionError("provider list must not call the network")

    monkeypatch.setattr(providers, "discover_models", _explode)
    assert cli._provider_list(argparse.Namespace(config=None, agent="codex")) == 0

    payload = json.loads(capsys.readouterr().out)
    assert [item["id"] for item in payload["providers"]] == ["relay"]
    assert payload["providers"][0]["configured"] is True
    assert payload["providers"][0]["declared_models"] == ["declared-only"]


def test_provider_list_marks_an_unconfigured_provider(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A provider with no env file cannot run; the list has to say so."""
    root = _repo(tmp_path, configured=False)
    _use_repo(monkeypatch, root)

    assert cli._provider_list(argparse.Namespace(config=None, agent="codex")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["providers"][0]["configured"] is False


def test_provider_models_reports_the_live_list(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """The served list wins, and the payload says where it came from."""
    root = _repo(tmp_path)
    _use_repo(monkeypatch, root)
    monkeypatch.setattr(
        providers, "discover_models", lambda *a, **k: (["served-a", "served-b"], None)
    )

    args = argparse.Namespace(config=None, agent="codex", provider_id="relay", reasoning=False)
    assert cli._provider_models(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "provider"
    assert [m["id"] for m in payload["models"]] == ["served-a", "served-b"]


def test_provider_models_falls_back_to_the_declared_list(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """When the endpoint cannot be asked, show the declared list and say so."""
    root = _repo(tmp_path)
    _use_repo(monkeypatch, root)
    monkeypatch.setattr(
        providers, "discover_models", lambda *a, **k: ([], "connection refused")
    )

    args = argparse.Namespace(config=None, agent="codex", provider_id="relay", reasoning=False)
    assert cli._provider_models(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "declared"
    assert payload["error"] == "connection refused"
    assert [m["id"] for m in payload["models"]] == ["declared-only"]


def test_provider_models_on_an_unconfigured_provider(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """An unconfigured provider still lists its declared models, flagged as such."""
    root = _repo(tmp_path, configured=False)
    _use_repo(monkeypatch, root)

    args = argparse.Namespace(config=None, agent="codex", provider_id="relay", reasoning=False)
    assert cli._provider_models(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "unconfigured"
    assert [m["id"] for m in payload["models"]] == ["declared-only"]


def test_unknown_provider_names_the_configured_ones(tmp_path: Path, monkeypatch) -> None:
    root = _repo(tmp_path)
    _use_repo(monkeypatch, root)

    args = argparse.Namespace(config=None, agent="codex", provider_id="nope", reasoning=False)
    with pytest.raises(EvalConfigError, match="unknown provider"):
        cli._provider_models(args)
