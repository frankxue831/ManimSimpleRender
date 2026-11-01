"""Single production CLI."""

import argparse
import json
import sys
from . import render


def _error(exc):
    print(str(exc), file=sys.stderr)
    if getattr(exc, "receipt_path", None):
        print("receipt: " + str(exc.receipt_path), file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Render and inspect a trusted Manim Scene",
    )
    for name in ("scene-file", "scene", "timeline", "output-dir"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--quality", default="qm")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--tool-timeout", type=float, default=30)
    args = parser.parse_args()
    try:
        print(json.dumps(render(**vars(args)), indent=2))
    except Exception as exc:
        _error(exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
