"""What an experiment definition is allowed to say, and what it expands to.

The value of a declarative design is that an invalid one is refused before any
container starts. These tests pin the refusals, because a definition that
silently ignores a key looks exactly like one that honors it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ai_native_evals.experiments.spec import (
    ExperimentError,
    ExperimentSpec,
    load_experiments,
    resolve_experiment,
)


def _write(root: Path, name: str, body: dict) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_text(yaml.safe_dump(body, allow_unicode=True), encoding="utf-8")
    return path


def test_single_axis_expands_to_one_cell_per_value() -> None:
    spec = ExperimentSpec.from_mapping(
        {"id": "e", "task_id": "t", "vary": {"agent": ["codex", "dsh"]}, "repeats": 3}
    )

    assert [cell.selectors for cell in spec.cells] == [
        {"agent": "codex"},
        {"agent": "dsh"},
    ]
    assert spec.attempt_count == 6


def test_two_axes_expand_to_the_cartesian_product() -> None:
    """A hand-written condition list can omit a combination; a product cannot."""
    spec = ExperimentSpec.from_mapping(
        {
            "id": "e",
            "task_id": "t",
            "vary": {"agent": ["codex", "dsh"], "reasoning_effort": ["low", "high"]},
        }
    )

    assert [cell.selectors for cell in spec.cells] == [
        {"agent": "codex", "reasoning_effort": "low"},
        {"agent": "codex", "reasoning_effort": "high"},
        {"agent": "dsh", "reasoning_effort": "low"},
        {"agent": "dsh", "reasoning_effort": "high"},
    ]
    # Stable order, so two runs of the same definition produce the same sequence.
    assert [cell.index for cell in spec.cells] == [0, 1, 2, 3]


def test_no_axes_is_a_single_baseline_cell() -> None:
    spec = ExperimentSpec.from_mapping({"id": "e", "task_id": "t", "repeats": 4})

    assert len(spec.cells) == 1
    assert spec.cells[0].label == "baseline"
    assert spec.attempt_count == 4


def test_fixed_selectors_merge_into_every_cell() -> None:
    spec = ExperimentSpec.from_mapping(
        {
            "id": "e",
            "task_id": "t",
            "vary": {"agent": ["codex"]},
            "fixed": {"model": "m", "reasoning_effort": "high"},
        }
    )

    assert spec.selectors_for(spec.cells[0]) == {
        "model": "m",
        "reasoning_effort": "high",
        "agent": "codex",
    }


def test_unknown_axis_is_refused() -> None:
    """A typo must not silently freeze a value that was meant to vary."""
    with pytest.raises(ExperimentError, match="not a run selector"):
        ExperimentSpec.from_mapping(
            {"id": "e", "task_id": "t", "vary": {"agnet": ["codex"]}}
        )


def test_varying_and_fixing_the_same_selector_is_refused() -> None:
    with pytest.raises(ExperimentError, match="both varies and fixes"):
        ExperimentSpec.from_mapping(
            {
                "id": "e",
                "task_id": "t",
                "vary": {"agent": ["codex"]},
                "fixed": {"agent": "dsh"},
            }
        )


def test_task_id_is_required() -> None:
    with pytest.raises(ExperimentError, match="needs a task_id"):
        ExperimentSpec.from_mapping({"id": "e"})


def test_repeats_must_be_a_positive_integer() -> None:
    with pytest.raises(ExperimentError, match="must be an integer"):
        ExperimentSpec.from_mapping({"id": "e", "task_id": "t", "repeats": "many"})
    with pytest.raises(ExperimentError, match="at least 1"):
        ExperimentSpec.from_mapping({"id": "e", "task_id": "t", "repeats": 0})


def test_empty_axis_is_refused() -> None:
    with pytest.raises(ExperimentError, match="non-empty list"):
        ExperimentSpec.from_mapping({"id": "e", "task_id": "t", "vary": {"agent": []}})


def test_duplicate_axis_value_is_refused() -> None:
    """Running the same configuration twice as two 'cells' would double-count it."""
    with pytest.raises(ExperimentError, match="repeats a value"):
        ExperimentSpec.from_mapping(
            {"id": "e", "task_id": "t", "vary": {"agent": ["codex", "codex"]}}
        )


def test_id_falls_back_to_the_filename(tmp_path: Path) -> None:
    root = tmp_path / "experiments"
    _write(root, "from-filename.yaml", {"task_id": "t"})

    experiments = load_experiments(root)

    assert list(experiments) == ["from-filename"]


def test_a_broken_definition_does_not_hide_the_others(tmp_path: Path) -> None:
    """`experiment list` stays useful with one bad file in the directory."""
    root = tmp_path / "experiments"
    _write(root, "good.yaml", {"id": "good", "task_id": "t"})
    _write(root, "bad.yaml", {"id": "bad", "task_id": "t", "vary": {"nope": ["x"]}})

    experiments = load_experiments(root)

    assert list(experiments) == ["good"]


def test_resolve_reports_the_known_ids_when_asked_for_an_unknown_one(tmp_path: Path) -> None:
    root = tmp_path / "experiments"
    _write(root, "known.yaml", {"id": "known", "task_id": "t"})

    with pytest.raises(ExperimentError, match="known"):
        resolve_experiment(tmp_path, "missing", root=root)


def test_the_shipped_definitions_are_valid() -> None:
    """Every definition committed to `experiments/` must load."""
    repo_root = Path(__file__).parents[1]
    experiments = load_experiments(repo_root / "experiments")

    assert experiments, "no experiment definitions found"
    for experiment_id, spec in experiments.items():
        assert spec.task_id, experiment_id
        assert spec.cells, experiment_id
        assert spec.attempt_count >= 1, experiment_id
