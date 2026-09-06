"""Inspect AI entry-point loader for the evaluation suite."""

# Importing these modules registers their Inspect objects. Keeping the imports
# here makes package discovery explicit and gives the package one stable entry
# point without modifying Inspect AI itself.
from .scorers import ainative, filesystem  # noqa: F401
from .solvers import codex, mock  # noqa: F401
from .tasks import codex_file_smoke, smoke  # noqa: F401
