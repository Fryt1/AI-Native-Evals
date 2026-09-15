"""The dependency between `experiments/` and `runs/` points one way.

A comparison is an experiment, and it used to live in `runs/`. That made the two
packages import each other, which Python tolerates only because the imports were
deferred into function bodies -- three accessors whose only job was to break the
cycle at call time. A runtime workaround for a structural problem is invisible
until someone adds an ordinary import and the whole package fails to load.

The direction is now enforced rather than remembered: `experiments/` may use
`runs/` to execute a plan, and `runs/` may not know that experiments exist.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).parents[1] / "src" / "ai_native_evals"


def _imported_packages(path: Path, *, package: str) -> set[str]:
    """Top-level `ai_native_evals` subpackages this module imports.

    ``package`` is the subpackage the file lives in (``runs`` or ``experiments``),
    which is what resolves a relative import's dots: ``from ..experiments.spec``
    inside ``ai_native_evals/runs/x.py`` reaches ``ai_native_evals.experiments``.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    packages: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("ai_native_evals."):
                    packages.add(alias.name.split(".")[1])
                # `import ai_native_evals.experiments` also counts.
                elif alias.name == "ai_native_evals":
                    continue
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # level 1 stays in this package, level 2 climbs to
                # ai_native_evals, level 3 leaves it entirely.
                if node.level == 2 and node.module:
                    packages.add(node.module.split(".")[0])
                elif node.level >= 3 and node.module:
                    packages.add(node.module.split(".")[0])
            elif node.module and node.module.startswith("ai_native_evals."):
                packages.add(node.module.split(".")[1])
    return {name for name in packages if name}


def test_runs_does_not_import_experiments() -> None:
    """`runs/` executes a plan; it must not know experiments exist."""
    offenders: list[str] = []
    for path in (SRC / "runs").rglob("*.py"):
        if "experiments" in _imported_packages(path, package="runs"):
            offenders.append(str(path.relative_to(SRC.parent.parent)))

    assert not offenders, (
        "runs/ imports experiments/, which reintroduces the cycle the lazy "
        f"accessors used to hide: {offenders}"
    )


def test_experiments_does_not_defer_its_imports_of_runs() -> None:
    """No function-body imports of `runs.*` -- those existed only for the cycle.

    A deferred import is how the cycle was tolerated. Now that the direction is
    one way, an ordinary module-level import is correct, and a function-body one
    would mean the cycle has quietly come back.
    """
    offenders: list[str] = []
    for path in (SRC / "experiments").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            for node in ast.walk(function):
                if not isinstance(node, ast.ImportFrom) or not node.level:
                    continue
                module = node.module or ""
                # level 2 inside experiments/<mod>.py is `ai_native_evals.<module>`
                if node.level == 2 and module.split(".")[0] == "runs":
                    offenders.append(f"{path.name}:{node.lineno}")

    assert not offenders, (
        f"experiments/ defers its runs/ imports, which only a cycle requires: {offenders}"
    )


def test_compare_is_an_experiment_not_a_run() -> None:
    """The module that made the cycle is on the experiments side of it."""
    assert (SRC / "experiments" / "compare.py").is_file()
    assert not (SRC / "runs" / "compare.py").exists()


def test_runs_does_not_re_export_compare() -> None:
    """`runs` must not present a comparison as part of the run lifecycle."""
    source = (SRC / "runs" / "__init__.py").read_text(encoding="utf-8")

    assert "compare" not in source.replace("experiments/", "").replace("`compare`", "")
