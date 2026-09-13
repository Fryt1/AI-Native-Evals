"""The Blender Task, and the host-service boundary it has to respect.

Two things were learned by running it, and both are pinned here:

* a Markdown Task prompt passed as a single `docker run` argument loses its
  structure -- a table's rows disappeared, and the Agent reported the missing
  names as an ambiguity in the request rather than as a delivery fault;
* Blender is a host service, so its `/workspace` is its own working directory,
  not the container path of the same name. A check that reads the live scene
  over MCP avoids depending on how the host was started.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_native_evals.scorers.multi_dcc_host_verifier import (
    _match_expected_objects,
    _verify_blender_live,
)

REPO = Path(__file__).resolve().parents[1]
TASK = REPO / "tasks" / "blender-scene-build"


# --- the live read-back comparison -------------------------------------------


def _scene(*objects: dict) -> list[dict]:
    return list(objects)


def test_a_matching_scene_passes() -> None:
    scene = _scene(
        {"name": "EvalTable", "type": "MESH", "location": [0.0, 0.0, 0.75]},
        {"name": "EvalLamp", "type": "LIGHT", "location": [2.0, 0.0, 3.0]},
    )
    expectations = [
        {"name": "EvalTable", "type": "MESH", "location": [0.0, 0.0, 0.75]},
        {"name": "EvalLamp", "type": "LIGHT", "location": [2.0, 0.0, 3.0]},
    ]

    passed, findings = _match_expected_objects(scene, expectations)

    assert passed is True
    assert all(check["passed"] for check in findings["checks"])


def test_a_missing_object_fails_and_names_what_was_found() -> None:
    """A failure has to say what the scene actually held."""
    passed, findings = _match_expected_objects(
        _scene({"name": "Cube", "type": "MESH", "location": [0.0, 0.0, 0.0]}),
        [{"name": "EvalTable", "type": "MESH", "location": [0.0, 0.0, 0.75]}],
    )

    assert passed is False
    assert "Cube" in findings["checks"][0]["detail"]


def test_a_wrong_location_fails() -> None:
    passed, findings = _match_expected_objects(
        _scene({"name": "EvalTable", "type": "MESH", "location": [9.0, 9.0, 9.0]}),
        [{"name": "EvalTable", "type": "MESH", "location": [0.0, 0.0, 0.75]}],
    )

    assert passed is False
    assert "location" in findings["checks"][0]["problems"][0]


def test_a_wrong_kind_fails() -> None:
    """A light where a mesh was asked for is not the requested object."""
    passed, findings = _match_expected_objects(
        _scene({"name": "EvalTable", "type": "LIGHT", "location": [0.0, 0.0, 0.75]}),
        [{"name": "EvalTable", "type": "MESH", "location": [0.0, 0.0, 0.75]}],
    )

    assert passed is False
    assert "type" in findings["checks"][0]["problems"][0]


def test_a_near_miss_location_is_accepted() -> None:
    """Floating point and unit rounding must not fail an otherwise correct scene."""
    passed, _ = _match_expected_objects(
        _scene({"name": "EvalTable", "type": "MESH", "location": [0.0001, 0.0, 0.7501]}),
        [{"name": "EvalTable", "type": "MESH", "location": [0.0, 0.0, 0.75]}],
    )

    assert passed is True


def test_leftover_objects_fail_a_clean_scene_expectation() -> None:
    """Having the right objects and the leftovers of a first attempt is not a
    clean result."""
    passed, findings = _match_expected_objects(
        _scene(
            {"name": "EvalTable", "type": "MESH", "location": [0.0, 0.0, 0.75]},
            {"name": "Cube", "type": "MESH", "location": [0.0, 0.0, 0.0]},
        ),
        [{"name": "EvalTable", "type": "MESH", "location": [0.0, 0.0, 0.75]}],
        absent=["Cube"],
    )

    assert passed is False
    assert any(check.get("answer") == "unexpected_object" for check in findings["checks"])


def test_absent_names_that_are_gone_pass() -> None:
    passed, _ = _match_expected_objects(
        _scene({"name": "EvalTable", "type": "MESH", "location": [0.0, 0.0, 0.75]}),
        [{"name": "EvalTable", "type": "MESH", "location": [0.0, 0.0, 0.75]}],
        absent=["Cube", "Light", "Camera"],
    )

    assert passed is True


def test_an_empty_scene_cannot_pass_a_non_empty_expectation() -> None:
    """An empty check list must not read as success."""
    passed, _ = _match_expected_objects(_scene(), [])

    assert passed is False


def test_unreachable_blender_reports_unreachable_rather_than_failing_the_scene() -> None:
    """A probe that could not connect must not be reported as a bad scene."""
    passed, findings = _verify_blender_live(
        "127.0.0.1", 1, [{"name": "x", "type": "MESH"}], timeout_seconds=1
    )

    assert passed is False
    assert findings["answer"] == "unreachable"


# --- the task bundle ---------------------------------------------------------


def _task() -> dict:
    import yaml

    return yaml.safe_load((TASK / "task.yaml").read_text(encoding="utf-8"))


def test_the_task_grades_the_live_scene_not_a_saved_file() -> None:
    """A saved `.blend` lands on the host, where the run cannot read it."""
    evaluators = [check["evaluator"] for check in _task()["test_plan"]["checks"]]

    assert "script.blender_live_scene.v1" in evaluators
    assert "script.blender_scene.v1" not in evaluators


def test_the_task_needs_no_repository_snapshot() -> None:
    """A Task with no resources must not create a Game Engine snapshot."""
    assert _task().get("resources") == []


def test_the_task_depends_only_on_blender() -> None:
    """Declaring unreal-mcp would make a Blender task require a UE5 editor."""
    import yaml

    preset = yaml.safe_load(
        (REPO / "config" / "presets" / "blender-codex.yaml").read_text(encoding="utf-8")
    )
    profile = yaml.safe_load(
        (REPO / "profiles" / "mcp" / f"{preset['mcp_profile']}.yaml").read_text(encoding="utf-8")
    )

    assert set(profile.get("servers", {})) == {"blender"}


def test_the_agent_profile_works_without_a_project_mount() -> None:
    """Its workdir must exist in a run that mounts no project."""
    import yaml

    preset = yaml.safe_load(
        (REPO / "config" / "presets" / "blender-codex.yaml").read_text(encoding="utf-8")
    )
    profile = yaml.safe_load(
        (REPO / "profiles" / "agents" / f"{preset['agent']}.yaml").read_text(encoding="utf-8")
    )

    assert profile["workdir"] == "/workspace"


def test_the_prompt_names_every_object_the_check_expects() -> None:
    """The subject must be told what is expected; the criteria stay implicit."""
    prompt = (TASK / "prompt.md").read_text(encoding="utf-8")
    expected = [
        obj["name"]
        for check in _task()["test_plan"]["checks"]
        if check["evaluator"] == "script.blender_live_scene.v1"
        for obj in check["config"].get("objects", [])
    ]

    assert expected
    for name in expected:
        assert name in prompt, name


def test_the_prompt_asks_for_the_defaults_to_be_removed() -> None:
    """`scene-clean` checks for their absence, so the prompt must require it."""
    prompt = (TASK / "prompt.md").read_text(encoding="utf-8").lower()

    assert "default" in prompt
    assert "remove" in prompt or "nothing else" in prompt


def test_the_rubric_criteria_are_answerable_from_the_read_back() -> None:
    """A criterion the evidence cannot answer is a gap, not a quality failure.

    The first version asked about table shape and light energy while the
    read-back collected only name, kind and origin; the Judge could honestly
    answer nothing but "unverifiable", which scored as a quality failure.
    """
    import yaml

    rubric = yaml.safe_load((TASK / "rubric.yaml").read_text(encoding="utf-8"))
    criteria = " ".join(item["description"].lower() for item in rubric["criteria"])

    # The read-back must collect the fields these criteria talk about.
    if "dimension" in criteria or "wider than" in criteria or "shaped" in criteria:
        assert "dimensions" in _live_probe_code()
    if "energy" in criteria or "light" in criteria:
        assert "energy" in _live_probe_code()


def _live_probe_code() -> str:
    """The source of the read-back probe, as text."""
    import importlib
    import inspect

    module = importlib.import_module("ai_native_evals.scorers.multi_dcc_host_verifier")
    return inspect.getsource(module._verify_blender_live)


@pytest.mark.parametrize("field", ["dimensions", "scale", "light", "energy"])
def test_the_read_back_collects_what_a_rubric_may_ask(field: str) -> None:
    assert field in _live_probe_code()


def test_the_task_judges_the_read_back_artifact() -> None:
    """The quality check must point at the evidence file, not a findings dict."""
    quality = next(
        check for check in _task()["test_plan"]["checks"] if check["id"] == "build-quality"
    )

    assert quality["input"]["artifact"] == "scene-objects.selected_artifact"
    assert quality["depends_on"] == ["scene-objects", "scene-clean"]


def test_the_task_declares_a_rubric_and_a_quality_prompt() -> None:
    task = _task()
    quality = next(
        check for check in task["test_plan"]["checks"] if check["id"] == "build-quality"
    )

    assert (TASK / quality["config"]["rubric"]).is_file()
    assert (REPO / quality["config"]["prompt"]).is_file()
    assert (TASK / task["prompt_file"]).is_file()


def test_the_read_back_result_is_json_serialisable() -> None:
    """Evidence is written to disk, so it must survive a round trip."""
    _passed, findings = _match_expected_objects(
        _scene({"name": "EvalTable", "type": "MESH", "location": [0.0, 0.0, 0.75]}),
        [{"name": "EvalTable", "type": "MESH", "location": [0.0, 0.0, 0.75]}],
    )

    assert json.loads(json.dumps(findings)) == findings
