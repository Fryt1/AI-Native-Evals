"""Inspect tasks used to validate the initial package wiring."""

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample

from ai_native_evals.scorers import ai_native_engine_scorer
from ai_native_evals.solvers import mock_agent


@task
def smoke() -> Task:
    """Run one model-free sample through Solver → Scorer."""
    return Task(
        dataset=MemoryDataset(
            [
                Sample(
                    input="Produce a deterministic smoke verdict.",
                    target="succeeded",
                    id="smoke-001",
                )
            ],
            name="ai-native-smoke",
        ),
        solver=mock_agent(),
        scorer=ai_native_engine_scorer(),
        metadata={"suite": "ai-native-evals", "kind": "wiring-smoke"},
    )
