"""Small command-line helpers for local repository diagnostics."""

from __future__ import annotations

import argparse

from . import __version__


def main() -> int:
    """Print the installed evaluation-suite version."""
    parser = argparse.ArgumentParser(description="AI-Native-Evals diagnostics")
    parser.add_argument("--version", action="store_true", help="print the package version")
    args = parser.parse_args()
    if args.version:
        print(__version__)
    else:
        parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
