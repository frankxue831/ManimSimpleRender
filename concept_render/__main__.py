"""Source-checkout command for silent concept rendering."""

import argparse
import json
import sys

from .render_adapter import render


def main():
    parser = argparse.ArgumentParser(description="Render a silent concept draft")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    try:
        result = render(args.input, args.output_dir, timeout=args.timeout)
    except Exception as exc:
        diagnostic = " ".join(str(exc).split())
        print("concept render: " + diagnostic, file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
