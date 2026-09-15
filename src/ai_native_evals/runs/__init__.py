"""Run lifecycle exports.

Deliberately does not export `compare`: a comparison is an experiment, and it
lives in `experiments/` so the dependency runs one way -- `experiments/` uses
`runs/` to execute a plan, and `runs/` knows nothing about experiments.
"""

from .agent_sandbox import EvaluatorAgentResult, default_readonly_mounts, run_evaluator_agent
from .docker_runtime import (
    DockerRuntimeError,
    list_orphan_resources,
    read_docker_logs,
    reclaim_orphans,
    start_docker_run,
    stop_docker_run,
    wait_docker_run,
)
from .lifecycle import (
    RunLifecycleError,
    cleanup_run,
    load_manifest,
    prepare_run,
    set_status,
    update_manifest,
)
from .resolver import EvalConfigError, load_config, resolve_run
from .spec import RunSpec

__all__ = [
    "DockerRuntimeError",
    "EvaluatorAgentResult",
    "EvalConfigError",
    "RunLifecycleError",
    "RunSpec",
    "cleanup_run",
    "default_readonly_mounts",
    "list_orphan_resources",
    "load_config",
    "load_manifest",
    "prepare_run",
    "read_docker_logs",
    "reclaim_orphans",
    "resolve_run",
    "run_evaluator_agent",
    "set_status",
    "start_docker_run",
    "stop_docker_run",
    "wait_docker_run",
    "update_manifest",
]
