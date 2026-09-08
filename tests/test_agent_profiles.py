"""Tests for the provider-neutral Agent profile seam."""

from ai_native_evals.agents import AgentProfile, available_adapters, create_adapter


def test_profile_selects_adapter_without_importing_docker_or_task_code() -> None:
    profile = AgentProfile.from_mapping(
        "codex",
        {"adapter": "codex", "image": "test-codex", "workdir": "/workspace"},
    )

    adapter = create_adapter(profile)

    assert adapter.adapter_id == "codex"
    assert {"codex", "dsh-acp"}.issubset(set(available_adapters()))


def test_profile_serializes_runtime_contract() -> None:
    profile = AgentProfile.from_mapping(
        "dsh",
        {
            "adapter": "dsh-acp",
            "image": "test-dsh",
            "protocol": "acp",
            "entrypoint": "node",
            "system_prompt": "follow the run contract",
            "command": ["/run-config/client.mjs", "${TASK_PROMPT}"],
            "writable_paths": ["/tmp/dsh-home"],
        },
    )

    assert profile.to_dict()["entrypoint"] == "node"
    assert profile.to_dict()["system_prompt"] == "follow the run contract"
    assert profile.to_dict()["command"] == ["/run-config/client.mjs", "${TASK_PROMPT}"]
    assert profile.to_dict()["writable_paths"] == ["/tmp/dsh-home"]
