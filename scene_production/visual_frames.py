"""Bounded caption-linked frame samples from an already verified production run.

This is technical sampling, not visual approval, subtitle overlay or causal
source mapping. Local files and tools are trusted; concurrent edits are not safe.
"""

import argparse
from fractions import Fraction
import json
from pathlib import Path
import subprocess

from . import digest, number, tool
from .captions import verify_sidecar

MAX_FRAMES = 12


def _selection(indices, record):
    if indices is None:
        indices = [(cue["start_frame"] + cue["end_frame"]) // 2 for cue in record["cues"]]
    if (
        type(indices) is not list
        or not 1 <= len(indices) <= MAX_FRAMES
        or any(
            type(index) is not int or not 0 <= index < record["duration_frames"]
            for index in indices
        )
        or len(set(indices)) != len(indices)
    ):
        raise ValueError("select 1..12 distinct integer frame indices inside the video")
    return sorted(indices)


def _hashes(raw):
    hashes = []
    dimensions = aspect = None
    for line in raw.decode("ascii").splitlines():
        if line.startswith("#dimensions 0:"):
            dimensions = line.split(":", 1)[1].strip()
        elif line.startswith("#sar 0:"):
            aspect = line.split(":", 1)[1].strip()
        elif line and not line.startswith("#"):
            fields = [part.strip() for part in line.split(",")]
            if len(fields) != 6 or len(fields[-1]) != 64:
                raise ValueError("invalid decoded frame hash response")
            hashes.append(fields[-1])
    if not hashes or dimensions is None or aspect is None:
        raise ValueError("incomplete decoded frame hash response")
    return hashes, dimensions, aspect


def export(captions_manifest, output_dir, frame_indices=None, tool_timeout=30):
    """Export at most twelve exact decoded frame indices into a new directory."""
    if not 0 < number(tool_timeout) <= 3600:
        raise ValueError("tool_timeout must be in (0, 3600]")
    record = verify_sidecar(captions_manifest)
    manifest = Path(captions_manifest).resolve(strict=True)
    manifest_hash = digest(manifest)
    selected = _selection(frame_indices, record)
    fps = record["fps"]
    if fps not in (15, 30) or Fraction(record["duration_frames"], fps) > 120:
        raise ValueError("frame sampling currently supports at most 120 seconds at 15 or 30 fps")
    output = Path(output_dir).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("output must be a new directory")
    output = output.resolve()
    for protected in (manifest.parent, Path(record["origin_receipt"]["path"]).parent):
        if output.is_relative_to(protected):
            raise ValueError("output must be outside the recorded run and caption sidecar")
    video = record["video"]["path"]
    probe = json.loads(
        tool(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_streams",
                "-of",
                "json",
                video,
            ],
            tool_timeout,
        )
    )
    stream = probe["streams"][0]
    if not (0 < stream["width"] <= 1280 and 0 < stream["height"] <= 720):
        raise ValueError("frame sampling is limited to 1280 by 720 pixels")
    selection = "select=" + "+".join(f"eq(n\\,{index})" for index in selected)
    common = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-xerror",
        "-n",
        "-i",
        video,
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        "-dn",
        "-vf",
        selection,
        "-pix_fmt",
        "rgb24",
        "-fps_mode",
        "passthrough",
    ]
    expected = _hashes(tool(common + ["-f", "framemd5", "-hash", "sha256", "-"], tool_timeout))
    if len(expected[0]) != len(selected):
        raise ValueError("source does not contain every selected frame")
    output.mkdir()
    # Never put a user-controlled directory in image2's output pattern. Even
    # -n does not prevent percent expansion in ancestor directory names.
    tool(common + ["-start_number", "1", "frame-%03d.png"], tool_timeout, cwd=output)
    pngs = [output / f"frame-{i:03}.png" for i in range(1, len(selected) + 1)]
    if set(output.iterdir()) != set(pngs) or any(p.is_symlink() or not p.is_file() for p in pngs):
        raise ValueError("unexpected extracted frame files")
    actual = _hashes(
        tool(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-xerror",
                "-framerate",
                "1",
                "-start_number",
                "1",
                "-i",
                "frame-%03d.png",
                "-map",
                "0:v:0",
                "-vf",
                "format=rgb24",
                "-f",
                "framemd5",
                "-hash",
                "sha256",
                "-",
            ],
            tool_timeout,
            cwd=output,
        )
    )
    if actual != expected:
        raise ValueError("PNG pixels or geometry disagree with selected decoded video frames")
    if verify_sidecar(manifest) != record or digest(manifest) != manifest_hash:
        raise ValueError("caption sidecar changed during extraction")
    result = {
        "schema_version": 1,
        "operation": "caption_frame_samples",
        "status": "success",
        "manifest_path": str(output / "manifest.json"),
        "captions": {"path": str(manifest), "sha256": manifest_hash},
        "video": record["video"],
        "source_navigation": {
            "saved_entrypoint": record["source"],
            "events_record": record["events"],
            "saved_snapshot": True,
            "edit_policy": "copy_before_editing",
            "current_authoring_project_known": False,
            "causal_source_mapping": False,
        },
        "frames": [
            {
                "frame_index": index,
                "frame_time_seconds": str(Fraction(index, fps)),
                "png": {"path": str(path), "sha256": digest(path)},
                "decoded_rgb24_sha256": pixel_hash,
                "active_events": [
                    cue for cue in record["cues"] if cue["start_frame"] <= index < cue["end_frame"]
                ],
            }
            for index, path, pixel_hash in zip(selected, pngs, actual[0])
        ],
        "subtitles_overlaid": False,
        "visual_acceptance": False,
        "semantic_acceptance": False,
        "continuous_playback_performed": False,
        "notice": "Frame index/fps is not encoded packet PTS. Event references locate author "
        "annotations, not causal Python lines or visible text. "
        "Sampled PNGs are not whole-video review.",
    }
    with (output / "manifest.json").open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captions", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--frames", nargs="+", type=int)
    parser.add_argument("--tool-timeout", type=float, default=30)
    args = parser.parse_args(argv)
    try:
        result = export(args.captions, args.output_dir, args.frames, args.tool_timeout)
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(2, f"frame sampling failed: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
