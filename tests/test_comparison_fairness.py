"""The fairness check must see the upstream and the reasoning level."""

from __future__ import annotations

from ai_native_evals_console.read_models import _comparison_fingerprint


def _manifest(**run_overrides):
    run = {
        "task_id": "t",
        "task_bundle": {"id": "t"},
        "test_plan": {"version": 1},
        "model_profile": "p",
        "model": "m",
        "model_provider": "eval",
        "provider": "sub2api",
        "provider_env_file": "config/.env.local",
        "reasoning_effort": "high",
        "mcp_profile": "none",
        "mcp_servers": {},
        "sandbox_profile": "docker-default",
        "sandbox": {"read_only_root": True},
        "resource_specs": [],
    }
    run.update(run_overrides)
    return {"run": run}


def test_identical_inputs_share_a_fingerprint() -> None:
    assert _comparison_fingerprint(_manifest()) == _comparison_fingerprint(_manifest())


def test_switching_provider_breaks_the_fingerprint() -> None:
    """Two upstreams are two different measurements, not a fair comparison."""
    other = _manifest(provider="deepseek", provider_env_file="config/providers/deepseek.env")
    assert _comparison_fingerprint(_manifest()) != _comparison_fingerprint(other)


def test_switching_reasoning_breaks_the_fingerprint() -> None:
    assert _comparison_fingerprint(_manifest()) != _comparison_fingerprint(
        _manifest(reasoning_effort="max")
    )


def test_switching_only_the_agent_keeps_the_fingerprint() -> None:
    """Comparing two Agents must not be reported as an unfair comparison.

    The fingerprint deliberately omits `agent`: comparing Agents is the point of
    the tool. But the resolver used to set `model_provider` to `"eval"` for the
    DSH adapter only -- a value no DSH entry point read, while the Codex renderer
    already defaulted to it -- so that field silently carried Agent identity into
    the fingerprint and every Codex-vs-DSH comparison was flagged "inputs differ".

    This drives the real resolver, so the assertion is about what a run actually
    records rather than about a hand-written manifest.
    """
    from pathlib import Path

    from ai_native_evals.runs import resolve_run

    repo_root = Path(__file__).parents[1]
    fingerprints = {
        agent: _comparison_fingerprint(
            {"run": resolve_run(repo_root, "codex-file-smoke", agent=agent).to_dict()}
        )
        for agent in ("codex", "dsh-release")
    }

    assert fingerprints["codex"] == fingerprints["dsh-release"], (
        "the two Agents differ in a frozen input; only `agent` is allowed to differ"
    )
