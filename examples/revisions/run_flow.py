"""Executable authored example: explicit feedback, scoped edit, full renders, audit.

Run from the source checkout with `python -m examples.revisions.run_flow --output DIR`.
No language model is called; the feedback, target and replacement are authored here.
"""

import argparse
import json
from pathlib import Path
import shutil

from scene_production import render, digest
from scene_production import revision


def execute(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    examples = Path(__file__).resolve().parents[1]
    case = output / "case"
    (case / "scenes").mkdir(parents=True)
    (case / "revisions").mkdir()
    for relative in (
        "revision-flow.manifest.json",
        "scenes/revision_flow.py",
        "scenes/scene_font.py",
        "revisions/flow.timeline.json",
    ):
        shutil.copyfile(examples / relative, case / relative)
    source = case / "scenes/revision_flow.py"
    timeline = case / "revisions/flow.timeline.json"
    manifest = case / "revision-flow.manifest.json"

    def produce():
        result = render(
            source,
            "RevisionFlow",
            timeline,
            output / "runs",
            quality="qm",
            record_scene_frames=True,
        )
        return Path(result["run_dir"]) / "result.json"

    before = produce()
    snapshot = revision.capture(manifest, before, output / "versions")
    plan = revision.locate(
        snapshot,
        "distinction",
        "Show reinspection separately and add two seconds reading time; preserve the tail.",
        acknowledge=["tail"],
    )
    (output / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    edits = json.loads((examples / "revisions/flow.edits.json").read_text())
    applied = revision.apply(plan, edits)
    # Author-maintained inspection configuration, not animation-driving data.
    shutil.copyfile(examples / "revisions/flow-revised.timeline.json", timeline)
    after = produce()
    audit = revision.audit(snapshot, after, ["distinction"])
    (output / "audit.json").write_text(json.dumps(audit, indent=2))
    candidate_source, candidate_timeline = digest(source), digest(timeline)
    rollback = revision.restore(snapshot, after)
    replay = revision.locate(snapshot, "distinction", plan["feedback"], acknowledge=["tail"])
    revision.apply(replay, edits)
    shutil.copyfile(examples / "revisions/flow-revised.timeline.json", timeline)
    exact_replay = digest(source) == candidate_source and digest(timeline) == candidate_timeline
    if not exact_replay:
        raise RuntimeError("replay differs from retained candidate")
    result = {
        "baseline_receipt": str(before),
        "candidate_receipt": str(after),
        "snapshot": str(snapshot),
        "apply": applied,
        "audit": audit,
        "rollback": rollback,
        "replay_identical": exact_replay,
        "semantic_author": "assistant-authored example; no feedback interpretation model",
        "human_review_required": True,
    }
    (output / "example-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="New empty output directory")
    result = execute(parser.parse_args().output)
    print(
        json.dumps(
            {
                "status": result["audit"]["status"],
                "replay_identical": result["replay_identical"],
                "human_review_required": True,
            },
            indent=2,
        )
    )
    return 0 if result["audit"]["status"] == "unchanged_non_target" else 2


if __name__ == "__main__":
    raise SystemExit(main())
