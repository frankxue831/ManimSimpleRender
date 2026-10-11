"""Measure a rendered video and decode two inspection frames per step."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile

TOOL_TIMEOUT = 30


def _run(command):
    try:
        result = subprocess.run(
            command,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TOOL_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("%s could not complete: %s" % (command[0], exc)) from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace")[-1500:]
        raise RuntimeError("%s failed: %s" % (command[0], " ".join(detail.split())))
    return result.stdout


def _time(value):
    if type(value) not in (int, float):
        raise ValueError("step times must be numbers, excluding booleans")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError("invalid step time") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError("step times must be finite and nonnegative")
    return result


def inspect_video(result_path):
    receipt_path = Path(result_path).resolve()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if type(receipt) is not dict or receipt.get("status") != "success":
        raise ValueError("a success receipt is required")
    video_value = receipt.get("video")
    if type(video_value) is not str or not Path(video_value).is_absolute():
        raise ValueError("receipt video must be an absolute path")
    video = Path(video_value).resolve()
    if not video.is_file() or video.stat().st_size <= 0:
        raise ValueError("video is missing or empty")

    probe = _run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video)]
    )
    try:
        measured = json.loads(probe)["format"]["duration"]
        if type(measured) not in (str, int, float):
            raise ValueError("unexpected duration type")
        duration = float(measured)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("duration is not finite and positive")
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise ValueError("ffprobe returned a missing or invalid duration") from exc

    steps = receipt.get("steps")
    if type(steps) is not list or not 1 <= len(steps) <= 12:
        raise ValueError("receipt must contain 1 to 12 steps")
    samples = []
    previous_end = 0.0
    for index, step in enumerate(steps):
        if type(step) is not dict:
            raise ValueError("invalid step object")
        if type(step.get("index")) is not int or step["index"] != index:
            raise ValueError("step indices must be consecutive and zero based")
        start = _time(step.get("start"))
        end = _time(step.get("end"))
        if end <= start or not math.isclose(start, previous_end, rel_tol=0, abs_tol=1e-9):
            raise ValueError("steps must be positive, ordered and contiguous from zero")
        if end > duration + 0.2:
            raise ValueError("step %d extends beyond measured duration" % index)
        # Generated reveals finish 0.6 seconds after each step begins.
        reveal_end = start + 0.6
        upper = min(end, duration)
        if upper <= reveal_end:
            raise ValueError("step %d has no measurable post-reveal interval" % index)
        available = upper - reveal_end
        first = reveal_end + min(0.1, available / 3)
        last = upper - min(0.1, available / 3)
        for sample in (first, last):
            sample = min(max(sample, math.nextafter(0.0, 1.0)), math.nextafter(duration, 0.0))
            if not (
                math.isfinite(sample) and start < sample < end and reveal_end < sample < duration
            ):
                raise ValueError("invalid clamped frame time")
            samples.append((index, sample))
        previous_end = end

    digest = hashlib.sha256()
    with video.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    frames_dir = Path(tempfile.mkdtemp(prefix="frames-", dir=str(receipt_path.parent)))
    frames = []
    for position, (index, sample) in enumerate(samples):
        frame = frames_dir / ("step-%02d-%d.png" % (index, position % 2))
        _run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-xerror",
                "-ss",
                repr(sample),
                "-i",
                str(video),
                "-map",
                "0:v:0",
                "-frames:v",
                "1",
                "-an",
                "-sn",
                "-c:v",
                "png",
                "-f",
                "image2",
                str(frame),
            ]
        )
        if not frame.is_file() or frame.stat().st_size <= 8:
            raise RuntimeError("decoder produced a missing or empty frame")
        with frame.open("rb") as source:
            if source.read(8) != b"\x89PNG\r\n\x1a\n":
                raise RuntimeError("decoder output is not a PNG")
        frames.append({"path": str(frame.resolve()), "time": sample, "step": index})
    evidence = {
        "video": str(video),
        "video_sha256": digest.hexdigest(),
        "duration_seconds": duration,
        "frames": frames,
    }
    evidence_path = frames_dir / "evidence.json"
    evidence_path.write_text(
        json.dumps(evidence, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )
    return evidence_path.resolve()


def main():
    parser = argparse.ArgumentParser(description="Measure video and decode inspection PNGs")
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    try:
        evidence_path = inspect_video(args.result)
    except Exception as exc:
        print("video inspection: " + " ".join(str(exc).split()), file=sys.stderr)
        return 1
    print(str(evidence_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
