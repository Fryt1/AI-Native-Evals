"""FastAPI read API for the AI-Native Eval Console."""

from __future__ import annotations

import re
from collections.abc import Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ai_native_evals.providers import (
    ProviderError,
    load_provider_profiles,
    model_reasoning_levels,
    provider_model_listing,
    provider_summary,
)
from ai_native_evals.runs.resolver import load_config

from .catalog import Catalog
from .checks import PreflightError, PreflightManager
from .launch import LaunchError, LaunchManager, LaunchRequest
from .paths import default_repo_root, resolve_runs_root
from .read_models import (
    artifact_content,
    comparison_detail,
    evaluator_events,
    registry,
    run_detail,
    run_digest,
    run_evaluation,
    run_events,
)
from .readers import safe_media_type

#: Largest artifact served inline as text. Larger files are a download.
_MAX_INLINE_PREVIEW_BYTES = 25_000_000


class RunPlanRequest(BaseModel):
    """User-selectable profile IDs; the server resolves profile contents."""

    task_id: str = Field(min_length=1, max_length=200)
    agent: str | None = Field(default=None, max_length=200)
    model_profile: str | None = Field(default=None, max_length=200)
    model_provider: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=300)
    provider: str | None = Field(default=None, max_length=200)
    reasoning_effort: str | None = Field(default=None, max_length=64)
    mcp_profile: str | None = Field(default=None, max_length=200)
    sandbox_profile: str | None = Field(default=None, max_length=200)
    preset: str | None = Field(default=None, max_length=200)
    game_engine_ref: str | None = Field(default=None, max_length=200)
    dsh_ref: str | None = Field(default=None, max_length=200)
    no_evaluate: bool = False

    def to_launch_request(self) -> LaunchRequest:
        return LaunchRequest(**self.model_dump())


class ReasoningLevelRequest(BaseModel):
    """Ask which reasoning levels one provider/model/Agent triple permits."""

    provider: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=300)
    agent: str | None = Field(default=None, max_length=200)


class RunExecuteRequest(BaseModel):
    """The only value accepted to start a run is a previously previewed plan."""

    plan_id: str = Field(min_length=1, max_length=200)


class PreflightRequest(BaseModel):
    """Which run, if any, the environment check should consider.

    A Task makes the check run-specific (its images, its MCP hosts). Omitting it
    checks the machine alone, which is what a first-time setup needs.
    """

    task_id: str | None = Field(default=None, max_length=200)
    agent: str | None = Field(default=None, max_length=200)
    provider: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=300)
    reasoning_effort: str | None = Field(default=None, max_length=64)
    mcp_profile: str | None = Field(default=None, max_length=200)
    sandbox_profile: str | None = Field(default=None, max_length=200)
    preset: str | None = Field(default=None, max_length=200)
    distro: str | None = Field(default=None, max_length=200)
    #: Re-probe even when an identical check already succeeded.
    force: bool = False


class AgentCheckRequest(BaseModel):
    """How deeply to verify one Agent profile, and which build of it."""

    #: `static` inspects the profile and image; `smoke` starts a real container.
    level: str = Field(default="static", max_length=16)
    #: Verify this build instead of the profile's declared default, so one Agent
    #: can be checked at several versions.
    version: str = Field(default="", max_length=120)


def _sanitize_artifact_text(content: str, runs_root: Path, artifact_path: Path) -> str:
    """Remove host filesystem paths from text shown or downloaded by the browser."""
    run_dir = artifact_path
    for _ in range(4):
        if run_dir.name == "workspace":
            break
        run_dir = run_dir.parent
    replacements = {
        str(runs_root): "<eval-runs>",
        str(run_dir): "<run>",
        str(runs_root).replace("\\", "\\\\"): "<eval-runs>",
        str(run_dir).replace("\\", "\\\\"): "<run>",
    }
    for source, target in replacements.items():
        content = content.replace(source, target)
    # JSONL and JSON often contain escaped Windows paths; keep the preview useful
    # without exposing a machine-specific drive path. The pattern allows an
    # escaped backslash, but does not *require* one -- it previously did, so a
    # plain `C:\Users\me\scene.blend` inside a JSON event slipped through while
    # the same path with doubled separators was redacted.
    content = re.sub(r"[A-Za-z]:\\(?:\\|\\.|[^\"\n])*", "<host-path>", content)
    return content


def _agent_adapter(repo: Path, config: Mapping[str, Any], agent: str) -> str | None:
    """Read one agent profile's adapter so capability checks use real facts."""
    profile_roots = config.get("profile_roots")
    raw_root: Any = "profiles/agents"
    if isinstance(profile_roots, Mapping) and profile_roots.get("agents"):
        raw_root = profile_roots["agents"]
    root = Path(str(raw_root))
    if not root.is_absolute():
        root = repo / root
    path = root / f"{agent}.yaml"
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, Mapping):
        return None
    return str(data.get("adapter") or agent)


def create_app(
    *,
    repo_root: Path | None = None,
    config_path: Path | None = None,
    runs_root: Path | None = None,
    frontend_dir: Path | None = None,
) -> FastAPI:
    """Create an isolated API app with all filesystem access rooted in EvalRuns."""
    repo = (repo_root or default_repo_root()).resolve()
    resolved_runs = resolve_runs_root(repo, config_path=config_path, runs_root=runs_root)
    catalog = Catalog(resolved_runs)
    launcher = LaunchManager(repo, config_path=config_path)
    checks = PreflightManager(repo, config_path=config_path)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        launcher.shutdown()
        checks.shutdown()

    app = FastAPI(
        title="AI-Native Eval Console API",
        version="1.0",
        lifespan=lifespan,
    )
    app.state.repo_root = repo
    app.state.catalog = catalog
    app.state.launcher = launcher
    app.state.checks = checks
    app.state.runs_root = resolved_runs

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        diagnostics = catalog.diagnostics()
        return {
            "ok": True,
            "service": "ai-native-evals-console",
            "catalog": {
                "run_count": diagnostics["run_count"],
                "comparison_count": diagnostics["comparison_count"],
                "last_indexed_at": diagnostics["last_indexed_at"],
            },
        }

    @app.post("/api/v1/run-plans")
    def create_run_plan(request: RunPlanRequest) -> dict[str, Any]:
        try:
            return launcher.create_plan(request.to_launch_request())
        except LaunchError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/run-plans/{plan_id}")
    def get_run_plan(plan_id: str) -> dict[str, Any]:
        value = launcher.get_plan(plan_id)
        if value is None:
            raise HTTPException(status_code=404, detail="Run plan not found or expired")
        return value

    @app.post("/api/v1/run-plans/{plan_id}/execute", status_code=202)
    def execute_run_plan(plan_id: str) -> dict[str, Any]:
        try:
            return launcher.execute(plan_id)
        except LaunchError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/runs", status_code=202)
    def create_run(request: RunExecuteRequest) -> dict[str, Any]:
        try:
            return launcher.execute(request.plan_id)
        except LaunchError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        value = launcher.get_job(job_id)
        if value is None:
            raise HTTPException(status_code=404, detail="Run job not found or expired")
        return value

    @app.post("/api/v1/preflight", status_code=202)
    def start_preflight(request: PreflightRequest) -> dict[str, Any]:
        """Start an environment check; poll the returned id for the result.

        Probing Docker and host services takes seconds, so this cannot be a
        blocking GET without freezing the page.
        """
        selector = request.model_dump(exclude={"force"})
        try:
            return checks.start(selector, force=request.force)
        except PreflightError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/preflight")
    def latest_preflight() -> dict[str, Any]:
        """The most recent check, so a reload does not lose the result.

        Returns `{"check": null}` when nothing has run yet, rather than 404. A
        first visit is a normal state, not a failure: the browser logs a red
        error for every 4xx, so expressing "nothing yet" as 404 put a console
        error on the page for every new user, and made this endpoint disagree
        with `/agents/checks`, which answers the same question with an empty
        result.
        """
        return {"check": checks.latest()}

    @app.get("/api/v1/preflight/{check_id}")
    def get_preflight(check_id: str) -> dict[str, Any]:
        """One check by id. A missing id is a genuine 404: it was asked for."""
        value = checks.get(check_id)
        if value is None:
            raise HTTPException(status_code=404, detail="Environment check not found or expired")
        return value

    @app.get("/api/v1/agents/checks")
    def latest_agent_checks() -> dict[str, Any]:
        """The most recent verification per Agent, so a reload keeps results."""
        return {"items": checks.agent_results()}

    @app.post("/api/v1/agents/{agent_id}/check", status_code=202)
    def start_agent_check(agent_id: str, request: AgentCheckRequest) -> dict[str, Any]:
        """Verify one Agent. `smoke` starts a real container and asks it a question."""
        if request.level not in {"static", "smoke"}:
            raise HTTPException(status_code=422, detail="level must be static or smoke")
        return checks.start_agent(agent_id, level=request.level, version=request.version)

    @app.get("/api/v1/agents/checks/{check_id}")
    def get_agent_check(check_id: str) -> dict[str, Any]:
        value = checks.get_agent(check_id)
        if value is None:
            raise HTTPException(status_code=404, detail="Agent check not found or expired")
        return value

    @app.post("/api/v1/admin/reindex")
    def reindex() -> dict[str, Any]:
        return {"ok": True, **catalog.reindex()}

    @app.get("/api/v1/runs")
    def list_runs(
        task_id: str | None = None,
        agent_id: str | None = None,
        model: str | None = None,
        status: str | None = None,
        decision: str | None = None,
        bucket: str | None = Query(None, pattern="^(running|failed|review|passed)$"),
        query: str | None = None,
        has_errors: bool = False,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        rows, total = catalog.list_runs(
            filters={
                "task_id": task_id,
                "agent_id": agent_id,
                "model": model,
                "status": status,
                "decision": decision,
                "bucket": bucket,
                "query": query,
                "has_errors": has_errors,
                "limit": limit,
                "offset": offset,
            }
        )
        return {
            "items": rows,
            "total": total,
            "limit": limit,
            "offset": offset,
            "stats": catalog.stats(),
        }

    @app.get("/api/v1/runs/stats")
    def get_run_stats() -> dict[str, Any]:
        return catalog.stats()

    @app.get("/api/v1/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        value = run_detail(catalog, run_id)
        if value is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return value

    @app.get("/api/v1/runs/{run_id}/checks")
    def get_checks(run_id: str) -> dict[str, Any]:
        value = run_detail(catalog, run_id)
        if value is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return {"run_id": run_id, "items": value["checks"]}

    @app.get("/api/v1/runs/{run_id}/evaluation")
    def get_evaluation(run_id: str) -> dict[str, Any]:
        value = run_evaluation(catalog, run_id)
        if value is None:
            raise HTTPException(status_code=404, detail="Evaluation not found")
        return value

    @app.get("/api/v1/runs/{run_id}/digest")
    def get_digest(run_id: str) -> dict[str, Any]:
        value = run_digest(catalog, run_id)
        if value is None:
            raise HTTPException(status_code=404, detail="Digest not found")
        return value

    @app.get("/api/v1/runs/{run_id}/events")
    def get_events(
        run_id: str,
        after_seq: int = Query(-1, ge=-1),
        limit: int = Query(100, ge=1, le=500),
        event_type: str | None = Query(None, alias="type"),
        actor_kind: str | None = None,
        turn_id: str | None = None,
        query: str | None = None,
    ) -> dict[str, Any]:
        value = run_events(
            catalog,
            run_id,
            after_seq=after_seq,
            limit=limit,
            types={item for item in (event_type or "").split(",") if item},
            actor_kind=actor_kind,
            turn_id=turn_id,
            query=query,
        )
        if value is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return value

    @app.get("/api/v1/runs/{run_id}/workspace")
    def get_workspace(run_id: str) -> dict[str, Any]:
        value = run_detail(catalog, run_id)
        if value is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return {"run_id": run_id, **value["workspace"], "artifacts": value["artifacts"]}

    @app.get("/api/v1/runs/{run_id}/evaluator-runs")
    def get_evaluator_runs(run_id: str) -> dict[str, Any]:
        value = run_detail(catalog, run_id)
        if value is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return {"run_id": run_id, "items": value["evaluator_runs"]}

    @app.get("/api/v1/runs/{run_id}/evaluator-runs/{evaluator_run_id}/events")
    def get_evaluator_events(
        run_id: str,
        evaluator_run_id: str,
        after_seq: int = Query(-1, ge=-1),
        limit: int = Query(100, ge=1, le=500),
        event_type: str | None = Query(None, alias="type"),
        query: str | None = None,
    ) -> dict[str, Any]:
        value = evaluator_events(
            catalog,
            run_id,
            evaluator_run_id,
            after_seq=after_seq,
            limit=limit,
            types={item for item in (event_type or "").split(",") if item},
            query=query,
        )
        if value is None:
            raise HTTPException(status_code=404, detail="Evaluator Run not found")
        return value

    @app.get("/api/v1/runs/{run_id}/artifacts")
    def get_artifacts(run_id: str) -> dict[str, Any]:
        value = run_detail(catalog, run_id)
        if value is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return {"run_id": run_id, "items": value["artifacts"]}

    @app.get("/api/v1/runs/{run_id}/artifacts/{artifact_id}")
    def get_artifact(run_id: str, artifact_id: str, download: bool = False) -> Response:
        value = artifact_content(catalog, run_id, artifact_id)
        if value is None:
            raise HTTPException(status_code=404, detail="Artifact not found")
        artifact, path = value
        mime = safe_media_type(path, str(artifact.get("mime_type") or ""))
        disposition = "attachment" if download else "inline"
        filename = Path(str(artifact.get("relative_path") or path.name)).name
        if mime.startswith("text/") or mime in {
            "application/json",
            "application/jsonl",
            "application/yaml",
        }:
            try:
                # Bounded like the legacy scan: a run's workspace is subject
                # input, and reading whatever size it declares into one response
                # is a memory limit the subject gets to choose. Over the cap the
                # caller is told to download instead.
                size = path.stat().st_size
                if size > _MAX_INLINE_PREVIEW_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"artifact is {size} bytes; "
                            "use ?download=true to retrieve it"
                        ),
                    )
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                raise HTTPException(status_code=404, detail="Artifact cannot be read") from exc
            content = _sanitize_artifact_text(content, catalog.runs_root, path)
            return PlainTextResponse(
                content,
                media_type=mime,
                headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
            )
        return FileResponse(
            path,
            media_type=mime,
            filename=filename,
            headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
        )

    @app.get("/api/v1/comparisons")
    def get_comparisons() -> dict[str, Any]:
        return {
            "items": [
                {key: value for key, value in item.items() if key != "output_dir"}
                for item in catalog.list_comparisons()
            ]
        }

    @app.get("/api/v1/comparisons/{comparison_id}")
    def get_comparison(comparison_id: str) -> dict[str, Any]:
        value = comparison_detail(catalog, comparison_id)
        if value is None:
            raise HTTPException(status_code=404, detail="Comparison not found")
        return value

    @app.get("/api/v1/registry")
    def get_registry() -> dict[str, Any]:
        return registry(repo)

    def _providers() -> dict[str, Any]:
        try:
            return load_provider_profiles(repo, _config())
        except ProviderError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def _provider_or_404(provider_id: str) -> Any:
        profile = _providers().get(provider_id)
        if profile is None:
            raise HTTPException(
                status_code=404, detail=f"unknown provider: {provider_id}"
            )
        return profile

    @app.get("/api/v1/providers")
    def list_providers(agent: str = "codex") -> dict[str, Any]:
        """List configured upstreams. Never blocks on a remote endpoint."""
        adapter = _agent_adapter(repo, _config(), agent)
        return {
            "items": [
                provider_summary(repo, profile, agent=agent, adapter=adapter)
                for profile in _providers().values()
            ]
        }

    @app.get("/api/v1/providers/{provider_id}/models")
    def list_provider_models(provider_id: str, agent: str = "codex") -> dict[str, Any]:
        """List the models a provider actually serves, asked live."""
        profile = _provider_or_404(provider_id)
        adapter = _agent_adapter(repo, _config(), agent)
        try:
            listing = provider_model_listing(repo, profile, agent=agent, adapter=adapter)
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"provider": provider_id, **listing}

    @app.post("/api/v1/reasoning-levels")
    def reasoning_levels(request: ReasoningLevelRequest) -> dict[str, Any]:
        """Reasoning levels legal for one provider/model/Agent triple."""
        profile = _provider_or_404(request.provider)
        agent = request.agent or "codex"
        adapter = _agent_adapter(repo, _config(), agent)
        result = model_reasoning_levels(
            repo, profile, request.model, agent=agent, adapter=adapter
        )
        return {
            "provider": request.provider,
            "model": request.model,
            "agent": agent,
            "adapter": adapter,
            **result,
        }

    # Loaded on first provider request: most Console reads do not need the
    # evaluation config, and a fixture without one must still serve.
    config_cache: dict[str, Any] = {}

    def _config() -> dict[str, Any]:
        if not config_cache:
            path = config_path or repo / "config" / "eval.yaml"
            config_cache["value"] = load_config(path) if path.is_file() else {}
        return config_cache["value"]

    static_candidates = [
        (frontend_dir or repo / "apps" / "eval-console" / "dist").resolve(),
        (Path(__file__).resolve().parent / "static").resolve(),
    ]
    static_root = next(
        (candidate for candidate in static_candidates if (candidate / "index.html").is_file()),
        static_candidates[0],
    )
    if static_root.is_dir() and (static_root / "index.html").is_file():
        assets_root = static_root / "assets"
        if assets_root.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_root), name="frontend-assets")

        @app.get("/", include_in_schema=False)
        def frontend_index() -> FileResponse:
            return FileResponse(static_root / "index.html", media_type="text/html")

        @app.get("/favicon.svg", include_in_schema=False)
        def favicon() -> FileResponse:
            return FileResponse(static_root / "favicon.svg", media_type="image/svg+xml")

        @app.get("/{full_path:path}", include_in_schema=False)
        def frontend_fallback(full_path: str) -> FileResponse:
            # Keep unknown API paths as API 404s and only serve files from dist.
            if full_path == "" or full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Not found")
            candidate = (static_root / full_path).resolve()
            try:
                candidate.relative_to(static_root)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail="Not found") from exc
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(static_root / "index.html", media_type="text/html")
    else:

        @app.get("/", include_in_schema=False)
        def missing_frontend() -> JSONResponse:
            return JSONResponse(
                {
                    "service": "ai-native-evals-console",
                    "message": "Frontend build not found; run pnpm --dir apps/eval-console build.",
                }
            )

    return app
