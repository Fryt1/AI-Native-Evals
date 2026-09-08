"""Filesystem-backed Task bundles with a legacy eval.yaml fallback."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..resources import ResourceSpec


class TaskBundleError(ValueError):
    """Raised when a Task bundle is missing or malformed."""


@dataclass(frozen=True, slots=True)
class TaskBundle:
    """Complete task-owned definition independent of the selected Agent."""

    task_id: str
    version: str
    prompt: str
    test_plan: dict[str, Any] = field(default_factory=dict)
    resources: tuple[dict[str, Any], ...] = ()
    dataset: tuple[dict[str, Any], ...] = ()
    rubric: Any = None
    execution: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    verify: dict[str, Any] = field(default_factory=dict)
    source_path: Path | None = None
    legacy: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return the resolved task definition used by a run manifest."""
        return {
            "id": self.task_id,
            "version": self.version,
            "prompt": self.prompt,
            "test_plan": copy.deepcopy(self.test_plan),
            "resources": [copy.deepcopy(value) for value in self.resources],
            "dataset": [copy.deepcopy(value) for value in self.dataset],
            "rubric": copy.deepcopy(self.rubric),
            "execution": copy.deepcopy(self.execution),
            "metadata": copy.deepcopy(self.metadata),
            "verify": copy.deepcopy(self.verify),
            "source_path": str(self.source_path) if self.source_path else None,
            "legacy": self.legacy,
        }

    def resource_specs(
        self,
        *,
        repo_root: Path,
        source_roots: Mapping[str, Any],
    ) -> tuple[ResourceSpec, ...]:
        """Validate and resolve the resources declared by this Task."""
        return tuple(
            ResourceSpec.from_mapping(
                value,
                repo_root=repo_root,
                source_roots=source_roots,
                index=index,
            )
            for index, value in enumerate(self.resources)
        )


class TaskLoader:
    """Small facade used by CLI and run resolution for Task discovery."""

    def __init__(self, repo_root: Path, config: Mapping[str, Any] | None = None) -> None:
        self.repo_root = repo_root.resolve()
        self.config = config or {}

    def load(self, task_id: str) -> TaskBundle:
        """Load one filesystem bundle or the compatibility inline definition."""
        return load_task_bundle(self.repo_root, task_id, config=self.config)

    def list(self) -> tuple[str, ...]:
        """List all available Task ids."""
        return list_task_bundles(self.repo_root, config=self.config)


def load_task_bundle(
    repo_root: Path,
    task_id: str,
    *,
    config: Mapping[str, Any] | None = None,
) -> TaskBundle:
    """Load tasks/<id> first, falling back to legacy inline task config."""
    config = config or {}
    for root in _task_roots(repo_root, config):
        task_dir = root / task_id
        manifest_path = task_dir / "task.yaml"
        if manifest_path.is_file():
            return _load_bundle_file(task_id, task_dir, manifest_path)
    return _load_legacy_bundle(repo_root, task_id, config)


def list_task_bundles(
    repo_root: Path,
    *,
    config: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """List filesystem Task bundles and legacy-only task ids without duplicates."""
    config = config or {}
    ids: set[str] = set()
    for root in _task_roots(repo_root, config):
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if child.is_dir() and (child / "task.yaml").is_file():
                ids.add(child.name)
    tasks = config.get("tasks", {})
    if isinstance(tasks, Mapping):
        ids.update(str(key) for key in tasks)
    return tuple(sorted(ids))


def _load_bundle_file(task_id: str, task_dir: Path, manifest_path: Path) -> TaskBundle:
    payload = _load_yaml(manifest_path)
    declared_id = payload.get("id", task_id)
    if not isinstance(declared_id, str) or declared_id != task_id:
        raise TaskBundleError(
            f"task bundle id mismatch: directory={task_id!r}, manifest={declared_id!r}"
        )
    version = payload.get("version", "1")
    if not isinstance(version, (str, int)) or not str(version):
        raise TaskBundleError(f"task {task_id!r} version must be non-empty")
    prompt = _load_prompt(task_dir, payload)
    test_plan = payload.get("test_plan", {})
    if test_plan is None:
        test_plan = {}
    if not isinstance(test_plan, Mapping):
        raise TaskBundleError(f"task {task_id!r} test_plan must be a mapping")
    resources = payload.get("resources", [])
    if not isinstance(resources, list) or not all(
        isinstance(value, Mapping) for value in resources
    ):
        raise TaskBundleError(f"task {task_id!r} resources must be a list of mappings")
    execution = payload.get("execution", {})
    metadata = payload.get("metadata", {})
    verify = payload.get("verify", {})
    for name, value in (("execution", execution), ("metadata", metadata), ("verify", verify)):
        if not isinstance(value, Mapping):
            raise TaskBundleError(f"task {task_id!r} {name} must be a mapping")
    dataset = _load_dataset(task_dir, payload)
    rubric = _load_optional_yaml(task_dir, payload.get("rubric_file"))
    if rubric is None and "rubric" in payload:
        rubric = payload["rubric"]
    return TaskBundle(
        task_id=task_id,
        version=str(version),
        prompt=prompt,
        test_plan=dict(test_plan),
        resources=tuple(dict(value) for value in resources),
        dataset=dataset,
        rubric=rubric,
        execution=dict(execution),
        metadata=dict(metadata),
        verify=dict(verify),
        source_path=manifest_path,
    )


def _load_legacy_bundle(
    repo_root: Path,
    task_id: str,
    config: Mapping[str, Any],
) -> TaskBundle:
    tasks = config.get("tasks", {})
    if "task_roots" in config and not any(
        (root / task_id / "task.yaml").is_file() for root in _task_roots(repo_root, config)
    ):
        raise TaskBundleError(f"task bundle does not exist: {task_id}")
    if isinstance(tasks, Mapping) and tasks and task_id not in tasks:
        raise TaskBundleError(f"legacy task does not exist: {task_id}")
    task_config = tasks.get(task_id, {}) if isinstance(tasks, Mapping) else {}
    if not isinstance(task_config, Mapping):
        raise TaskBundleError(f"task {task_id!r} must be a mapping")
    prompt = task_config.get("prompt")
    if prompt is None:
        prompt = (
            f"Execute evaluation task {task_id!r} in /workspace. "
            "Read project instructions before making changes and verify the result."
        )
    if not isinstance(prompt, str):
        raise TaskBundleError(f"task {task_id!r} prompt must be a string")
    test_plan = task_config.get("test_plan") or {}
    if not isinstance(test_plan, Mapping):
        raise TaskBundleError(f"task {task_id!r} test_plan must be a mapping")
    verify = task_config.get("verify") or {}
    if not isinstance(verify, Mapping):
        raise TaskBundleError(f"task {task_id!r} verify must be a mapping")
    resources = task_config.get("resources")
    if resources is None:
        resources = _legacy_default_resources(config)
    if not isinstance(resources, list) or not all(
        isinstance(value, Mapping) for value in resources
    ):
        raise TaskBundleError(f"task {task_id!r} resources must be a list of mappings")
    execution = {
        key: task_config[key]
        for key in ("agent", "model_profile", "mcp_profile")
        if key in task_config
    }
    return TaskBundle(
        task_id=task_id,
        version="legacy",
        prompt=prompt,
        test_plan=dict(test_plan),
        resources=tuple(dict(value) for value in resources),
        dataset=_legacy_dataset(task_config),
        execution=execution,
        verify=dict(verify),
        source_path=None,
        legacy=True,
    )


def _legacy_default_resources(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Preserve old inline-config behavior without making it the new default."""
    paths = config.get("paths", {})
    if not isinstance(paths, Mapping):
        return []
    resources: list[dict[str, Any]] = []
    if paths.get("game_engine"):
        resources.append(
            {
                "id": "game-engine",
                "kind": "repository",
                "source": "game_engine",
                "mount": "game-engine",
            }
        )
    if paths.get("dsh"):
        resources.append(
            {
                "id": "dsh",
                "kind": "repository",
                "source": "dsh",
                "mount": "ai-native-dsh",
                "required": False,
            }
        )
    return resources


def _task_roots(repo_root: Path, config: Mapping[str, Any]) -> tuple[Path, ...]:
    raw = config.get("task_roots", ["tasks"])
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise TaskBundleError("task_roots must be a string or list")
    roots: list[Path] = []
    for value in raw:
        if not isinstance(value, str) or not value:
            raise TaskBundleError("task_roots entries must be non-empty strings")
        path = Path(value)
        roots.append((repo_root / path if not path.is_absolute() else path).resolve())
    return tuple(roots)


def _load_prompt(task_dir: Path, payload: Mapping[str, Any]) -> str:
    raw_file = payload.get("prompt_file", "prompt.md")
    if raw_file is not None:
        if not isinstance(raw_file, str) or not raw_file:
            raise TaskBundleError("prompt_file must be a non-empty string")
        path = (task_dir / raw_file).resolve()
        try:
            path.relative_to(task_dir.resolve())
        except ValueError as exc:
            raise TaskBundleError("prompt_file must stay inside the task directory") from exc
        if path.is_file():
            return path.read_text(encoding="utf-8")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise TaskBundleError(f"task bundle {task_dir.name!r} requires prompt.md or prompt")
    return prompt


def _load_dataset(task_dir: Path, payload: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    raw_file = payload.get("dataset_file")
    raw_dataset = payload.get("dataset")
    if raw_file is not None:
        if not isinstance(raw_file, str) or not raw_file:
            raise TaskBundleError("dataset_file must be a non-empty string")
        path = (task_dir / raw_file).resolve()
        try:
            path.relative_to(task_dir.resolve())
        except ValueError as exc:
            raise TaskBundleError("dataset_file must stay inside the task directory") from exc
        if not path.is_file():
            raise TaskBundleError(f"dataset file does not exist: {path}")
        values: Any
        if path.suffix.lower() in {".jsonl", ".ndjson"}:
            values = []
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        values.append(json.loads(line))
            except (OSError, json.JSONDecodeError) as exc:
                raise TaskBundleError(f"could not parse dataset file: {path}") from exc
        else:
            values = _load_yaml(path)
            if isinstance(values, Mapping) and "samples" in values:
                values = values["samples"]
    else:
        values = raw_dataset if raw_dataset is not None else []
    if not isinstance(values, list) or not all(isinstance(value, Mapping) for value in values):
        raise TaskBundleError(f"task {task_dir.name!r} dataset must be a list of mappings")
    return tuple(dict(value) for value in values)


def _legacy_dataset(task_config: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    values = task_config.get("dataset", [])
    if not isinstance(values, list) or not all(isinstance(value, Mapping) for value in values):
        return ()
    return tuple(dict(value) for value in values)


def _load_optional_yaml(task_dir: Path, raw_file: Any) -> Any:
    if raw_file is None:
        return None
    if not isinstance(raw_file, str) or not raw_file:
        raise TaskBundleError("rubric_file must be a non-empty string")
    path = (task_dir / raw_file).resolve()
    try:
        path.relative_to(task_dir.resolve())
    except ValueError as exc:
        raise TaskBundleError("rubric_file must stay inside the task directory") from exc
    if not path.is_file():
        raise TaskBundleError(f"rubric file does not exist: {path}")
    return _load_yaml(path)


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise TaskBundleError(f"could not read task file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise TaskBundleError(f"task file must contain a mapping: {path}")
    return payload
