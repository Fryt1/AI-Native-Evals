"""Inspect AI entry-point loader for the evaluation suite."""

# Importing these modules registers their Inspect objects. Keeping the imports
# here makes package discovery explicit and gives the package one stable entry
# point without modifying Inspect AI itself.
from .scorers import ainative, filesystem, hello_world, multi_dcc_host_verifier  # noqa: F401
from .solvers import codex, mock  # noqa: F401
from .tasks import codex_file_smoke, multi_dcc_roundtrip, smoke  # noqa: F401



