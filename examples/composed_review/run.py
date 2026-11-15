"""Assemble and review a synthetic post-render composite, without auto-approval.

From a trusted checkout, run the module with --output-dir pointing to a new directory.
This is a project-specific FFmpeg recipe, not an MSR compositor or a sandbox.
"""

import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess

from examples.silent_review import run as silent_review
from scene_production import editorial

EXPECTED_RATE = Fraction(15, 1)
EXPECTED_FRAMES = 30
CONTEXT_FRAMES = 15


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def save(path, data):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, allow_nan=False)
        stream.write("\n")


def command(argv, log, timeout=120):
    """Run exact argv once; retain diagnostics, and fail rather than retry."""
    with Path(log).open("x", encoding="utf-8") as stream:
        result = subprocess.run(
            argv, stdout=stream, stderr=subprocess.STDOUT, check=False, timeout=timeout
        )
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with exit {result.returncode}; see {log}")


def probe(video):
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    data = json.loads(completed.stdout)
    streams = data.get("streams", [])
    videos = [item for item in streams if item.get("codec_type") == "video"]
    if len(videos) != 1 or any(item.get("codec_type") == "audio" for item in streams):
        raise ValueError("Expected exactly one video and no audio")
    video_stream = videos[0]
    if (
        (video_stream.get("width"), video_stream.get("height")) != (854, 480)
        or Fraction(video_stream.get("avg_frame_rate", "0/1")) != EXPECTED_RATE
        or video_stream.get("nb_frames") != str(EXPECTED_FRAMES)
    ):
        raise ValueError("Unexpected composed video dimensions, rate, or frame count")
    return {"width": 854, "height": 480, "rate": "15/1", "frames": EXPECTED_FRAMES}


def frame_signatures(video, output, *, crop=False, count=EXPECTED_FRAMES):
    """Compare decoded frames and normalized ffmpeg timestamps, not source PTS."""
    command(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-n",
            "-i",
            str(video),
            *(["-vf", "crop=160:90:674:20", "-frames:v", "15"] if crop else []),
            "-pix_fmt",
            "yuv420p",
            "-f",
            "framemd5",
            str(output),
        ],
        str(output) + ".log",
    )
    rows = [
        tuple(field.strip() for field in line.split(","))
        for line in Path(output).read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    if len(rows) != count:
        raise ValueError("Unexpected decoded frame count")
    return rows


def verify_insert(overlay, composite, root, name):
    source = frame_signatures(overlay, root / (name + "-source.framemd5"), count=15)
    cropped = frame_signatures(composite, root / (name + "-insert.framemd5"), crop=True, count=15)
    if source != cropped:
        raise RuntimeError("Composite insert pixels or normalized timing differ from source")
    if len({row[-1] for row in source}) < 2:
        raise RuntimeError("Synthetic insert did not contain visible motion")


def fixture_assessment(evidence):
    return {
        "reviewer": "example-fixture-not-an-independent-reviewer",
        "scope": (
            "FIXTURE ONLY: this record is attached to the final composite. "
            "No viewing, listening, or semantic acceptance has been performed."
        ),
        "decision": "needs_review",
        "findings": [
            {
                "id": "F1",
                "time_seconds": 1.5,
                "section": "lookup",
                "observed": "The fixture lookup correction requires editorial inspection.",
                "acceptance_question": "Is the selected row clear in the actual final composite?",
            }
        ],
        "evidence": [str(evidence)],
    }


def compose(source, overlay, output, log):
    """One concrete authored assembly; lossless evidence, not delivery advice."""
    before_source, before_overlay = digest(source), digest(overlay)
    argv = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-n",
        "-i",
        str(source),
        "-i",
        str(overlay),
        "-filter_complex",
        "[0:v][1:v]overlay=674:20:enable='lt(n,15)':eof_action=pass:repeatlast=0[v]",
        "-map",
        "[v]",
        "-c:v",
        "libx264",
        "-crf",
        "0",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]
    command(argv, log)
    if digest(source) != before_source or digest(overlay) != before_overlay:
        raise RuntimeError("Assembly input changed during encoding")
    return {
        "argv": argv,
        "source_sha256": before_source,
        "overlay_sha256": before_overlay,
        "output_sha256": digest(output),
    }


def execute(output):
    root = Path(output).expanduser().resolve()
    root.mkdir(exist_ok=False)
    # Existing example captures observed render exits, performs both full renders and
    # a silent raw-scene audit, and labels its own editorial records as fixtures.
    scene = silent_review.execute(root / "scene-workflow")
    if scene["audit_status"] != "unchanged_non_target":
        raise RuntimeError("Raw scene audit did not prove unchanged non-target frames")
    baseline = Path(scene["baseline_video"])
    candidate = Path(scene["candidate_video"])
    probe(baseline)
    probe(candidate)
    overlay = root / "synthetic-motion.mp4"
    generate = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-n",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=160x90:rate=15:duration=1",
        "-an",
        "-c:v",
        "libx264",
        "-crf",
        "0",
        "-pix_fmt",
        "yuv420p",
        str(overlay),
    ]
    command(generate, root / "synthetic-motion.log")
    if not overlay.is_file():
        raise RuntimeError("Synthetic motion insert was not created")
    assembled_baseline, assembled_candidate = (
        root / "baseline-composite.mp4",
        root / "candidate-composite.mp4",
    )
    recipes = {
        "synthetic_motion_argv": generate,
        "baseline": compose(baseline, overlay, assembled_baseline, root / "baseline-assembly.log"),
        "candidate": compose(
            candidate, overlay, assembled_candidate, root / "candidate-assembly.log"
        ),
    }
    probe(assembled_baseline)
    probe(assembled_candidate)
    bframes = frame_signatures(assembled_baseline, root / "baseline-composite.framemd5")
    cframes = frame_signatures(assembled_candidate, root / "candidate-composite.framemd5")
    verify_insert(overlay, assembled_baseline, root, "baseline")
    verify_insert(overlay, assembled_candidate, root, "candidate")
    if bframes[:CONTEXT_FRAMES] != cframes[:CONTEXT_FRAMES]:
        raise RuntimeError("Protected composite context frames or normalized timestamps changed")
    if bframes[CONTEXT_FRAMES:] == cframes[CONTEXT_FRAMES:]:
        raise RuntimeError("The composed candidate did not reflect the expected edit")
    versions = {}
    for name in ("ffmpeg", "ffprobe"):
        info = subprocess.run(
            [name, "-version"], capture_output=True, text=True, check=True, timeout=30
        )
        versions[name] = info.stdout.splitlines()[0]
    assembly_record = root / "assembly.json"
    save(
        assembly_record,
        {"versions": versions, "recipes": recipes, "synthetic_motion_sha256": digest(overlay)},
    )
    old = root / "composite-review-baseline.json"
    new = root / "composite-review-candidate.json"
    editorial.record_review(assembled_baseline, fixture_assessment(assembly_record), old)
    stale = editorial.compare(old, assembled_candidate)
    if stale["assessment_current"] or not stale["candidate_requires_fresh_assessment"]:
        raise RuntimeError("Changed composite did not invalidate the prior review")
    editorial.record_review(
        assembled_candidate, fixture_assessment(assembly_record), new, previous=old
    )
    current = editorial.compare(new, assembled_candidate)
    result = {
        "fixture_only": True,
        "baseline_composite": str(assembled_baseline),
        "candidate_composite": str(assembled_candidate),
        "assembly_record": str(assembly_record),
        "raw_scene_audit": scene["audit_status"],
        "protected_composite_frames": CONTEXT_FRAMES,
        "baseline_review": str(old),
        "candidate_review": str(new),
        "baseline_review_current_for_candidate": stale["assessment_current"],
        "candidate_review_current": current["assessment_current"],
        "candidate_decision": current["decision"],
        "readiness": current["readiness"],
        "notice": (
            "Synthetic insert; project-specific FFmpeg assembly, not an MSR compositor. "
            "Raw scene audit does not audit the final composite. Fixture records are not "
            "visual review or approval; independent full playback remains necessary."
        ),
    }
    save(root / "example-result.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, help="New directory in an existing parent")
    print(json.dumps(execute(parser.parse_args(argv).output_dir), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
