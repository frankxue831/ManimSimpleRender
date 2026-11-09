"""Record local editorial judgments against exact delivered video bytes.

These unsigned records do not authenticate reviewers or prove playback, quality,
comprehension, or render completion. Trusted local files; no concurrent writers.
"""

import argparse
import json
import math
from pathlib import Path
import re

from . import digest, pairs

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 4000


def _entry(path):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("artifact must be a file")
    return {"path": str(path), "sha256": digest(path)}


def _valid_entry(entry):
    return (
        isinstance(entry, dict)
        and set(entry) == {"path", "sha256"}
        and isinstance(entry["path"], str)
        and Path(entry["path"]).is_absolute()
        and isinstance(entry["sha256"], str)
        and SHA.fullmatch(entry["sha256"]) is not None
    )


def _unchanged(entry):
    try:
        return _entry(entry["path"])["sha256"] == entry["sha256"]
    except (OSError, ValueError):
        return False


def _assessment(data):
    if not isinstance(data, dict) or set(data) != {
        "reviewer",
        "scope",
        "decision",
        "findings",
        "evidence",
    }:
        raise ValueError("assessment requires reviewer, scope, decision, findings, and evidence")
    if (
        not _text(data["reviewer"])
        or not _text(data["scope"])
        or data["decision"]
        not in (
            "approved",
            "changes_requested",
            "needs_review",
        )
    ):
        raise ValueError("invalid reviewer or decision")
    findings = data["findings"]
    if not isinstance(findings, list) or len(findings) > 100:
        raise ValueError("findings must be a list of at most 100 items")
    if data["decision"] == "changes_requested" and not findings:
        raise ValueError("changes_requested requires a finding")
    if data["decision"] == "approved" and findings:
        raise ValueError("approved assessment cannot contain unresolved findings")
    ids = set()
    for finding in findings:
        if (
            not isinstance(finding, dict)
            or not {"id", "observed", "acceptance_question"} <= finding.keys()
            or not finding.keys()
            <= {"id", "observed", "acceptance_question", "section", "time_seconds"}
            or not isinstance(finding["id"], str)
            or not ID.fullmatch(finding["id"])
            or finding["id"] in ids
            or not _text(finding["observed"])
            or not _text(finding["acceptance_question"])
        ):
            raise ValueError("invalid or duplicate finding")
        if "section" in finding and not _text(finding["section"]):
            raise ValueError("invalid section")
        if "time_seconds" in finding:
            t = finding["time_seconds"]
            try:
                valid_time = type(t) in (int, float) and math.isfinite(t) and t >= 0
            except OverflowError:
                valid_time = False
            if not valid_time:
                raise ValueError("invalid finding time")
        ids.add(finding["id"])
    if not isinstance(data["evidence"], list) or len(data["evidence"]) > 100:
        raise ValueError("evidence must be a list of at most 100 paths")
    return ids


def _load(path):
    raw = Path(path).read_bytes()
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("review exceeds 4 MiB")
    data = json.loads(raw, object_pairs_hook=pairs)
    if not isinstance(data, dict) or set(data) != {
        "schema_version",
        "video",
        "assessment",
        "evidence",
        "previous",
    }:
        raise ValueError("invalid review record")
    if (
        type(data["schema_version"]) is not int
        or data["schema_version"] != 1
        or not _valid_entry(data["video"])
    ):
        raise ValueError("invalid review version or video")
    assessment = dict(data["assessment"])
    assessment["evidence"] = []
    _assessment(assessment)
    if not isinstance(data["evidence"], list) or not all(_valid_entry(x) for x in data["evidence"]):
        raise ValueError("invalid evidence references")
    previous = data["previous"]
    if previous is not None and (
        not isinstance(previous, dict)
        or set(previous) != {"review", "addressed_findings"}
        or not _valid_entry(previous["review"])
        or not isinstance(previous["addressed_findings"], list)
        or any(
            not isinstance(x, str) or not ID.fullmatch(x) for x in previous["addressed_findings"]
        )
        or len(set(previous["addressed_findings"])) != len(previous["addressed_findings"])
    ):
        raise ValueError("invalid previous review reference")
    return data


def record_review(video, assessment, output, previous=None, addresses=()):
    """Create an exclusive review record; no file is overwritten."""
    _assessment(assessment)
    video_entry = _entry(video)
    evidence = [_entry(path) for path in assessment["evidence"]]
    addresses = list(addresses)
    prior = None
    if previous is not None:
        prior_entry = _entry(previous)
        prior_data = _load(previous)
        valid = {item["id"] for item in prior_data["assessment"]["findings"]}
        if not addresses or len(set(addresses)) != len(addresses) or not set(addresses) <= valid:
            raise ValueError("addresses must name distinct findings in previous review")
        if not _unchanged(prior_entry):
            raise ValueError("previous review changed during recording")
        prior = {"review": prior_entry, "addressed_findings": addresses}
    elif addresses:
        raise ValueError("addresses requires a previous review")
    result = {
        "schema_version": 1,
        "video": video_entry,
        "assessment": {k: assessment[k] for k in ("reviewer", "scope", "decision", "findings")},
        "evidence": evidence,
        "previous": prior,
    }
    if not all(_unchanged(entry) for entry in [video_entry, *evidence]):
        raise ValueError("artifact changed during recording")
    with Path(output).open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    return result


def compare(review, candidate):
    """Compare a candidate with a recorded judgment, without making a new one."""
    data = _load(review)
    candidate_entry = _entry(candidate)
    current = candidate_entry["sha256"] == data["video"]["sha256"]
    return {
        "review": _entry(review),
        "reviewed_video": data["video"],
        "candidate": candidate_entry,
        "assessment_current": current,
        "candidate_requires_fresh_assessment": not current,
        "readiness": "not_determined",
        "reviewed_video_available_and_unchanged": _unchanged(data["video"]),
        "reviewer": data["assessment"]["reviewer"],
        "scope": data["assessment"]["scope"],
        "decision": data["assessment"]["decision"],
        "findings": data["assessment"]["findings"],
        "evidence": [{**entry, "unchanged": _unchanged(entry)} for entry in data["evidence"]],
        "previous": data["previous"],
        "previous_review_unchanged": (
            None if data["previous"] is None else _unchanged(data["previous"]["review"])
        ),
        "notice": (
            "Byte identity is not authentication, playback, comprehension, "
            "or editorial approval."
        ),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    record = sub.add_parser("record")
    for option in ("video", "assessment", "output"):
        record.add_argument("--" + option, required=True)
    record.add_argument("--previous")
    record.add_argument("--addresses", nargs="+", default=[])
    check = sub.add_parser("compare")
    check.add_argument("--review", required=True)
    check.add_argument("--candidate", required=True)
    args = parser.parse_args(argv)
    try:
        if args.operation == "record":
            with open(args.assessment, encoding="utf-8") as handle:
                assessment = json.load(handle, object_pairs_hook=pairs)
            result = record_review(
                args.video, assessment, args.output, args.previous, args.addresses
            )
        else:
            result = compare(args.review, args.candidate)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(2, f"editorial review failed: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
