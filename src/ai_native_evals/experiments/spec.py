"""Declarative experiment definitions: what varies, what is frozen, how often.

`compare --agents a,b` is one experiment with one varying axis. Real evaluation
questions are not all shaped that way -- "is the run stable across attempts?",
"does the result depend on the provider?" -- and expressing those by adding
flags to a comparison command produces a flag per axis and a command that cannot
say what it held fixed.

An experiment states the design instead:

    task: codex-file-smoke
    vary:
      agent: [codex, dsh-release]
      provider: [sub2api]
    fixed:
      model: gpt-5.6-luna
      reasoning_effort: high
    repeats: 5

The axes are the Cartesian product, so `vary` with two keys produces every
combination rather than a hand-written list that can silently omit one. Every
key that is *not* under `vary` is frozen and recorded in the manifest: what an
experiment held constant is as much a part of its result as what it changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from pathlib import Path
from typing import Any

import yaml

#: Selectors a run resolves, in the order they reach `resolve_run`. An axis must
#: name one of these, so a typo is refused at load time rather than silently
#: freezing a value that was meant to vary.
VARYABLE = (
    "agent",
    "model_profile",
    "provider",
    "model",
    "reasoning_effort",
    "mcp_profile",
    "sandbox_profile",
    "preset",
    "game_engine_ref",
    "dsh_ref",
)


class ExperimentError(ValueError):
    """Raised when an experiment definition cannot be used."""


@dataclass(frozen=True, slots=True)
class Cell:
    """One combination of axis values: the unit that is repeated N times."""

    index: int
    selectors: dict[str, str]

    @property
    def label(self) -> str:
        """A stable human label, e.g. ``agent=codex · provider=sub2api``."""
        if not self.selectors:
            return "baseline"
        return " · ".join(f"{key}={self.selectors[key]}" for key in sorted(self.selectors))

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index, "label": self.label, "selectors": dict(self.selectors)}


@dataclass(frozen=True, slots=True)
class ExperimentSpec:
    """A validated experiment design."""

    experiment_id: str
    task_id: str
    vary: dict[str, tuple[str, ...]]
    fixed: dict[str, str] = field(default_factory=dict)
    repeats: int = 1
    description: str = ""
    source_path: Path | None = None

    @property
    def cells(self) -> tuple[Cell, ...]:
        """Every combination of the varying axes, in a stable order.

        Sorted axis names keep the order deterministic, so two runs of the same
        definition produce the same cells in the same sequence.
        """
        if not self.vary:
            return (Cell(index=0, selectors={}),)
        keys = sorted(self.vary)
        cells = []
        for index, combination in enumerate(product(*(self.vary[key] for key in keys))):
            cells.append(Cell(index=index, selectors=dict(zip(keys, combination, strict=True))))
        return tuple(cells)

    @property
    def attempt_count(self) -> int:
        """Total runs this experiment will execute."""
        return len(self.cells) * self.repeats

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.experiment_id,
            "task_id": self.task_id,
            "description": self.description,
            "vary": {key: list(values) for key, values in self.vary.items()},
            "fixed": dict(self.fixed),
            "repeats": self.repeats,
            "cells": [cell.to_dict() for cell in self.cells],
            "attempt_count": self.attempt_count,
            "source_path": str(self.source_path) if self.source_path else None,
        }

    @classmethod
    def from_mapping(
        cls, value: Any, *, source_path: Path | None = None, default_id: str = ""
    ) -> ExperimentSpec:
        """Validate one experiment definition."""
        if not isinstance(value, dict):
            raise ExperimentError("experiment must be a mapping")

        experiment_id = str(value.get("id") or default_id).strip()
        if not experiment_id:
            raise ExperimentError("experiment needs an id (or a filename to derive one from)")

        task_id = value.get("task_id") or value.get("task")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ExperimentError(f"experiment {experiment_id!r} needs a task_id")
        task_id = task_id.strip()

        raw_vary = value.get("vary", {})
        if not isinstance(raw_vary, dict):
            raise ExperimentError(f"experiment {experiment_id!r} vary must be a mapping")
        vary: dict[str, tuple[str, ...]] = {}
        for key, raw_values in raw_vary.items():
            axis = str(key)
            if axis not in VARYABLE:
                raise ExperimentError(
                    f"experiment {experiment_id!r} varies {axis!r}, which is not a run "
                    f"selector; varyable axes are {', '.join(VARYABLE)}"
                )
            if not isinstance(raw_values, (list, tuple)) or not raw_values:
                raise ExperimentError(
                    f"experiment {experiment_id!r} vary.{axis} must be a non-empty list"
                )
            values = tuple(str(item).strip() for item in raw_values if str(item).strip())
            if not values:
                raise ExperimentError(
                    f"experiment {experiment_id!r} vary.{axis} has no usable values"
                )
            if len(set(values)) != len(values):
                raise ExperimentError(f"experiment {experiment_id!r} vary.{axis} repeats a value")
            vary[axis] = values

        raw_fixed = value.get("fixed", {})
        if not isinstance(raw_fixed, dict):
            raise ExperimentError(f"experiment {experiment_id!r} fixed must be a mapping")
        fixed: dict[str, str] = {}
        for key, raw in raw_fixed.items():
            name = str(key)
            if name not in VARYABLE:
                raise ExperimentError(
                    f"experiment {experiment_id!r} fixes {name!r}, which is not a run selector"
                )
            if name in vary:
                # Freezing and varying the same selector is contradictory, and
                # silently preferring one would make the manifest a lie.
                raise ExperimentError(
                    f"experiment {experiment_id!r} both varies and fixes {name!r}"
                )
            if raw is None:
                continue
            fixed[name] = str(raw)

        repeats_value = value.get("repeats", 1)
        if isinstance(repeats_value, bool) or not isinstance(repeats_value, int):
            raise ExperimentError(f"experiment {experiment_id!r} repeats must be an integer")
        if repeats_value < 1:
            raise ExperimentError(f"experiment {experiment_id!r} repeats must be at least 1")

        return cls(
            experiment_id=experiment_id,
            task_id=task_id,
            vary=vary,
            fixed=fixed,
            repeats=repeats_value,
            description=str(value.get("description") or "").strip(),
            source_path=source_path,
        )

    def selectors_for(self, cell: Cell) -> dict[str, str]:
        """The full selector set for one cell: its axis values over the frozen ones."""
        return {**self.fixed, **cell.selectors}


def load_experiment_file(path: Path) -> ExperimentSpec:
    """Load one experiment definition from YAML."""
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    return ExperimentSpec.from_mapping(value, source_path=path, default_id=path.stem)


def load_experiments(root: Path) -> dict[str, ExperimentSpec]:
    """Load every experiment under a directory, keyed by declared id.

    A malformed definition is skipped rather than raising, so one bad file does
    not hide the others from `experiment list`.
    """
    experiments: dict[str, ExperimentSpec] = {}
    if not root.is_dir():
        return experiments
    for path in sorted((*root.glob("*.yaml"), *root.glob("*.yml"))):
        try:
            spec = load_experiment_file(path)
        except (ExperimentError, OSError, yaml.YAMLError, ValueError):
            continue
        experiments[spec.experiment_id] = spec
    return experiments


def resolve_experiment(
    repo_root: Path, experiment_id: str, *, root: Path | None = None
) -> ExperimentSpec:
    """Resolve one experiment by id, or by path to a definition file."""
    candidate = Path(experiment_id)
    if candidate.suffix in {".yaml", ".yml"} and candidate.is_file():
        return load_experiment_file(candidate)
    experiments = load_experiments(root or (repo_root / "experiments"))
    if experiment_id not in experiments:
        known = ", ".join(sorted(experiments)) or "(none)"
        raise ExperimentError(f"unknown experiment: {experiment_id!r}; known: {known}")
    return experiments[experiment_id]
