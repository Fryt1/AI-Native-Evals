"""Inspect scorer that verifies a Blender scene by reading the saved .blend."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from inspect_ai.scorer import Score, Scorer, Target, accuracy, scorer
from inspect_ai.solver import TaskState

from ..adapters.codex_events import _compact


def _find_blender() -> str:
    configured = os.environ.get("BLENDER_EXECUTABLE", r"E:\blender\blender.exe")
    if Path(configured).is_file():
        return str(Path(configured))
    return "blender"


@scorer(metrics=[accuracy()], name="blender_scene_scorer")
def blender_scene_scorer(
    relative_scene_path: str = "scene.blend",
    expected_name: str | None = None,
    expected_location: tuple[float, float, float] | None = None,
    blend_executable: str | None = None,
) -> Scorer:
    """Open the saved .blend with Blender and verify the scene state."""

    async def score(state: TaskState, target: Target) -> Score:
        del target
        run_dir = state.store.get("run_dir")
        if not isinstance(run_dir, str):
            return _fail("scoring_failed", "solver did not publish a run_dir")
        scene = Path(run_dir) / relative_scene_path
        if not scene.is_file():
            return _fail("missing_scene", f"saved scene does not exist: {scene}")

        executable = blend_executable or _find_blender()
        completed = await _run_blender_inspect(executable, scene)
        payload, error = _parse_result(completed, scene.parent)
        if error is not None:
            return _fail(
                "inspect_failed",
                f"could not inspect scene: {error}; stderr={_compact(completed.stderr)}",
            )

        details = payload.get("details") if isinstance(payload, dict) else None
        objects = details.get("objects") if isinstance(details, dict) else None
        if not isinstance(objects, list):
            return _fail("invalid_result", f"inspect returned no objects: {_compact(details)}")

        if expected_name is not None:
            matches = [
                obj for obj in objects if isinstance(obj, dict) and obj.get("name") == expected_name
            ]
            if not matches:
                names = [obj.get("name") for obj in objects if isinstance(obj, dict)]
                return _fail("wrong_object", f"expected object {expected_name!r}; found {names}")
            obj = matches[0]
        else:
            if not objects:
                return _fail("no_objects", "scene contains no objects")
            obj = objects[0]

        if expected_location is not None:
            location = obj.get("location")
            if not isinstance(location, list) or len(location) != 3:
                return _fail("missing_location", f"object has no valid location: {location!r}")
            actual = tuple(float(value) for value in location)
            expected = tuple(float(value) for value in expected_location)
            if not _approx_equal(actual, expected):
                return _fail(
                    "wrong_location",
                    f"expected location {expected}, actual {actual}",
                )

        explanation = (
            f"Blender scene verified: object={obj.get('name')} "
            f"location={_compact(obj.get('location'))}"
        )
        return Score(
            value=True,
            answer="pass",
            explanation=explanation,
            metadata={
                "scene_path": str(scene),
                "objects": objects,
                "expected_name": expected_name,
                "expected_location": list(expected_location) if expected_location else None,
                "blender_executable": executable,
            },
        )

    return score


async def _run_blender_inspect(
    executable: str, scene: Path
) -> subprocess.CompletedProcess[str]:
    """Run Blender with the inspect script against the saved scene."""
    script = Path(__file__).resolve().with_name("blender_inspect_scene.py")
    command = [
        executable,
        "--background",
        str(scene),
        "--python",
        str(script),
        "--",
        "--result-file",
        str(scene.parent / "blender-inspect-result.json"),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=180,
    )
    return completed


def _parse_result(
    completed: subprocess.CompletedProcess[str], scene_dir: Path
) -> tuple[dict[str, Any] | None, str | None]:
    """Return the JSON result and an error message when parsing fails."""
    result_file = scene_dir / "blender-inspect-result.json"
    try:
        payload = json.loads(result_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "Blender inspect did not produce a result file"
    except OSError as exc:
        return None, f"could not read Blender inspect result: {exc}"
    except json.JSONDecodeError as exc:
        return None, f"Blender inspect result is not valid JSON: {exc}"
    if not isinstance(payload, dict):
        return None, "Blender inspect result must be an object"
    return payload, None


def _approx_equal(left: tuple[float, float, float], right: tuple[float, float, float]) -> bool:
    return all(abs(a - b) <= 1e-4 for a, b in zip(left, right, strict=True))


def _fail(answer: str, explanation: str) -> Score:
    return Score(value=False, answer=answer, explanation=explanation, reason=answer)
