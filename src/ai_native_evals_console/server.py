"""Console server entry point."""

from __future__ import annotations

from pathlib import Path


def run_server(
    *,
    repo_root: Path,
    config_path: Path | None = None,
    runs_root: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8787,
    reload: bool = False,
) -> None:
    """Start the API and the built React application when available."""
    import uvicorn

    from .api import create_app

    app = create_app(repo_root=repo_root, config_path=config_path, runs_root=runs_root)
    uvicorn.run(app, host=host, port=port, reload=reload)
