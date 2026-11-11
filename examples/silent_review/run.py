"""Real silent renders and a fixture review, always ending in needs_review.

Run from a trusted source checkout:
python -m examples.silent_review.run --output-dir /existing/parent/new-example
No LLM, TTS, credentials, private assets, or external review service is used.
"""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

from scene_production import digest, editorial, revision
from scene_production.completion import read_completion

FIXTURES = Path(__file__).resolve().parent
CHECKOUT = FIXTURES.parents[1]


def save(path, data):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, allow_nan=False)
        stream.write("\n")


def render_attempt(project, output, name):
    """Consume this process's stdout only after its observed exit; never poll receipts."""
    command = [
        sys.executable,
        "-m",
        "scene_production.agent",
        "render",
        "--scene-file",
        str(project / "scene.py"),
        "--scene",
        "SilentReview",
        "--timeline",
        str(project / "timeline.json"),
        "--output-dir",
        str(output / name),
        "--quality",
        "ql",
        "--timeout",
        "120",
        "--tool-timeout",
        "30",
        "--record-scene-frames",
    ]
    completed = subprocess.run(command, cwd=CHECKOUT, capture_output=True, check=False)
    # Retain actual transport evidence separately from agent-owned attempt files.
    (output / f"{name}.stdout.jsonl").write_bytes(completed.stdout)
    (output / f"{name}.stderr.log").write_bytes(completed.stderr)
    completion = read_completion(
        completed.stdout, completed.returncode, expected_operation="render"
    )
    save(
        output / f"{name}.completion.json",
        {
            "process_returncode": completed.returncode,
            "status": completion.status,
            "reason": completion.reason,
            "run_token": completion.run_token,
        },
    )
    if completion.status != "succeeded":
        raise RuntimeError(
            f"{name} completion is {completion.status}: {completion.reason}. "
            "Retain this attempt and investigate; do not automatically retry."
        )
    return completion.terminal["artifacts"]


def artifact(artifacts, key):
    """Check retained bytes separately, after accepting process completion."""
    entry = artifacts[key]
    path = Path(entry["path"])
    if not path.is_file() or digest(path) != entry["sha256"]:
        raise ValueError(f"retained {key} artifact is missing or changed")
    return path


def evidence(artifacts):
    # These are attached bytes, not claims that a reviewer looked at them.
    keys = ["receipt", "source", "timeline", "frame_trace"]
    keys.extend(sorted(key for key in artifacts if key.startswith("frame_") and key[6:].isdigit()))
    return [str(artifact(artifacts, key)) for key in keys]


def execute(output):
    output = Path(output).expanduser().resolve()
    output.mkdir(exist_ok=False)  # Parent must exist; never reuse an earlier run.
    project = output / "project"
    project.mkdir()
    for name in (
        "scene.py",
        "timeline.json",
        "manifest.json",
        "candidate-body.txt",
        "fixture-finding.json",
    ):
        shutil.copyfile(FIXTURES / name, project / name)

    before = render_attempt(project, output, "baseline")
    before_receipt, before_video = artifact(before, "receipt"), artifact(before, "video")
    snapshot = revision.capture(project / "manifest.json", before_receipt, output / "versions")
    finding = json.loads((project / "fixture-finding.json").read_text(encoding="utf-8"))
    finding["evidence"] = evidence(before)
    first = output / "review-baseline.json"
    editorial.record_review(before_video, finding, first)

    target = "lookup"  # Author-selected, not inferred from feedback or sample images.
    plan = revision.locate(snapshot, target, finding["findings"][0]["acceptance_question"])
    save(output / "plan.json", plan)
    edits = {target: (project / "candidate-body.txt").read_text(encoding="utf-8")}
    save(output / "edits.json", edits)
    save(output / "applied.json", revision.apply(plan, edits))
    after = render_attempt(project, output, "candidate")
    after_receipt, after_video = artifact(after, "receipt"), artifact(after, "video")
    audit = revision.audit(snapshot, after_receipt, [target])
    save(output / "audit.json", audit)

    stale = editorial.compare(first, after_video)
    save(output / "comparison-previous.json", stale)
    if stale["assessment_current"] or not stale["candidate_requires_fresh_assessment"]:
        raise RuntimeError(
            "Expected different candidate bytes and a non-current baseline assessment"
        )
    candidate_assessment = {
        "reviewer": "example-fixture-not-an-independent-reviewer",
        "scope": (
            "FIXTURE ONLY: candidate bytes and machine evidence are linked. "
            "No playback or semantic review was performed; F1 remains outstanding."
        ),
        "decision": "needs_review",
        "findings": finding["findings"],
        "evidence": [*evidence(after), str(output / "audit.json")],
    }
    second = output / "review-candidate.json"
    editorial.record_review(
        after_video, candidate_assessment, second, previous=first, addresses=["F1"]
    )
    current = editorial.compare(second, after_video)
    save(output / "comparison-candidate.json", current)
    result = {
        "fixture_only": True,
        "baseline_video": str(before_video),
        "candidate_video": str(after_video),
        "baseline_receipt": str(before_receipt),
        "candidate_receipt": str(after_receipt),
        "snapshot": snapshot,
        "baseline_review": str(first),
        "candidate_review": str(second),
        "audit_status": audit["status"],
        "previous_assessment_current": stale["assessment_current"],
        "candidate_assessment_current": current["assessment_current"],
        "candidate_decision": current["decision"],
        "readiness": current["readiness"],
        "human_review_required": True,
        "notice": "Machine evidence and fixture findings never establish editorial approval.",
    }
    save(output / "example-result.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, help="New directory in an existing parent")
    result = execute(parser.parse_args(argv).output_dir)
    print(json.dumps(result, indent=2))
    # Exit 0 means the machine demonstration ran, never editorial approval.
    return 0 if result["audit_status"] == "unchanged_non_target" else 2


if __name__ == "__main__":
    raise SystemExit(main())
