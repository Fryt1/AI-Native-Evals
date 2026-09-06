"""Inspect Task that drives a real sandboxed Agent across Blender and UE5.

The task prompt is the *only* thing visible to the Agent; every expected host
state lives in the scorer so it can be independently verified after the run.
Because the scorer needs the live DCCs it is not meant to run through the plain
Inspect viewer; it is exercised by the Docker run lifecycle in
``docs/MULTI_DCC_ROUNDTRIP_RESULT.md``. The factory keeps the prompt and
expectations in one place so the manifest task and this Task cannot drift.
"""

from __future__ import annotations

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample

from ai_native_evals.scorers import multi_dcc_host_verifier


def multi_dcc_roundtrip_prompt() -> str:
    """The exact Agent prompt used for the multi-DCC round-trip task."""
    return (
        "Work across two DCCs through MCP and leave verifiable state on the host.\n"
        "Blender (server 'blender'): create an object named RoundtripCube (a unit cube) "
        "at (0,0,1). Then save the current Blender scene to a file named "
        "roundtrip.blend inside the evidence directory (the Windows host path that is "
        "mounted into this container at /workspace/evidence, usually "
        "D:\\work\\AI-Native\\EvalRuns\\<run-id>\\evidence) and call save/export so the "
        "file exists.\n"
        "UE5 (server 'unreal-mcp'): in the current level create a StaticMeshActor named "
        "RoundtripActor at (0,0,100), and add a cube StaticMeshComponent named "
        "RoundtripMesh using the engine's built-in cube mesh.\n"
        "Finally write /workspace/evidence/roundtrip-summary.json containing both the "
        "saved blender file path and the actor ref from UE5, then report done."
    )


@task
def multi_dcc_roundtrip() -> Task:
    """Ask a sandboxed Agent to create objects in Blender and UE5."""
    return Task(
        dataset=MemoryDataset(
            [
                Sample(
                    input=multi_dcc_roundtrip_prompt(),
                    id="multi-dcc-roundtrip-001",
                )
            ],
            name="ai-native-multi-dcc-roundtrip",
        ),
        solver=None,
        scorer=multi_dcc_host_verifier(
            evidence_dir="evidence",
            expected_blend_objects=[
                {"name": "RoundtripCube", "type": "MESH", "location": (0.0, 0.0, 1.0)}
            ],
            expected_ue5={
                "actor_label": "RoundtripActor",
                "location": {"x": 0.0, "y": 0.0, "z": 100.0},
                "expected_components": ["StaticMeshComponent0", "RoundtripMesh"],
                "mesh_ref": "/Engine/BasicShapes/Cube.Cube",
            },
        ),
        metadata={
            "suite": "ai-native-evals",
            "kind": "multi-dcc-roundtrip",
            "run_root": "roundtrip-blender-ue5",
        },
    )
