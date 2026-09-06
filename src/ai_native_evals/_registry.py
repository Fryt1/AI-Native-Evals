"""Inspect AI entry-point loader for the evaluation suite."""

# Importing these modules registers their Inspect objects. Keeping the imports
# here makes package discovery explicit and gives the package one stable entry
# point without modifying Inspect AI itself.
from .scorers import ainative  # noqa: F401
from .solvers import mock  # noqa: F401
from .tasks import smoke  # noqa: F401
