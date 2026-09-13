"""Model capability marking, and the Agent labels the picker shows.

`/v1/models` answers "what can be called", not "what can hold a conversation and
call tools". A relay commonly lists image and embedding models beside chat
models, and choosing one produces a run that fails after the container has
already started. These tests pin the classification and its deliberate
conservatism.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ai_native_evals.providers import model_capability
from ai_native_evals_console.read_models import _list_yaml_profiles


@pytest.mark.parametrize(
    "model_id",
    [
        "gpt-5.6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-6-astra",
        "deepseek/deepseek-v4.1-flash",
        "deepseek/deepseek-v4-pro",
        "claude-sonnet-5",
    ],
)
def test_chat_models_are_chat(model_id: str) -> None:
    assert model_capability(model_id) == "chat"


@pytest.mark.parametrize("model_id", ["gpt-image-1", "gpt-image-1.5", "gpt-image-2", "dall-e-3"])
def test_image_models_are_excluded(model_id: str) -> None:
    assert model_capability(model_id) == "image"


@pytest.mark.parametrize(
    "model_id",
    ["text-embedding-3-large", "whisper-1", "bge-rerank-v2", "tts-1", "omni-moderation-latest"],
)
def test_non_chat_models_are_excluded(model_id: str) -> None:
    assert model_capability(model_id) == "other"


def test_an_unrecognised_name_stays_chat() -> None:
    """Wrongly hiding a working model is worse than showing a suspicious one.

    The operator can always read the id; a model that vanishes cannot be chosen
    at all.
    """
    assert model_capability("some-new-frontier-model") == "chat"
    assert model_capability("internal-model-v7") == "chat"


def test_classification_is_case_insensitive() -> None:
    assert model_capability("GPT-Image-1") == "image"
    assert model_capability("TEXT-EMBEDDING-3") == "other"


# --- Agent labels ------------------------------------------------------------


def _profile(tmp_path: Path, name: str, payload: dict) -> Path:
    root = tmp_path / "agents"
    root.mkdir(exist_ok=True)
    (root / f"{name}.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return root


def test_agent_label_and_capabilities_reach_the_console(tmp_path: Path) -> None:
    """A picker showing a bare `codex-dcc` tells the operator nothing."""
    root = _profile(
        tmp_path,
        "codex-dcc",
        {
            "id": "codex-dcc",
            "label": "Codex（含 Blender / UE5）",
            "description": "可通过 MCP 操作宿主机 DCC。",
            "adapter": "codex",
            "workdir": "/workspace/game-engine",
            "capabilities": ["filesystem", "shell", "mcp", "blender", "ue5"],
        },
    )

    entry = _list_yaml_profiles(root, "agent")[0]

    assert entry["label"] == "Codex（含 Blender / UE5）"
    assert entry["summary"] == "可通过 MCP 操作宿主机 DCC。"
    assert entry["capabilities"] == ["filesystem", "shell", "mcp", "blender", "ue5"]
    assert entry["workdir"] == "/workspace/game-engine"


def test_a_profile_without_a_label_falls_back_to_its_id(tmp_path: Path) -> None:
    """An unlabelled profile must still appear, just under its id."""
    root = _profile(tmp_path, "plain", {"id": "plain", "adapter": "codex"})

    entry = _list_yaml_profiles(root, "agent")[0]

    assert entry["label"] == "plain"
    assert entry["summary"] == "codex"


def test_agent_entry_survives_a_missing_capabilities_key(tmp_path: Path) -> None:
    root = _profile(tmp_path, "bare", {"id": "bare", "adapter": "codex"})

    entry = _list_yaml_profiles(root, "agent")[0]

    assert entry["capabilities"] == []
    assert entry["workdir"] == ""


def test_adapter_only_profiles_still_report_a_summary(tmp_path: Path) -> None:
    """Every shipped Agent profile carries a description; this is the fallback."""
    root = _profile(tmp_path, "x", {"id": "x", "adapter": "dsh-acp"})

    assert _list_yaml_profiles(root, "agent")[0]["summary"] == "dsh-acp"


def test_model_entries_keep_their_existing_shape(tmp_path: Path) -> None:
    """The model branch must not be disturbed by the agent additions."""
    root = _profile(tmp_path, "m", {"id": "m", "provider": "p", "model": "real-model"})

    entry = _list_yaml_profiles(root, "model")[0]

    assert entry["provider_id"] == "p"
    assert entry["model_id"] == "real-model"
    assert "capabilities" not in entry
