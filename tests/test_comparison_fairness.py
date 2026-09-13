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
