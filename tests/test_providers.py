"""Provider selection, live model discovery, and reasoning validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ai_native_evals.providers import (
    agent_reasoning_levels,
    allowed_reasoning_levels,
    clear_cache,
    load_provider_profiles,
    provider_summary,
    resolve_wire_api,
)
from ai_native_evals.runs.resolver import EvalConfigError, resolve_run

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clear_provider_cache():
    clear_cache()
    yield
    clear_cache()


def _write_task(root: Path) -> None:
    task_dir = root / "tasks" / "sample"
    task_dir.mkdir(parents=True)
    (task_dir / "task.yaml").write_text(
        "id: sample\nversion: 1\nprompt: do the thing\ntest_plan:\n  version: 1\n  checks: []\n",
        encoding="utf-8",
    )


def _env(base: str = "http://localhost:1") -> dict[str, dict[str, str]]:
    """A minimal provider env file pointing at an address the test never dials."""
    return {
        "config/providers/sub2api.env": {
            "UPSTREAM_BASE_URL": base,
            "UPSTREAM_API_KEY": "k",
        }
    }


def _profile(name: str, **fields) -> str:
    payload = {"id": name, **fields}
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)


def _repo(
    tmp_path: Path,
    providers: dict[str, str],
    env_files: dict[str, dict[str, str]] | None = None,
) -> Path:
    root = tmp_path / "repo"
    (root / "config" / "providers").mkdir(parents=True, exist_ok=True)
    (root / "profiles" / "providers").mkdir(parents=True, exist_ok=True)
    (root / "profiles" / "models").mkdir(parents=True, exist_ok=True)
    (root / "profiles" / "agents").mkdir(parents=True, exist_ok=True)
    for name, body in providers.items():
        (root / "profiles" / "providers" / f"{name}.yaml").write_text(body, encoding="utf-8")
    for rel, values in (env_files or {}).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
            encoding="utf-8",
        )
    (root / "profiles" / "mcp").mkdir(parents=True, exist_ok=True)
    (root / "profiles" / "mcp" / "none.yaml").write_text(
        _profile("none", servers={}), encoding="utf-8"
    )
    (root / "profiles" / "agents" / "codex.yaml").write_text(
        _profile("codex", adapter="codex", image="test-image"), encoding="utf-8"
    )
    (root / "profiles" / "agents" / "dsh.yaml").write_text(
        _profile("dsh", adapter="dsh-acp", image="test-image"), encoding="utf-8"
    )
    (root / "config" / "eval.yaml").write_text(
        "version: 1\n"
        "profile_roots:\n  providers: profiles/providers\n"
        "paths:\n  runs_root: runs\ndefaults:\n  agent: codex\n",
        encoding="utf-8",
    )
    _write_task(root)
    return root


def test_reasoning_levels_intersect_provider_and_agent() -> None:
    """A level must be legal for both sides; neither may assume the other."""
    # The relay rejects `ultra`; Codex alone would have accepted it.
    provider = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
    assert allowed_reasoning_levels(provider, agent_reasoning_levels("codex")) == provider
    # DSH names the same level `off`, and the value lands in DSH's own config.
    assert allowed_reasoning_levels(provider, agent_reasoning_levels("dsh", "dsh-acp")) == [
        "off",
        "low",
        "high",
        "max",
    ]


def test_level_is_spelled_the_way_the_agent_spells_it() -> None:
    """The value lands in the Agent's own config, so it must use that vocabulary.

    The relay says `none` where DSH says `off`. Returning the provider's word
    for a DSH run would put an invalid value in DSH's settings file.
    """
    assert allowed_reasoning_levels(["none", "high"], ["off", "high"]) == ["off", "high"]
    assert allowed_reasoning_levels(["off", "high"], ["none", "high"]) == ["none", "high"]


def test_wire_protocol_must_be_shared_by_provider_and_agent() -> None:
    """DSH speaks chat-completions only, so a responses-only provider cannot serve it."""
    assert resolve_wire_api({"wire_api": ["responses", "chat"]}, "codex") == "responses"
    assert resolve_wire_api({"wire_api": ["responses", "chat"]}, "dsh", "dsh-acp") == "chat"
    assert resolve_wire_api({"wire_api": ["responses"]}, "dsh", "dsh-acp") is None
    assert resolve_wire_api({"wire_api": ["chat"]}, "codex") == "chat"


def test_unknown_provider_is_rejected(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        {"sub2api": _profile("sub2api", env_file="config/providers/sub2api.env")},
    )
    with pytest.raises(EvalConfigError, match="unknown provider"):
        resolve_run(root, "sample", provider="nope", model="some-model")


def test_unconfigured_provider_is_rejected_before_a_run(tmp_path: Path) -> None:
    """A provider without an env file cannot run; saying so beats a 401 later."""
    root = _repo(
        tmp_path,
        {"deepseek": _profile("deepseek", env_file="config/providers/deepseek.env")},
    )
    with pytest.raises(EvalConfigError, match="is not configured"):
        resolve_run(root, "sample", provider="deepseek", model="deepseek-v4.1-flash")


def test_provider_requires_a_model(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        {"sub2api": _profile("sub2api", env_file="config/providers/sub2api.env")},
        _env(),
    )
    with pytest.raises(EvalConfigError, match="needs a model"):
        resolve_run(root, "sample", provider="sub2api")


def test_illegal_reasoning_is_rejected_before_a_run(tmp_path: Path) -> None:
    """The relay's own enum is the authority, not a hand-written list."""
    root = _repo(
        tmp_path,
        {
            "sub2api": _profile(
                "sub2api",
                env_file="config/providers/sub2api.env",
                wire_api=["responses", "chat"],
                reasoning_levels=["none", "high", "max"],
                default_reasoning="high",
            ),
        },
        _env(),
    )
    with pytest.raises(EvalConfigError, match="not available"):
        resolve_run(root, "sample", provider="sub2api", model="m", reasoning_effort="ultra")


def test_provider_selection_negotiates_protocol_per_agent(tmp_path: Path) -> None:
    """One upstream serves both harnesses, each on the protocol it can speak."""
    root = _repo(
        tmp_path,
        {
            "sub2api": _profile(
                "sub2api",
                env_file="config/providers/sub2api.env",
                wire_api=["responses", "chat"],
                reasoning_levels=["none", "low", "high", "medium"],
                default_reasoning="high",
            )
        },
        _env(),
    )
    codex = resolve_run(root, "sample", provider="sub2api", model="m", agent="codex")
    assert codex.protocol == "responses"
    assert codex.provider == "sub2api"
    assert codex.provider_env_file and codex.provider_env_file.endswith("sub2api.env")
    assert codex.reasoning_effort == "high"

    dsh = resolve_run(root, "sample", provider="sub2api", model="m", agent="dsh")
    assert dsh.protocol == "chat"


def test_model_provider_option_also_selects_a_provider(tmp_path: Path) -> None:
    """The older `--model-provider` spelling reaches the same selection."""
    root = _repo(
        tmp_path,
        {
            "sub2api": _profile(
                "sub2api", env_file="config/providers/sub2api.env", wire_api=["responses"]
            )
        },
        _env(),
    )
    spec = resolve_run(root, "sample", model_provider="sub2api", model="m")
    assert spec.provider == "sub2api"
    assert spec.model == "m"


def test_provider_summary_needs_no_network(tmp_path: Path) -> None:
    """Opening the dialog must not wait on a remote endpoint."""
    root = _repo(
        tmp_path,
        {
            "sub2api": _profile(
                "sub2api",
                env_file="config/providers/sub2api.env",
                models=[{"id": "m1"}, {"id": "m2"}],
                reasoning_levels=["none", "high", "max"],
            )
        },
        _env("http://10.255.255.1"),
    )
    profiles = load_provider_profiles(
        root, {"profile_roots": {"providers": "profiles/providers"}}
    )
    summary = provider_summary(root, profiles["sub2api"], agent="codex", adapter="codex")
    assert summary["configured"] is True
    assert summary["declared_models"] == ["m1", "m2"]
    assert summary["wire_api"] == ["responses"]

def test_model_the_provider_does_not_list_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    """A model name nobody serves must fail at preview, not mid-run.

    This is the failure that motivated the whole provider registry: the repo
    carried a binding for `deepseek/deepseek-v4-flash` long after the relay
    stopped serving it, and the run died at request time instead.
    """
    from ai_native_evals import providers

    root = _repo(
        tmp_path,
        {
            "sub2api": _profile(
                "sub2api",
                env_file="config/providers/sub2api.env",
                wire_api=["responses"],
                reasoning_levels=["none", "high", "max"],
            )
        },
        _env(),
    )
    monkeypatch.setattr(
        providers, "discover_models", lambda *a, **k: (["gpt-5.6-luna"], None)
    )
    with pytest.raises(EvalConfigError, match="does not serve model"):
        resolve_run(root, "sample", provider="sub2api", model="deepseek-v4-flash")


def test_unreachable_provider_does_not_invent_a_verdict(
    tmp_path: Path, monkeypatch
) -> None:
    """When the endpoint cannot be asked, the model is unchecked, not rejected."""
    from ai_native_evals import providers

    root = _repo(
        tmp_path,
        {
            "sub2api": _profile(
                "sub2api",
                env_file="config/providers/sub2api.env",
                wire_api=["responses"],
                reasoning_levels=["none", "high", "max"],
            )
        },
        _env(),
    )
    monkeypatch.setattr(
        providers, "discover_models", lambda *a, **k: ([], "connection refused")
    )
    spec = resolve_run(root, "sample", provider="sub2api", model="anything-at-all")
    assert spec.model == "anything-at-all"


def test_a_level_is_stored_in_the_agents_own_vocabulary(tmp_path: Path) -> None:
    """The resolved value is written into the Agent's config file.

    Asking for `none` on a DSH run must land as `off`, because that is the word
    DSH's settings file accepts; the two mean the same thing.
    """
    root = _repo(
        tmp_path,
        {
            "sub2api": _profile(
                "sub2api",
                env_file="config/providers/sub2api.env",
                wire_api=["responses", "chat"],
                reasoning_levels=["none", "low", "high", "medium"],
                default_reasoning="high",
            )
        },
        _env(),
    )
    codex = resolve_run(
        root, "sample", provider="sub2api", model="m", agent="codex", reasoning_effort="none"
    )
    assert codex.reasoning_effort == "none"

    dsh = resolve_run(
        root, "sample", provider="sub2api", model="m", agent="dsh", reasoning_effort="none"
    )
    assert dsh.reasoning_effort == "off"

    # A level DSH cannot transmit is still refused.
    with pytest.raises(EvalConfigError, match="not available"):
        resolve_run(
            root,
            "sample",
            provider="sub2api",
            model="m",
            agent="dsh",
            reasoning_effort="medium",
        )
