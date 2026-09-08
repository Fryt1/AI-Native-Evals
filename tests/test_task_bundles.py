"""Tests for filesystem Task bundles and optional resources."""

from pathlib import Path

from ai_native_evals.runs.lifecycle import load_manifest, prepare_run
from ai_native_evals.runs.resolver import resolve_run
from ai_native_evals.tasks.bundles import load_task_bundle


def test_filesystem_bundle_is_authoritative_and_has_no_implicit_project(tmp_path: Path) -> None:
    task_dir = tmp_path / "tasks" / "plain"
    task_dir.mkdir(parents=True)
    (task_dir / "prompt.md").write_text("write an answer", encoding="utf-8")
    (task_dir / "task.yaml").write_text(
        "id: plain\nversion: 1\nprompt_file: prompt.md\nresources: []\n",
        encoding="utf-8",
    )
    config = tmp_path / "eval.yaml"
    config.write_text(
        """
 task_roots: [tasks]
 paths: {runs_root: EvalRuns}
 defaults: {agent: codex, model_profile: m, mcp_profile: none}
 model_profiles: {m: {model: test}}
 agents: {codex: {image: test-agent}}
 mcp_profiles: {none: {servers: {}}}
 """,
        encoding="utf-8",
    )

    bundle = load_task_bundle(tmp_path, "plain", config={"task_roots": ["tasks"]})
    spec = resolve_run(tmp_path, "plain", config_path=config)
    run_dir = prepare_run(tmp_path, spec)
    manifest = load_manifest(run_dir)

    assert bundle.prompt == "write an answer"
    assert spec.resource_specs == ()
    assert manifest["paths"]["project"] is None
    assert manifest["snapshots"]["resources"] == {}


def test_bundle_can_define_dataset_samples_next_to_task(tmp_path: Path) -> None:
    task_dir = tmp_path / "tasks" / "dataset-task"
    task_dir.mkdir(parents=True)
    (task_dir / "prompt.md").write_text("do one sample", encoding="utf-8")
    (task_dir / "cases.jsonl").write_text(
        '{"id":"one","input":"a"}\n{"id":"two","input":"b"}\n',
        encoding="utf-8",
    )
    (task_dir / "task.yaml").write_text(
        "id: dataset-task\nversion: 1\nprompt_file: prompt.md\n"
        "dataset_file: cases.jsonl\nresources: []\n",
        encoding="utf-8",
    )

    bundle = load_task_bundle(tmp_path, "dataset-task", config={"task_roots": ["tasks"]})

    assert bundle.dataset == (
        {"id": "one", "input": "a"},
        {"id": "two", "input": "b"},
    )
