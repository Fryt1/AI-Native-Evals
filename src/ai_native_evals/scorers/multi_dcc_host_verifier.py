"""Independent host read-back scorer for multi-DCC evaluation runs.

The Agent runs inside the Docker sandbox and only talks to the DCCs through
standard MCP. This scorer never trusts Agent prose or its exit code: it opens
the saved ``.blend`` headless with Blender on the host and starts a fresh
unreal-mcp session to read back UE5 world state.

``verify_host_state`` is the synchronous core used by both the Inspect scorer
and the ``ai-native-evals run verify`` CLI gate.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

from inspect_ai.scorer import Score, Scorer, Target, accuracy, scorer
from inspect_ai.solver import TaskState

from .blender_scene import _approx_equal, _find_blender

_SCENE_TOOLSET = "editor_toolset.toolsets.scene.SceneTools"
_ACTOR_TOOLSET = "editor_toolset.toolsets.actor.ActorTools"
_OBJECT_TOOLSET = "editor_toolset.toolsets.object.ObjectTools"


def _fail(answer: str, explanation: str) -> Score:
    return Score(value=False, answer=answer, explanation=explanation, reason=answer)


def _json_roundtrip(value: Any) -> Any:
    return json.loads(json.dumps(value))


@scorer(metrics=[accuracy()], name="multi_dcc_host_verifier")
def multi_dcc_host_verifier(
    *,
    evidence_dir: str | None = None,
    expected_blend_objects: list[dict[str, Any]] | None = None,
    expected_ue5: dict[str, Any] | None = None,
    mcp_url: str | None = None,
    timeout_seconds: int = 60,
    skip_if_absent: bool = True,
) -> Scorer:
    """Score multi-DCC state via independent Blender/UE5 read-back.

    Args:
        evidence_dir: Directory containing saved host artifacts (e.g. a
            ``roundtrip.blend``). Defaults to ``<run_dir>/evidence``.
        expected_blend_objects: Objects expected inside the saved ``.blend``;
            each item supports ``name``, ``type`` and ``location`` keys.
        expected_ue5: Expectations for UE5: ``actor_label``, ``location``,
            ``expected_components`` and optionally ``mesh_ref`` for one
            component.
        mcp_url: Full unreal-mcp endpoint (default
            ``http://127.0.0.1:8000/mcp``).
        timeout_seconds: Timeout for the UE read-back session.
        skip_if_absent: When no UE MCP URL is configured, report ``skipped``
            instead of failing (used by plain Inspect runs).
    """
    evidence_dir = evidence_dir or os.environ.get(
        "AI_NATIVE_EVALS_EVIDENCE_DIR", "evidence"
    )

    async def score(state: TaskState, target: Target) -> Score:
        del target
        run_dir = state.store.get("run_dir")
        if not isinstance(run_dir, str):
            return _fail("scoring_failed", "solver did not publish a run_dir")

        evidence_path = Path(run_dir) / evidence_dir
        passed, findings = verify_host_state(
            evidence_dir=evidence_path,
            expected_blend_objects=expected_blend_objects,
            expected_ue5=expected_ue5,
            mcp_url=mcp_url,
            timeout_seconds=timeout_seconds,
            skip_if_absent=skip_if_absent,
        )
        if passed:
            return Score(
                value=True,
                answer="pass",
                explanation=(
                    "independent host read-back verified Blender/UE5 state"
                    if findings
                    else "no expectations were configured; nothing to verify"
                ),
                metadata={"findings": _json_roundtrip(findings)},
            )
        return _fail(
            "failed",
            "host verification failed: "
            + json.dumps(findings, ensure_ascii=False)[:2000],
        )

    return score


def verify_host_state(
    *,
    evidence_dir: str | Path,
    expected_blend_objects: list[dict[str, Any]] | None = None,
    expected_ue5: dict[str, Any] | None = None,
    mcp_url: str | None = None,
    timeout_seconds: int = 60,
    skip_if_absent: bool = True,
) -> tuple[bool, dict[str, Any]]:
    """Run independent host read-back and return ``(passed, findings)``."""
    evidence_path = Path(evidence_dir)
    findings: dict[str, Any] = {}
    passed = True

    if expected_blend_objects:
        scene_name = os.environ.get("AI_NATIVE_EVALS_BLEND_SCENE_NAME", "roundtrip.blend")
        scene = evidence_path / scene_name
        if not scene.is_file():
            passed = False
            findings["blender"] = {
                "passed": False,
                "answer": "missing_scene",
                "detail": f"saved scene does not exist: {scene}",
            }
        else:
            blender_ok, blender_meta = _verify_blender(scene, expected_blend_objects)
            passed = passed and blender_ok
            findings["blender"] = blender_meta

    ue_url = mcp_url or os.environ.get("AI_NATIVE_EVALS_UE5_MCP_URL")
    if expected_ue5 and ue_url:
        ue_ok, ue_meta = _verify_ue5(ue_url, expected_ue5, timeout_seconds=timeout_seconds)
        passed = passed and ue_ok
        findings["ue5"] = ue_meta
    elif expected_ue5:
        if skip_if_absent:
            findings["ue5"] = {"passed": None, "skipped": "no UE MCP URL configured"}
        else:
            passed = False
            findings["ue5"] = {
                "passed": False,
                "skipped": "no UE MCP URL configured but skip_if_absent=False",
            }

    return passed, findings


# ---------------------------------------------------------------------------
# Blender verification
# ---------------------------------------------------------------------------
def _verify_blender(
    scene: Path, expectations: list[dict[str, Any]]
) -> tuple[bool, dict[str, Any]]:
    """Open a saved .blend headless and check each expected object."""
    executable = _find_blender()
    completed = subprocess.run(
        [
            executable,
            "--background",
            str(scene),
            "--python",
            str(Path(__file__).resolve().parent / "blender_inspect_scene.py"),
            "--",
            "--result-file",
            str(scene.parent / "blender-inspect-result.json"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=180,
    )
    try:
        payload = json.loads(
            (scene.parent / "blender-inspect-result.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        return False, {
            "passed": False,
            "answer": "inspect_failed",
            "detail": f"could not inspect scene: {exc}",
            "stderr": (completed.stderr or "")[-500:],
        }

    details = payload.get("details") if isinstance(payload, dict) else None
    objects = details.get("objects") if isinstance(details, dict) else None
    if not isinstance(objects, list):
        return False, {
            "passed": False,
            "answer": "invalid_result",
            "detail": (
                "inspect returned no objects: "
                + json.dumps(payload, ensure_ascii=False)[:500]
            ),
        }

    checks: list[dict[str, Any]] = []
    for expected in expectations:
        name = expected.get("name")
        matches = [obj for obj in objects if isinstance(obj, dict) and obj.get("name") == name]
        if not matches:
            names = [obj.get("name") for obj in objects if isinstance(obj, dict)]
            checks.append(
                {
                    "expected": expected,
                    "passed": False,
                    "answer": "wrong_object",
                    "detail": f"expected object {name!r}; found {names}",
                }
            )
            continue
        obj = matches[0]
        problems: list[str] = []
        if expected.get("type") and obj.get("type") != expected["type"]:
            problems.append(f"type={obj.get('type')!r} != {expected['type']!r}")
        expected_loc = expected.get("location")
        if expected_loc is not None:
            actual_loc = obj.get("location")
            if not isinstance(actual_loc, list) or len(actual_loc) != 3:
                problems.append(f"object has no valid location: {actual_loc!r}")
            else:
                actual = tuple(float(v) for v in actual_loc)
                wanted = tuple(float(v) for v in expected_loc)
                if not _approx_equal(actual, wanted):
                    problems.append(f"location={actual} != expected {wanted}")
        checks.append(
            {
                "expected": expected,
                "passed": not problems,
                "actual": {
                    "name": obj.get("name"),
                    "type": obj.get("type"),
                    "location": obj.get("location"),
                },
                "problems": problems,
            }
        )

    all_passed = all(check["passed"] for check in checks)
    return all_passed, {
        "passed": all_passed,
        "scene": str(scene),
        "objects": objects,
        "checks": checks,
    }


# ---------------------------------------------------------------------------
# UE5 verification through a fresh MCP session
# ---------------------------------------------------------------------------
def _verify_ue5(
    url: str, expectations: dict[str, Any], *, timeout_seconds: int = 60
) -> tuple[bool, dict[str, Any]]:
    """Start an independent unreal-mcp session and read back world state."""
    session = _UeMcpSession(url, timeout_seconds=timeout_seconds)
    try:
        return session.verify(expectations)
    finally:
        session.close()


class _UeMcpSession:
    """Minimal streamable-HTTP MCP client for the UE MCP plugin."""

    def __init__(self, url: str, *, timeout_seconds: int = 60) -> None:
        self.url = url
        self.timeout = timeout_seconds
        self.session_id: str | None = None
        initialize, sid = self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "ai-native-evals-host-readback", "version": "0.1"},
                },
            }
        )
        self.session_id = sid
        if initialize.get("jsonrpc") != "2.0":
            raise RuntimeError(f"UE MCP initialize failed: {initialize}")

    def _post(self, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
        body = json.dumps(payload).encode()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            sid = response.headers.get("Mcp-Session-Id", "")
            return json.loads(response.read().decode()), sid

    def call(self, toolset: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raw, _ = self._post(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "call_tool",
                    "arguments": {
                        "toolset_name": toolset,
                        "tool_name": tool,
                        "arguments": arguments,
                    },
                },
            }
        )
        return raw

    def close(self) -> None:
        # A read-only tools session needs no further handshake; release the
        # client-side id so a later verify starts a fresh session.
        self.session_id = None

    def verify(self, expectations: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        label = expectations.get("actor_label")
        if not label:
            return False, {
                "passed": False,
                "answer": "invalid_config",
                "detail": "expected_ue5.actor_label is required",
            }

        # tag/collision_channels are required by the UE plugin input schema.
        find = _text_result(
            self.call(
                _SCENE_TOOLSET,
                "find_actors",
                {"name": label, "tag": "", "collision_channels": []},
            )
        )
        actors = find.get("returnValue")
        if not isinstance(actors, list) or not actors:
            return False, {
                "passed": False,
                "answer": "actor_missing",
                "detail": f"find_actors returned no actor for {label!r}",
            }
        actor_ref = str(actors[0].get("refPath", ""))
        problems: list[str] = []
        checks: dict[str, Any] = {
            "actor_ref": actor_ref,
            "actor_label": label,
        }

        expected_loc = expectations.get("location")
        if expected_loc is not None:
            transform = _text_result(
                self.call(
                    _ACTOR_TOOLSET, "get_actor_transform", {"actor": {"refPath": actor_ref}}
                )
            ).get("returnValue", {})
            loc = transform.get("location") if isinstance(transform, dict) else None
            if not isinstance(loc, dict):
                problems.append(f"actor transform has no location: {transform!r}")
            else:
                actual = (loc.get("x"), loc.get("y"), loc.get("z"))
                wanted = (expected_loc.get("x"), expected_loc.get("y"), expected_loc.get("z"))
                try:
                    numeric_actual = tuple(float(v) for v in actual)
                    numeric_wanted = tuple(float(v) for v in wanted)
                    if not all(
                        abs(a - b) <= 1e-3
                        for a, b in zip(numeric_actual, numeric_wanted, strict=True)
                    ):
                        problems.append(f"location={actual} != expected {wanted}")
                except (TypeError, ValueError):
                    problems.append(f"location values are not numeric: {actual!r}")
            checks["transform_location"] = loc

        components = _text_result(
            self.call(_ACTOR_TOOLSET, "get_components", {"actor": {"refPath": actor_ref}})
        ).get("returnValue")
        component_refs = []
        component_names = []
        if isinstance(components, list):
            for component in components:
                ref = str(component.get("refPath", ""))
                component_refs.append(ref)
                component_names.append(ref.rsplit(".", 1)[-1])
        for expected_component in expectations.get("expected_components", []):
            if expected_component not in component_names:
                problems.append(
                    f"missing component {expected_component!r}; found {component_names}"
                )
        checks["components"] = component_refs

        mesh_ref = expectations.get("mesh_ref")
        mesh_component = expectations.get("mesh_component")
        if mesh_ref:
            mesh_ok = False
            mesh_detail = ""
            candidates = (
                [ref for ref in component_refs if ref.endswith(f".{mesh_component}")]
                if mesh_component
                else component_refs
            )
            for ref in candidates:
                props = _text_result(
                    self.call(
                        _OBJECT_TOOLSET,
                        "get_properties",
                        {"instance": {"refPath": ref}, "properties": ["StaticMesh"]},
                    )
                ).get("returnValue")
                if mesh_ref in str(props):
                    mesh_ok = True
                    mesh_detail = f"on {ref}"
                    break
            if not mesh_ok:
                problems.append(
                    f"no component references {mesh_ref!r} (checked {candidates or component_refs})"
                )
            else:
                checks["mesh_ref"] = {"ref": mesh_ref, "found_on": mesh_detail}

        passed = not problems
        return passed, {
            "passed": passed,
            "answer": "pass" if passed else "failed",
            "problems": problems,
            "checks": checks,
        }


def _text_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract the JSON object inside an MCP tool result text payload."""
    content = raw.get("result", {}).get("content", [])
    texts = [item.get("text", "") for item in content if isinstance(item, dict)]
    joined = "".join(texts)
    try:
        parsed = json.loads(joined)
    except (json.JSONDecodeError, TypeError):
        return {"returnValue": None}
    return parsed if isinstance(parsed, dict) else {"returnValue": None}
