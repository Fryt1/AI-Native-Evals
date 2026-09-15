"""The fairness check must see the upstream and the reasoning level."""

from __future__ import annotations

from pathlib import Path

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


def test_a_declared_varying_axis_is_not_an_unfairness() -> None:
    """A sweep that varies an axis must not be flagged as a broken invariant.

    The fingerprint exists to catch a run differing in an input the design meant
    to hold constant. When the design *says* it varies `reasoning_effort`, a
    difference there is the experiment working -- and reporting it as unfair
    would flag every well-formed sweep, which is how a real warning gets ignored.
    """
    low = _manifest(reasoning_effort="low")
    high = _manifest(reasoning_effort="high")

    assert _comparison_fingerprint(low) != _comparison_fingerprint(high)
    assert _comparison_fingerprint(low, varying={"reasoning_effort"}) == (
        _comparison_fingerprint(high, varying={"reasoning_effort"})
    )


def test_a_varying_axis_does_not_hide_a_real_difference() -> None:
    """Excluding the varying axis must not blind the check to everything else."""
    left = _manifest(reasoning_effort="low")
    right = _manifest(reasoning_effort="high", provider="deepseek")

    assert _comparison_fingerprint(left, varying={"reasoning_effort"}) != (
        _comparison_fingerprint(right, varying={"reasoning_effort"})
    )


def _agent_repo(tmp_path: Path) -> Path:
    """A minimal repository holding two Agent profiles, and nothing else.

    Built here rather than pointed at the checkout on purpose: resolving against
    the real repository requires `config/.env.local`, which is gitignored. A test
    that needs a developer's credentials passes locally and fails on a fresh
    clone -- which is exactly what happened when CI was first added.
    """
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "eval.yaml").write_text(
        "version: 1\n"
        "task_roots: [tasks]\n"
        "profile_roots:\n"
        "  agents: profiles/agents\n"
        "  models: profiles/models\n"
        "  mcp: profiles/mcp\n"
        "paths:\n"
        "  runs_root: EvalRuns\n"
        "defaults:\n"
        "  agent: codex\n"
        "  model_profile: test-model\n"
        "  mcp_profile: none\n",
        encoding="utf-8",
    )
    profiles = {
        "profiles/agents/codex.yaml": "id: codex\nadapter: codex\nimage: codex-image\n",
        "profiles/agents/dsh-release.yaml": (
            "id: dsh-release\nadapter: dsh-acp\nprotocol: acp\nimage: dsh-image\n"
        ),
        "profiles/models/test-model.yaml": "id: test-model\nmodel: test/model\n",
        "profiles/mcp/none.yaml": "id: none\nservers: {}\n",
    }
    for relative, body in profiles.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    task = tmp_path / "tasks" / "demo"
    task.mkdir(parents=True)
    (task / "task.yaml").write_text(
        "id: demo\nversion: 1\nprompt_file: prompt.md\nresources: []\n", encoding="utf-8"
    )
    (task / "prompt.md").write_text("Do the thing.", encoding="utf-8")
    return tmp_path


def test_switching_only_the_agent_keeps_the_fingerprint(tmp_path: Path) -> None:
    """Comparing two Agents must not be reported as an unfair comparison.

    The fingerprint deliberately omits `agent`: comparing Agents is the point of
    the tool. But the resolver used to set `model_provider` to `"eval"` for the
    DSH adapter only -- a value no DSH entry point read, while the Codex renderer
    already defaulted to it -- so that field silently carried Agent identity into
    the fingerprint and every Codex-vs-DSH comparison was flagged "inputs differ".

    This drives the real resolver, so the assertion is about what a run actually
    records rather than about a hand-written manifest.
    """
    from ai_native_evals.runs import resolve_run

    repo_root = _agent_repo(tmp_path)
    fingerprints = {
        agent: _comparison_fingerprint(
            {"run": resolve_run(repo_root, "demo", agent=agent).to_dict()}
        )
        for agent in ("codex", "dsh-release")
    }

    assert fingerprints["codex"] == fingerprints["dsh-release"], (
        "the two Agents differ in a frozen input; only `agent` is allowed to differ"
    )
