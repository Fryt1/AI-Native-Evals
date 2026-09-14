"""Behavior tests for the read-only Eval Console boundary."""

from __future__ import annotations

import ast
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from ai_native_evals_console.api import create_app
from ai_native_evals_console.catalog import Catalog


def _write_run(root: Path, run_id: str = "run-1", *, normalized: bool = True) -> Path:
    run_dir = root / run_id
    workspace = run_dir / "workspace"
    (workspace / "output").mkdir(parents=True)
    (workspace / "evidence").mkdir(parents=True)
    (workspace / "trace").mkdir(parents=True)
    (workspace / "output" / "result.json").write_text('{"ok": true}\n', encoding="utf-8")
    manifest = {
        "status": "completed",
        "run": {
            "run_id": run_id,
            "task_id": "demo-task",
            "agent": "codex",
            "model": "demo-model",
            "created_at": "2026-09-09T00:00:00+00:00",
            "agent_profile": {"id": "codex"},
            "sandbox": {"id": "docker-default"},
        },
        "runtime": {
            "started_at": "2026-09-09T00:00:00+00:00",
            "completed_at": "2026-09-09T00:00:02+00:00",
        },
    }
    (run_dir / "run-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    evaluation = {
        "run_id": run_id,
        "task_id": "demo-task",
        "decision": "pass",
        "outcome_score": 1.0,
        "quality_score": 0.8,
        "process_score": 0.9,
        "checks": [
            {
                "check_id": "result-exists",
                "phase": "outcome",
                "evaluator": "script.file_exists.v1",
                "status": "passed",
                "passed": True,
                "score": 1.0,
                "required": True,
                "evidence_refs": ["output/result.json"],
            }
        ],
        "errors": [],
    }
    (workspace / "evidence" / "evaluation.json").write_text(
        json.dumps(evaluation), encoding="utf-8"
    )
    if normalized:
        events = [
            {"seq": 0, "type": "run_started", "agent_id": "codex", "payload": {}},
            {
                "seq": 1,
                "type": "agent_message",
                "agent_id": "codex",
                "payload": {"item": {"text": "done"}},
            },
        ]
        (workspace / "trace" / "normalized-events.jsonl").write_text(
            "".join(json.dumps(item) + "\n" for item in events), encoding="utf-8"
        )
    else:
        digest = {
            "stats": {"event_items": {"error": 1}},
            "timeline": [{"kind": "error", "error": "warning"}],
        }
        (workspace / "trace" / "digest.json").write_text(json.dumps(digest), encoding="utf-8")
    return run_dir


def test_catalog_and_api_hide_host_paths_and_serve_artifacts(tmp_path: Path) -> None:
    _write_run(tmp_path)
    app = create_app(repo_root=tmp_path, runs_root=tmp_path)
    client = TestClient(app)

    runs = client.get("/api/v1/runs?limit=10")
    assert runs.status_code == 200
    assert runs.json()["total"] == 1
    assert "run_dir" not in runs.json()["items"][0]
    assert str(tmp_path) not in runs.text

    detail = client.get("/api/v1/runs/run-1")
    assert detail.status_code == 200
    assert detail.json()["scores"]["quality"] == 0.8
    assert "run_dir" not in detail.json()["summary"]
    assert str(tmp_path) not in detail.text
    artifacts = client.get("/api/v1/runs/run-1/artifacts").json()["items"]
    artifact = next(item for item in artifacts if item["relative_path"] == "output/result.json")
    content = client.get(f"/api/v1/runs/run-1/artifacts/{artifact['artifact_id']}")
    assert content.status_code == 200
    assert '"ok": true' in content.text
    assert client.get("/api/v1/runs/run-1/artifacts/../../secret").status_code in {404, 422}


def test_subject_declared_mime_cannot_serve_active_content(tmp_path: Path) -> None:
    """A run must not be able to serve itself as same-origin script.

    `artifacts/manifest.json` lives in the workspace the evaluated Agent writes,
    and `mime_type` from it was used verbatim as the response `Content-Type`.
    Declaring `text/html` therefore made the Console execute the run's own file.
    """
    run_dir = _write_run(tmp_path)
    artifacts_dir = run_dir / "workspace" / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "evil.html").write_text(
        "<script>alert(document.domain)</script>", encoding="utf-8"
    )
    (artifacts_dir / "manifest.json").write_text(
        json.dumps(
            {
                "artifacts": [
                    {
                        "relative_path": "artifacts/evil.html",
                        "role": "subject_output",
                        "mime_type": "text/html",
                        "previewable": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app(repo_root=tmp_path, runs_root=tmp_path))

    items = client.get("/api/v1/runs/run-1/artifacts").json()["items"]
    evil = next(item for item in items if item["relative_path"].endswith("evil.html"))
    response = client.get(f"/api/v1/runs/run-1/artifacts/{evil['artifact_id']}")

    assert response.status_code == 200
    served = response.headers["content-type"].split(";")[0]
    assert served not in {"text/html", "image/svg+xml", "application/xhtml+xml"}
    assert served == "application/octet-stream"


def test_svg_artifact_is_not_served_as_an_image(tmp_path: Path) -> None:
    """SVG is scriptable, so it must not ride the image/* path."""
    run_dir = _write_run(tmp_path)
    output = run_dir / "workspace" / "output"
    (output / "diagram.svg").write_text("<svg onload='alert(1)'/>", encoding="utf-8")
    client = TestClient(create_app(repo_root=tmp_path, runs_root=tmp_path))

    items = client.get("/api/v1/runs/run-1/artifacts").json()["items"]
    svg = next(item for item in items if item["relative_path"].endswith(".svg"))
    response = client.get(f"/api/v1/runs/run-1/artifacts/{svg['artifact_id']}")

    assert response.headers["content-type"].split(";")[0] == "application/octet-stream"


def test_events_do_not_leak_host_paths(tmp_path: Path) -> None:
    """The events endpoint returned raw trace payloads, host paths included."""
    run_dir = _write_run(tmp_path, normalized=False)
    host_path = str(run_dir / "workspace" / "output" / "scene.blend")
    (run_dir / "workspace" / "trace" / "normalized-events.jsonl").write_text(
        json.dumps(
            {
                "seq": 0,
                "type": "file_changed",
                "agent_id": "codex",
                "payload": {"path": host_path, "posix": "/home/runner/.codex/auth.json"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "workspace" / "trace" / "digest.json").unlink()

    response = TestClient(create_app(repo_root=tmp_path, runs_root=tmp_path)).get(
        "/api/v1/runs/run-1/events"
    )

    assert response.status_code == 200
    assert str(tmp_path) not in response.text
    assert "/home/runner" not in response.text


def test_registry_reports_profiles_without_host_paths(tmp_path: Path) -> None:
    """`/registry` passed the loader's absolute `source_path` to the browser."""
    repo = tmp_path / "repo"
    (repo / "profiles" / "providers").mkdir(parents=True)
    (repo / "profiles" / "providers" / "relay.yaml").write_text(
        "id: relay\nname: Relay\nmodels:\n  - id: m\n", encoding="utf-8"
    )
    (repo / "config").mkdir(parents=True)
    (repo / "config" / "eval.yaml").write_text(
        "profile_roots:\n  providers: profiles/providers\n", encoding="utf-8"
    )
    client = TestClient(create_app(repo_root=repo, runs_root=tmp_path / "runs"))

    response = client.get("/api/v1/registry")

    assert response.status_code == 200
    assert str(tmp_path) not in response.text
    providers = response.json()["providers"]
    relay = next(item for item in providers if item["id"] == "relay")
    # Relative to the repository, which is what the UI needs and all it should get.
    assert relay["path"] == "profiles/providers/relay.yaml"


def test_digest_timeline_is_a_legacy_trace_fallback(tmp_path: Path) -> None:
    _write_run(tmp_path, normalized=False)
    app = create_app(repo_root=tmp_path, runs_root=tmp_path)
    response = TestClient(app).get("/api/v1/runs/run-1/events")
    assert response.status_code == 200
    payload = response.json()
    assert payload["events"][0]["type"] == "error"
    assert payload["events"][0]["source"]["adapter"] == "digest"


def test_catalog_reindex_is_repeatable_and_comparison_is_readable(tmp_path: Path) -> None:
    _write_run(tmp_path)
    comparison_dir = tmp_path / "comparisons" / "demo-comparison"
    comparison_dir.mkdir(parents=True)
    (comparison_dir / "comparison.json").write_text(
        json.dumps(
            {
                "comparison_id": "demo-comparison",
                "created_at": "2026-09-09T00:00:00+00:00",
                "invariant": {"task_id": "demo-task"},
                "runs": [{"run_id": "run-1", "agent": "codex"}],
            }
        ),
        encoding="utf-8",
    )
    catalog = Catalog(tmp_path)
    assert catalog.reindex()["indexed_runs"] == 1
    assert catalog.reindex()["changed_runs"] == 0
    app = create_app(repo_root=tmp_path, runs_root=tmp_path)
    client = TestClient(app)
    response = client.get("/api/v1/comparisons/demo-comparison")
    assert response.status_code == 200
    assert response.json()["fairness"]["fair"] is True
    assert response.json()["output_dir"] is None


def test_experiment_artifacts_are_indexed_like_comparisons(tmp_path: Path) -> None:
    """`experiment run` writes `experiments/<id>/experiment.json`.

    The Console scanned only `comparisons/`, so a matrix experiment was invisible
    in the UI even though it carries the same facts under the same keys. An
    experiment is a comparison with more than one varying axis; one reader has to
    see both.
    """
    experiment_dir = tmp_path / "experiments" / "sweep-20260914T000000Z-abc123"
    experiment_dir.mkdir(parents=True)
    (experiment_dir / "experiment.json").write_text(
        json.dumps(
            {
                "experiment_run_id": experiment_dir.name,
                "comparison_id": experiment_dir.name,
                "experiment_id": "sweep",
                "created_at": "2026-09-14T00:00:00+00:00",
                "invariant": {"task_id": "demo-task", "fixed": {"model": "m"}, "repeats": 3},
                "cells": [{"label": "agent=codex", "pass_rate": 0.67, "measured": 3}],
                "runs": [
                    {
                        "agent": "codex",
                        "run_id": "run-1",
                        "attempt": 1,
                        "status": "completed",
                        "evaluation": {"decision": "pass"},
                    }
                ],
                "discrimination": {"separated": False, "reason": "overlap"},
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app(repo_root=tmp_path, runs_root=tmp_path))

    listing = client.get("/api/v1/comparisons")
    assert listing.status_code == 200
    assert [item["comparison_id"] for item in listing.json()["items"]] == [experiment_dir.name]

    detail = client.get(f"/api/v1/comparisons/{experiment_dir.name}")
    assert detail.status_code == 200
    assert len(detail.json()["runs"]) == 1
    assert str(tmp_path) not in detail.text


def test_evaluator_child_trace_is_readable_without_becoming_a_top_level_run(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path)
    evaluator_dir = (
        run_dir / "workspace" / "trace" / "evaluators" / "quality-judge-report-quality" / "judge-1"
    )
    evaluator_dir.mkdir(parents=True)
    (evaluator_dir / "normalized-events.jsonl").write_text(
        json.dumps(
            {
                "seq": 0,
                "type": "agent_message",
                "agent_id": "codex",
                "payload": {"item": {"text": "judge done"}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    app = create_app(repo_root=tmp_path, runs_root=tmp_path)
    client = TestClient(app)
    response = client.get("/api/v1/runs/run-1/evaluator-runs/judge-1/events")
    assert response.status_code == 200
    assert response.json()["evaluator_run_id"] == "judge-1"
    assert response.json()["events"][0]["summary"] == "judge done"


def test_run_bucket_filters_use_decision_and_lifecycle_status(tmp_path: Path) -> None:
    _write_run(tmp_path, run_id="passed-run")
    failed_dir = _write_run(tmp_path, run_id="failed-run")
    manifest = json.loads((failed_dir / "run-manifest.json").read_text(encoding="utf-8"))
    manifest["status"] = "failed"
    (failed_dir / "run-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    failed_evaluation = json.loads(
        (failed_dir / "workspace" / "evidence" / "evaluation.json").read_text(encoding="utf-8")
    )
    failed_evaluation["decision"] = "fail"
    failed_evaluation["outcome_score"] = 0.0
    (failed_dir / "workspace" / "evidence" / "evaluation.json").write_text(
        json.dumps(failed_evaluation), encoding="utf-8"
    )
    app = create_app(repo_root=tmp_path, runs_root=tmp_path)
    client = TestClient(app)
    assert [
        item["run_id"] for item in client.get("/api/v1/runs?bucket=passed").json()["items"]
    ] == ["passed-run"]
    assert [
        item["run_id"] for item in client.get("/api/v1/runs?bucket=failed").json()["items"]
    ] == ["failed-run"]
    assert client.get("/api/v1/runs/stats").json()["decision"]["pass"] == 1


def test_comparison_dto_does_not_expose_host_paths(tmp_path: Path) -> None:
    _write_run(tmp_path, run_id="run-a")
    comparison_dir = tmp_path / "comparisons" / "comparison-a"
    comparison_dir.mkdir(parents=True)
    (comparison_dir / "comparison.json").write_text(
        json.dumps(
            {
                "comparison_id": "comparison-a",
                "created_at": "2026-09-09T00:00:00+00:00",
                "output_dir": str(comparison_dir),
                "invariant": {"task_id": "demo-task"},
                "runs": [{"run_id": "run-a", "agent": "codex", "run_dir": str(tmp_path / "run-a")}],
            }
        ),
        encoding="utf-8",
    )
    response = TestClient(create_app(repo_root=tmp_path, runs_root=tmp_path)).get(
        "/api/v1/comparisons/comparison-a"
    )
    assert response.status_code == 200
    assert str(tmp_path) not in response.text
    assert response.json()["runs"][0]["summary"].get("run_dir") is None


def test_evaluation_digest_checks_endpoints_return_sanitized_views(tmp_path: Path) -> None:
    _write_run(tmp_path)
    client = TestClient(create_app(repo_root=tmp_path, runs_root=tmp_path))
    assert client.get("/api/v1/runs/run-1/checks").json()["items"][0]["check_id"] == "result-exists"
    assert client.get("/api/v1/runs/run-1/evaluation").json()["decision"] == "pass"
    assert client.get("/api/v1/runs/run-1/digest").status_code == 404


def test_evaluation_modules_do_not_depend_on_console() -> None:
    """No module the evaluator owns may import the Console projection layer.

    This scanned eight subpackages and missed the top-level modules beside them --
    which is where `preflight.py` reached into `ai_native_evals_console.paths`, so
    the rule was broken for as long as the test existed and the test could not
    see it. It now walks every owned module, and names the package by its own
    import rather than by a string that a comment can trip over.
    """
    root = Path(__file__).parents[1] / "src" / "ai_native_evals"
    owned_modules = (
        "agents",
        "adapters",
        "evaluation",
        "resources",
        "runs",
        "sandboxes",
        "scorers",
        "solvers",
        "tasks",
    )
    owned_files = [path for module in owned_modules for path in (root / module).rglob("*.py")]
    # The top-level modules (cli, preflight, providers, plans, mcp, ...), which
    # the original scan skipped.
    owned_files += [path for path in root.glob("*.py")]
    assert len(owned_files) > 50, "the scan is not finding the owned modules"

    offenders = []
    for path in owned_files:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name.split(".")[0] == "ai_native_evals_console" for name in names):
                offenders.append(f"{path.relative_to(root.parent.parent)}:{node.lineno}")

    # The CLI is the documented exception: it may dispatch the optional console
    # command. It is also a top-level module, so it must be excluded by path.
    cli = str(Path("src") / "ai_native_evals" / "cli.py")
    offenders = [item for item in offenders if not item.startswith(cli)]
    assert not offenders, f"evaluation modules import the Console: {offenders}"


def test_run_plan_resolves_existing_selectors_without_creating_a_run() -> None:
    repo_root = Path(__file__).parents[1]
    client = TestClient(create_app(repo_root=repo_root, runs_root=repo_root / ".." / "EvalRuns"))
    response = client.post(
        "/api/v1/run-plans",
        json={
            "task_id": "codex-file-smoke",
            "agent": "codex",
            "model_profile": "sub2api-deepseek",
            "mcp_profile": "none",
            "sandbox_profile": "docker-default",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["plan_id"].startswith("plan-")
    assert data["run_id"].startswith("codex-file-smoke-")
    assert data["resolved"]["agent"] == "codex"
    assert data["resolved"]["model"] == "deepseek/deepseek-v4.1-flash"
    assert data["resolved"]["sandbox_profile"] == "docker-default"
    assert data["checks"]
    assert "D:\\work\\AI-Native\\EvalRuns" not in response.text


def test_run_plan_rejects_unknown_profile_before_execution() -> None:
    repo_root = Path(__file__).parents[1]
    client = TestClient(create_app(repo_root=repo_root, runs_root=repo_root / ".." / "EvalRuns"))
    response = client.post(
        "/api/v1/run-plans",
        json={"task_id": "codex-file-smoke", "agent": "not-a-real-agent"},
    )
    assert response.status_code == 422
    assert "unknown agent profile" in response.json()["detail"]


def test_run_plan_execution_uses_background_job_without_real_docker(
    tmp_path: Path, monkeypatch
) -> None:
    from ai_native_evals_console import launch

    repo_root = Path(__file__).parents[1]
    manager = launch.LaunchManager(repo_root, config_path=repo_root / "config" / "eval.yaml")
    plan = manager.create_plan(
        launch.LaunchRequest(
            task_id="codex-file-smoke",
            agent="codex",
            model_profile="sub2api-deepseek",
            mcp_profile="none",
            sandbox_profile="docker-default",
        )
    )
    run_dir = tmp_path / plan["run_id"]

    def fake_prepare(_repo_root, spec, **_kwargs):
        workspace = run_dir / "workspace"
        (workspace / "evidence").mkdir(parents=True)
        (workspace / "trace").mkdir(parents=True)
        manifest = {"status": "prepared", "run": spec.to_dict(), "paths": {}}
        (run_dir / "run-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return run_dir

    monkeypatch.setattr(launch, "prepare_run", fake_prepare)
    monkeypatch.setattr(launch, "start_docker_run", lambda *_args, **_kwargs: {"status": "running"})
    monkeypatch.setattr(
        launch, "wait_docker_run", lambda *_args, **_kwargs: {"status": "completed"}
    )
    monkeypatch.setattr(
        launch,
        "evaluate_run",
        lambda *_args, **_kwargs: {
            "run_id": plan["run_id"],
            "task_id": "codex-file-smoke",
            "decision": "pass",
            "outcome_score": 1.0,
            "quality_score": 1.0,
            "process_score": 1.0,
            "checks": [],
            "errors": [],
        },
    )
    try:
        job = manager.execute(plan["plan_id"])
        assert job["status"] in {"queued", "preparing"}
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            current = manager.get_job(job["job_id"])
            if current and current["status"] in {"completed", "failed", "error"}:
                break
            time.sleep(0.02)
        assert current is not None
        assert current["status"] == "completed"
        assert current["decision"] == "pass"
        assert (run_dir / "workspace" / "evidence" / "evaluation.json").is_file()
    finally:
        manager.shutdown()


def test_run_plan_exposes_provider_and_model_separately() -> None:
    repo_root = Path(__file__).parents[1]
    client = TestClient(create_app(repo_root=repo_root, runs_root=repo_root / ".." / "EvalRuns"))
    response = client.post(
        "/api/v1/run-plans",
        json={
            "task_id": "codex-file-smoke",
            "agent": "codex",
            # A route name that is not a provider profile: the legacy
            # binding-matching path, which must keep working.
            "model_provider": "eval",
            "model": "deepseek/deepseek-v4.1-flash",
            "mcp_profile": "none",
            "sandbox_profile": "docker-default",
        },
    )
    assert response.status_code == 200
    resolved = response.json()["resolved"]
    assert resolved["model_provider"] == "eval"
    assert resolved["model"] == "deepseek/deepseek-v4.1-flash"
    assert resolved["model_profile"] == "sub2api-deepseek"
