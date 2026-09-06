"""Verify that the first Inspect Task is assembled correctly."""

from ai_native_evals.tasks.smoke import smoke


def test_smoke_task_has_dataset_solver_and_scorer() -> None:
    evaluation_task = smoke()

    assert evaluation_task.dataset is not None
    assert len(evaluation_task.dataset) == 1
    assert evaluation_task.solver is not None
    assert evaluation_task.scorer is not None
