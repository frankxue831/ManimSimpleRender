"""Validate a step-only revision without modifying or rendering either input."""

import argparse
import copy
import json
import math
import sys

from concept_render.model import validate


def validate_revision(original: dict, candidate: dict) -> dict:
    """Return actual step changes or raise ValueError; leave both inputs unchanged.

    This checks revision scope and scene grammar, not whether the feedback was
    understood correctly. The caller must separately review that meaning.
    """
    for label, scene in (("original", original), ("candidate", candidate)):
        try:
            validate(scene)
        except ValueError as error:
            raise ValueError(f"Invalid {label}: {error}") from error

    for field in ("version", "title", "font", "nodes", "edges"):
        if original[field] != candidate[field]:
            raise ValueError(f"Revision must preserve {field}")
    if len(original["steps"]) != len(candidate["steps"]):
        raise ValueError("Revision must preserve step count")

    original_shown = {node for step in original["steps"] for node in step["show"]}
    candidate_shown = {node for step in candidate["steps"] for node in step["show"]}
    if original_shown != candidate_shown:
        raise ValueError("Revision must preserve the set of shown nodes")

    changes = []
    for number, (before, after) in enumerate(zip(original["steps"], candidate["steps"]), start=1):
        for field in ("caption", "duration", "show", "highlight"):
            # Highlights use membership; reveal order affects the animation.
            if field == "highlight" and set(before[field]) == set(after[field]):
                continue
            if before[field] != after[field]:
                changes.append(
                    {
                        "step": number,
                        "field": field,
                        "before": copy.deepcopy(before[field]),
                        "after": copy.deepcopy(after[field]),
                    }
                )
    if not changes:
        raise ValueError("Revision must contain at least one step change")
    return {
        "changes": changes,
        "duration_before": math.fsum(step["duration"] for step in original["steps"]),
        "duration_after": math.fsum(step["duration"] for step in candidate["steps"]),
    }


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _load(path):
    with open(path, "rb") as source:
        raw = source.read(64 * 1024 + 1)
    if len(raw) > 64 * 1024:
        raise ValueError("Concept input exceeds 64 KiB")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", required=True)
    parser.add_argument("--candidate", required=True)
    args = parser.parse_args(argv)
    try:
        report = validate_revision(_load(args.original), _load(args.candidate))
    except OSError as error:
        print(f"concept revision unavailable: {' '.join(str(error).split())}", file=sys.stderr)
        return 2
    except (ValueError, RecursionError) as error:
        diagnostic = " ".join(str(error).split())
        print(f"concept revision: {diagnostic}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
