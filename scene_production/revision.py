"""Explicit scene IDs and guarded revisions of trusted local Manim sources.

No language understanding, rendering cache, or semantic quality judgement.
Snapshots and receipts are local unsigned records, not a security boundary.
"""

import argparse
import os
import ast
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from . import digest, load_timeline, pairs, _verify_file

MARKER = re.compile(r"^\s*# scene:(start|end) ([a-z][a-z0-9_-]*)\s*$")


def read(path):
    raw = Path(path).read_bytes()
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("JSON input exceeds 2 MiB")
    return json.loads(raw, object_pairs_hook=pairs)


def regions(source):
    lines = source.splitlines(keepends=True)
    found, opened = {}, None
    offset = 0
    for lineno, line in enumerate(lines, 1):
        match = MARKER.fullmatch(line.rstrip("\r\n"))
        if match:
            kind, name = match.groups()
            if kind == "start":
                if opened or name in found:
                    raise ValueError("nested or duplicate scene marker")
                opened = (name, offset + len(line), lineno + 1)
            else:
                if not opened or opened[0] != name:
                    raise ValueError("unmatched scene marker")
                found[name] = (opened[1], offset, opened[2], lineno - 1)
                opened = None
        offset += len(line)
    if opened or not found:
        raise ValueError("missing scene markers")
    return found


def local(root, value):
    p = (root / value).resolve()
    if not p.is_relative_to(root) or not p.is_file():
        raise ValueError("manifest files must exist under its directory")
    return p


def manifest(path):
    path = Path(path).resolve()
    m = read(path)
    if m.get("version") != 1 or type(m["version"]) is not int:
        raise ValueError("manifest version must be 1")
    root = path.parent
    files = {k: local(root, m[k]) for k in ("source", "timeline")}
    deps = [local(root, v) for v in m["dependencies"]]
    if len(set([path, *files.values(), *deps])) != 3 + len(deps):
        raise ValueError("manifest file roles must be distinct")
    brief = m["brief"]
    if any(not brief.get(k) for k in ("audience", "goal", "sources", "pitfalls")):
        raise ValueError("brief requires audience, goal, sources and pitfalls")
    segs = m["segments"]
    ids = [s["id"] for s in segs]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("duplicate or empty segment IDs")
    source = files["source"].read_bytes().decode("utf-8")
    if set(ids) != set(regions(source)):
        raise ValueError("manifest IDs disagree with source markers")
    timeline = load_timeline(files["timeline"].read_bytes())
    if ids != [s["label"] for s in timeline["segments"]]:
        raise ValueError("manifest IDs must match ordered timeline labels")
    for s in segs:
        if (
            not s["captions"]
            or not s["objects"]
            or any(d not in ids or ids.index(d) >= ids.index(s["id"]) for d in s["depends_on"])
        ):
            raise ValueError("segment requires captions, objects and valid dependencies")
    return m, path, files, deps, timeline


def recorded(path):
    path = Path(path).resolve()
    d = read(path)
    if path.name != "result.json" or d.get("status") != "success":
        raise ValueError("expected canonical successful render receipt")
    root = path.parent
    if Path(d["run_dir"]).resolve() != root:
        raise ValueError("receipt root mismatch")
    for p, sha in (
        (root / "source.py", d["source_sha256"]),
        (root / "timeline.json", d["timeline_sha256"]),
        (Path(d["video"]), d["video_sha256"]),
    ):
        _verify_file(str(p), root, sha)
    return d


def rendered_scene(receipt):
    command = receipt.get("command", [])
    positions = [i for i, v in enumerate(command) if v == receipt["source"]]
    if len(positions) != 1 or positions[0] + 1 >= len(command):
        raise ValueError("render receipt has no unambiguous Scene class command")
    return command[positions[0] + 1]


def capture(manifest_path, receipt_path, store):
    m, path, files, deps, timeline = manifest(manifest_path)
    r = recorded(receipt_path)
    if rendered_scene(r) != m["scene"]:
        raise ValueError("rendered Scene class disagrees with manifest")
    if (
        digest(files["source"]) != r["source_sha256"]
        or digest(files["timeline"]) != r["timeline_sha256"]
    ):
        raise ValueError("current inputs disagree with recorded render")
    tracked = [path, *files.values(), *deps]
    state = {str(p): digest(p) for p in tracked}
    root = Path(store).resolve()
    root.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="version-", dir=root))
    copies = {}
    for i, p in enumerate(tracked):
        copy = out / ("%03d" % i + p.suffix)
        shutil.copyfile(p, copy)
        if digest(copy) != state[str(p)]:
            raise ValueError("file changed during capture")
        copies[str(p)] = {"copy": copy.name, "sha256": state[str(p)]}
    result = {
        "version": 1,
        "manifest": str(path),
        "source": str(files["source"]),
        "timeline": str(files["timeline"]),
        "files": copies,
        "receipt": str(Path(receipt_path).resolve()),
        "receipt_sha256": digest(receipt_path),
        "video_sha256": r["video_sha256"],
        "segments": timeline["segments"],
        "notice": (
            "Unsigned local snapshot; dependency identity captured now, "
            "not proof of render environment."
        ),
    }
    (out / "snapshot.json").write_text(json.dumps(result, indent=2))
    return str(out / "snapshot.json")


def baseline(path):
    path = Path(path).resolve()
    b = read(path)
    for entry in b["files"].values():
        _verify_file(str(path.parent / entry["copy"]), path.parent, entry["sha256"])
    if digest(b["receipt"]) != b["receipt_sha256"]:
        raise ValueError("baseline receipt changed")
    recorded(b["receipt"])
    return b, path


def check_static(b):
    for p, entry in b["files"].items():
        if p not in (b["source"], b["timeline"]) and digest(p) != entry["sha256"]:
            raise ValueError("manifest or dependency changed: " + p)


def locate(snapshot, target, feedback, acknowledge=()):
    b, path = baseline(snapshot)
    check_static(b)
    m, mp, files, deps, tl = manifest(b["manifest"])
    if target not in {s["id"] for s in m["segments"]}:
        raise ValueError("unknown target: " + target)
    if (
        digest(files["source"]) != b["files"][b["source"]]["sha256"]
        or digest(files["timeline"]) != b["files"][b["timeline"]]["sha256"]
    ):
        raise ValueError("stale baseline: current source/timeline changed")
    source = files["source"].read_bytes().decode("utf-8")
    start, end, first, last = regions(source)[target]
    segment = next(s for s in m["segments"] if s["id"] == target)
    window = next(s for s in tl["segments"] if s["label"] == target)
    r = recorded(b["receipt"])
    previews = [f for f in r.get("frames", []) if window["start"] < f["time"] < window["end"]]
    affected = set()
    changed = {target}
    while True:
        more = {s["id"] for s in m["segments"] if changed.intersection(s["depends_on"])}
        more -= changed
        if not more:
            break
        affected.update(more)
        changed.update(more)
    affected = sorted(affected)
    if not set(acknowledge).issubset(affected):
        raise ValueError("acknowledgement includes unknown dependent")
    for frame in previews:
        _verify_file(frame["path"], Path(b["receipt"]).parent, frame["sha256"])
    return {
        "version": 1,
        "snapshot": str(path),
        "snapshot_sha256": digest(path),
        "target": target,
        "feedback": feedback,
        "scope": [target],
        "dependent_segments": affected,
        "acknowledged_dependents": sorted(set(acknowledge)),
        "dependency_policy": "unchanged; changes require a new baseline and explicit review",
        "context": {
            **segment,
            "first_line": first,
            "last_line": last,
            "code": source[start:end],
            "time": window,
            "previews": previews,
        },
        "notice": (
            "Caller/assistant selects target and writes edits. "
            "No semantic interpretation of feedback."
        ),
    }


def apply(plan, edits):
    if digest(plan["snapshot"]) != plan["snapshot_sha256"]:
        raise ValueError("snapshot changed")
    b, path = baseline(plan["snapshot"])
    check_static(b)
    if set(edits) != set(plan["scope"]):
        raise ValueError("edits must exactly match explicit scope")
    if not set(plan["dependent_segments"]).issubset(plan.get("acknowledged_dependents", [])):
        raise ValueError("dependent scenes require explicit acknowledgement and regression checks")
    for p in (b["source"], b["timeline"]):
        if digest(p) != b["files"][p]["sha256"]:
            raise ValueError("stale baseline: current inputs changed")
    src = Path(b["source"]).read_bytes().decode("utf-8")
    ranges = regions(src)
    updated = src
    for name in sorted(edits, key=lambda n: ranges[n][0], reverse=True):
        replacement = edits[name]
        if any(MARKER.fullmatch(line) for line in replacement.splitlines()):
            raise ValueError("replacement cannot inject markers")
        a, z, _, _ = ranges[name]
        updated = updated[:a] + replacement + updated[z:]
    ast.parse(updated)
    if set(regions(updated)) != set(ranges):
        raise ValueError("edited markers changed")
    if updated == src:
        raise ValueError("no change")
    target = Path(b["source"])
    fd, name = tempfile.mkstemp(prefix=".scene-revision-", dir=target.parent)
    os.close(fd)
    tmp = Path(name)
    try:
        tmp.write_bytes(updated.encode("utf-8"))
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)
    return {
        "feedback": plan["feedback"],
        "target": plan["target"],
        "snapshot": str(path),
        "before_source_sha256": hashlib.sha256(src.encode()).hexdigest(),
        "after_source_sha256": digest(target),
        "semantic_author": "caller/assistant",
        "scope": plan["scope"],
    }


def check_scope(snapshot, targets):
    b, path = baseline(snapshot)
    check_static(b)
    old = (path.parent / b["files"][b["source"]]["copy"]).read_bytes().decode("utf-8")
    new = Path(b["source"]).read_bytes().decode("utf-8")
    a = regions(old)
    z = regions(new)
    if set(a) != set(z) or not targets or any(t not in a for t in targets):
        raise ValueError("invalid target/region set")

    def mask(s, r):
        for t in sorted(targets, key=lambda t: r[t][0], reverse=True):
            x, y, _, _ = r[t]
            s = s[:x] + "<allowed scene body>\n" + s[y:]
        return s

    if mask(old, a) != mask(new, z):
        raise ValueError("source changed outside declared target")
    manifest(b["manifest"])
    return b, path


def frame_sequence(video, start, end, fps, timeout=120):
    x, y = Fraction(str(start)) * fps, Fraction(str(end)) * fps
    if x.denominator != 1 or y.denominator != 1:
        raise ValueError("scene boundaries must align to video frames")
    command = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-xerror",
        "-i",
        str(video),
        "-map",
        "0:v:0",
        "-vf",
        f"trim=start_frame={int(x)}:end_frame={int(y)},setpts=PTS-STARTPTS,format=rgb24",
        "-an",
        "-fps_mode",
        "passthrough",
        "-f",
        "framemd5",
        "-",
    ]
    output = subprocess.run(
        command, capture_output=True, text=True, timeout=timeout, check=True
    ).stdout
    time_base = None
    hashes = []
    for line in output.splitlines():
        if line.startswith("#tb 0:"):
            time_base = Fraction(line.split(":", 1)[1].strip())
        if line and not line.startswith("#"):
            if time_base is None:
                raise ValueError("missing decoded frame time base")
            fields = [v.strip() for v in line.split(",")]
            hashes.append(
                (
                    str(int(fields[2]) * time_base),
                    str(int(fields[3]) * time_base),
                    fields[4],
                    fields[5],
                )
            )

    if len(hashes) != int(y - x):
        raise ValueError("decoded frame count disagrees with scene interval")
    return hashes


def verify(snapshot, candidate_receipt, targets):
    b, path = check_scope(snapshot, targets)
    old = recorded(b["receipt"])
    new = recorded(candidate_receipt)
    if (
        digest(b["source"]) != new["source_sha256"]
        or digest(b["timeline"]) != new["timeline_sha256"]
    ):
        raise ValueError("candidate is not current source/timeline")
    if rendered_scene(new) != read(b["manifest"])["scene"]:
        raise ValueError("candidate Scene class disagrees with manifest")
    now = load_timeline(Path(b["timeline"]).read_bytes())["segments"]
    if [s["label"] for s in now] != [s["label"] for s in b["segments"]]:
        raise ValueError("scene ID order changed")

    def fps(r):
        stream = next(s for s in r["probe"]["streams"] if s["codec_type"] == "video")
        average = Fraction(stream["avg_frame_rate"])
        nominal = Fraction(stream.get("r_frame_rate", stream["avg_frame_rate"]))
        if nominal <= 0 or abs(average - nominal) / nominal > Fraction(1, 1000):
            raise ValueError("unsupported frame grid: uncertain nominal/average rate")
        return nominal

    if any(s["codec_type"] == "audio" for r in (old, new) for s in r["probe"]["streams"]):
        raise ValueError("revision media comparison supports silent video only")
    if fps(old) != fps(new):
        raise ValueError("video frame rate changed")

    def geometry(r):
        stream = next(s for s in r["probe"]["streams"] if s["codec_type"] == "video")
        dims = (stream["width"], stream["height"])
        if any(type(v) is not int or v <= 0 for v in dims):
            raise ValueError("invalid video geometry")
        return (*dims, stream.get("sample_aspect_ratio", "1:1"))

    if geometry(old) != geometry(new):
        raise ValueError("video geometry changed")
    evidence = []
    for a, z in zip(b["segments"], now):
        if a["label"] in targets:
            continue
        if Fraction(str(a["end"])) - Fraction(str(a["start"])) != Fraction(
            str(z["end"])
        ) - Fraction(str(z["start"])):
            raise ValueError("non-target duration changed")
        before = frame_sequence(old["video"], a["start"], a["end"], fps(old))
        after = frame_sequence(new["video"], z["start"], z["end"], fps(new))
        if before != after:
            raise ValueError("non-target media changed: " + a["label"])
        evidence.append(
            {
                "id": a["label"],
                "before_interval": [a["start"], a["end"]],
                "after_interval": [z["start"], z["end"]],
                "frames": len(before),
                "decoded_sequence_sha256": hashlib.sha256(
                    json.dumps(before, separators=(",", ":")).encode()
                ).hexdigest(),
            }
        )
    return {
        "scope": targets,
        "baseline": str(path),
        "baseline_video_sha256": old["video_sha256"],
        "candidate_video_sha256": new["video_sha256"],
        "non_target_media_equal": True,
        "non_target_source_equal": True,
        "dependencies_equal": True,
        "scenes": evidence,
        "notice": (
            "All decoded non-target RGB frames equal at mapped scene intervals. "
            "No semantic quality or user-understanding judgement."
        ),
    }


def restore(snapshot, current_receipt):
    b, path = baseline(snapshot)
    check_static(b)
    r = recorded(current_receipt)
    for key, sha in [("source", "source_sha256"), ("timeline", "timeline_sha256")]:
        if digest(b[key]) != r[sha]:
            raise ValueError("current files differ from guarded candidate")
    for key in ("source", "timeline"):
        shutil.copyfile(path.parent / b["files"][b[key]]["copy"], b[key])
    return {
        "restored_from": str(path),
        "source_sha256": digest(b["source"]),
        "timeline_sha256": digest(b["timeline"]),
        "retained_candidate_receipt": str(current_receipt),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)
    c = sub.add_parser("capture")
    c.add_argument("--manifest", required=True)
    c.add_argument("--run", required=True)
    c.add_argument("--store", required=True)
    locator = sub.add_parser("locate")
    locator.add_argument("--baseline", required=True)
    locator.add_argument("--target", required=True)
    locator.add_argument("--feedback", required=True)
    locator.add_argument("--acknowledge", action="append", default=[])
    a = sub.add_parser("apply")
    a.add_argument("--plan", required=True)
    a.add_argument("--edits", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--baseline", required=True)
    v.add_argument("--run", required=True)
    v.add_argument("--target", required=True)
    r = sub.add_parser("restore")
    r.add_argument("--baseline", required=True)
    r.add_argument("--run", required=True)
    args = p.parse_args()
    try:
        if args.action == "capture":
            result = {"snapshot": capture(args.manifest, args.run, args.store)}
        elif args.action == "locate":
            result = locate(args.baseline, args.target, args.feedback, args.acknowledge)
        elif args.action == "apply":
            result = apply(read(args.plan), read(args.edits))
        elif args.action == "verify":
            result = verify(args.baseline, args.run, [args.target])
        else:
            result = restore(args.baseline, args.run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as e:
        p.exit(1, str(e) + "\n")


if __name__ == "__main__":
    main()
