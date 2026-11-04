"""Isolated bootstrap for receiving commands; invoke this FILE with python -I.

The product directory and Python environment must already be trusted. This avoids
project/CWD/PYTHONPATH imports before inspection; it is not a Python sandbox.
"""

import sys


def main():
    if not sys.flags.isolated:
        print(
            "Use python -I /trusted/product/scene_production/launcher.py COMMAND ...",
            file=sys.stderr,
        )
        return 2
    # -I has removed CWD, PYTHONPATH and the user site before Python starts us.
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "portable":
        from scene_production.portable import main as run
    elif command == "revision":
        from scene_production.revision import main as run
    elif command == "capture":
        from scene_production.frame_capture import main as run
    elif command == "render":
        from scene_production.__main__ import main as run
    else:
        message = "Commands: portable, revision, render, capture"
        print(message, file=sys.stdout if command == "--help" else sys.stderr)
        return 0 if command == "--help" else 2
    sys.argv = [sys.argv[0], *sys.argv[2:]]
    return run()


if __name__ == "__main__":
    sys.exit(main())
