"""Small real-Agent Task used to verify the Codex evaluation path."""

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample

from ai_native_evals.scorers import hello_world_scorer
from ai_native_evals.solvers import codex_agent


@task
def codex_file_smoke() -> Task:
    """Ask Codex to create one file, then check the workspace state."""
    return Task(
        dataset=MemoryDataset(
            [
                Sample(
                    input=(
                        "Create a file named hello.txt in the current workspace. "
                        "Its exact contents must be hello. Do not modify any other file."
                    ),
                    id="codex-file-001",
                )
            ],
            name="ai-native-codex-file-smoke",
        ),
        solver=codex_agent(run_root_override="runs/codex-file-smoke"),
        scorer=hello_world_scorer(
            relative_path="evidence/hello.txt", expected_text="ai-native-codex-ok"
        ),
        metadata={
            "suite": "ai-native-evals",
            "kind": "codex-smoke",
            "run_root": "runs/codex-file-smoke",
        },
    )

