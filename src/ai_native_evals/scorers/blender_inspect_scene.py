"""Inspect-facing Blender scene inspection script (run inside Blender)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy


def main() -> int:
    args = list(sys.argv)
    if "--" in args:
        args = args[args.index("--") + 1 :]
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-file", required=True, type=Path)
    ns = parser.parse_args(args)

    objects = []
    for obj in bpy.data.objects:
        objects.append(
            {
                "name": obj.name,
                "type": obj.type,
                "location": list(obj.location),
                "rotation_euler": list(obj.rotation_euler),
                "scale": list(obj.scale),
            }
        )
    result = {"status": "succeeded", "details": {"objects": objects}}
    ns.result_file.parent.mkdir(parents=True, exist_ok=True)
    ns.result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
