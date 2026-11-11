"""Caption-free native-frame evidence from trusted retained local renders.

Not source execution, hostile-media isolation, playback or editorial approval.
"""

from fractions import Fraction
import hashlib
from importlib.metadata import PackageNotFoundError, version
import math
import os
from pathlib import Path
import shutil
import time
import uuid

from app.render.diagnostics import DiagnosticTimeline, utc_timestamp
from . import load_timeline, number
from .evidence_io import Budget, Storage, capture, identity, strict_json
from .evidence_sheets import build_sheets, pillow, png_limit, verify_native

MIB = 1024 * 1024
LIMITS = {
    "receipt_bytes": 2 * MIB,
    "source_bytes": 2 * MIB,
    "timeline_bytes": 65536,
    "trace_bytes": 16 * MIB,
    "video_bytes": 512 * MIB,
    "width": 3840,
    "height": 2160,
    "nominal_fps": 60,
    "duration_seconds": 120,
    "decoded_frames": 7200,
    "selected_frames": 120,
    "selected_pixels": 100_000_000,
    "pixel_work": 100_000_000_000,
}
# Reserve codec/filter lookahead in each source pass. This is accounting headroom,
# not a claim about decoder RSS or hostile bitstreams; tools are single-threaded.
LOOKAHEAD = 64
SHEET_PIXELS = 1020 * 1068


def selection(indices, count, all_frames=False):
    if type(all_frames) is not bool or (all_frames and indices is not None):
        raise ValueError("choose explicit indices or all_frames")
    if indices is None:
        k = count if all_frames else min(12, count)
        if k > LIMITS["selected_frames"]:
            raise ValueError("all frames exceed selection limit; choose sparse indices")
        indices = [0] if k == 1 else [j * (count - 1) // (k - 1) for j in range(k)]
    if (
        type(indices) is not list
        or not 1 <= len(indices) <= LIMITS["selected_frames"]
        or any(type(i) is not int or not 0 <= i < count for i in indices)
        or len(set(indices)) != len(indices)
    ):
        raise ValueError("select 1..120 distinct integer frame indices inside the video")
    return sorted(indices)


def coverage_for(selected, count):
    omitted, cursor = [], 0
    for index in selected:
        if cursor < index:
            omitted.append([cursor, index])
        cursor = index + 1
    if cursor < count:
        omitted.append([cursor, count])
    return {
        "domain": "decoded_video_frame_indices",
        "total_frame_count": count,
        "exported_count": len(selected),
        "selected_indices": selected,
        "omitted_intervals": omitted,
        "interval_convention": "half_open",
        "export_coverage_complete": not omitted,
        "review_performed_by_exporter": False,
    }


def origin(from_run, budget):
    path = Path(from_run)
    if path.name != "result.json":
        raise ValueError("use canonical run/result.json")
    receipt, raw = identity(path, LIMITS["receipt_bytes"], budget, collect=True)
    path = Path(receipt["path"])
    old = strict_json(raw)
    if (
        type(old) is not dict
        or old.get("status") != "success"
        or old.get("operation", "render") != "render"
    ):
        raise ValueError("requires an original successful render receipt")
    marker = old.get("completed_artifact_version")
    if "completed_artifact_version" in old and (type(marker) is not int or marker != 1):
        raise ValueError("unsupported completed_artifact_version")
    root = path.parent
    value = old.get("run_dir")
    if type(value) is not str or not Path(value).is_absolute() or Path(value).resolve() != root:
        raise ValueError("run_dir must match canonical receipt parent")
    if type(old.get("source")) is not str:
        raise ValueError("missing historical source record")
    entries = {"receipt": receipt}
    specs = [
        ("source", str(root / "source.py"), old.get("source_sha256")),
        ("timeline", str(root / "timeline.json"), old.get("timeline_sha256")),
        ("video", old.get("video"), old.get("video_sha256")),
    ]
    if "frame_trace" in old:
        specs.append(("trace", old["frame_trace"]["path"], old["frame_trace"]["sha256"]))
    # Metadata gate for ALL inputs before any potentially large artifact hashing.
    for name, value, sha in specs:
        budget.check()
        if type(value) is not str or not Path(value).is_absolute():
            raise ValueError("recorded artifact path must be absolute")
        p = Path(value)
        if p.is_symlink() or not p.resolve(strict=True).is_relative_to(root) or not p.is_file():
            raise ValueError("recorded artifact must be a regular file inside run root")
        if not 0 < p.stat().st_size <= LIMITS[name + "_bytes"]:
            raise ValueError("input exceeds byte limit or is empty")
        if type(sha) is not str:
            raise ValueError("missing recorded SHA-256")
    for name, value, sha in specs:
        entries[name] = identity(value, LIMITS[name + "_bytes"], budget, expected=sha)
    _, timeline = identity(root / "timeline.json", LIMITS["timeline_bytes"], budget, collect=True)
    load_timeline(timeline)
    video = Path(entries["video"]["path"])
    # A brand check plus forced MOV demuxing: extension alone is insufficient.
    with video.open("rb") as handle:
        header = handle.read(16)
    if (
        len(header) < 16
        or header[4:8] != b"ftyp"
        or header[8:12] not in (b"isom", b"iso2", b"iso6", b"mp41", b"mp42", b"avc1")
    ):
        raise ValueError("requires a supported local MP4 container")
    budget.check()
    return entries


def input_args(video):
    # Both flags are needed: a file protocol whitelist alone permits external
    # local data references. Do not enable MOV data references or absolute paths.
    return [
        "-protocol_whitelist",
        "file,pipe",
        "-f",
        "mov",
        "-enable_drefs",
        "0",
        "-use_absolute_path",
        "0",
        "-i",
        video,
    ]


def probe_video(video, budget, timeout, diagnostics):
    raw = capture(
        [
            "ffprobe",
            "-v",
            "error",
            *input_args(video),
            "-select_streams",
            "v:0",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
        ],
        budget,
        timeout,
        diagnostics=diagnostics,
    )
    probe = strict_json(raw)
    if len(probe["streams"]) != 1:
        raise ValueError("requires one selected video stream")
    s = probe["streams"][0]
    w, h = s["width"], s["height"]
    if (
        type(w) is not int
        or type(h) is not int
        or not 0 < w <= LIMITS["width"]
        or not 0 < h <= LIMITS["height"]
    ):
        raise ValueError("unsupported video geometry")
    if s["codec_name"] != "h264" or s["pix_fmt"] != "yuv420p":
        raise ValueError("only normal H.264 8-bit yuv420p MSR video is supported")
    if s.get("sample_aspect_ratio") not in (None, "N/A", "0:1", "1:1"):
        raise ValueError("requires square pixels")
    if s.get("color_transfer") not in (None, "unknown", "bt709", "smpte170m", "bt470bg"):
        raise ValueError("unsupported color transfer/HDR")
    if s.get("color_primaries") not in (None, "unknown", "bt709", "smpte170m", "bt470bg"):
        raise ValueError("unsupported color primaries/HDR")
    if s.get("color_space") not in (None, "unknown", "bt709", "smpte170m", "bt470bg"):
        raise ValueError("unsupported color space")
    if "rotate" in s.get("tags", {}) or s.get("side_data_list"):
        raise ValueError("rotation or unsupported video side data")
    try:
        rate = Fraction(s["avg_frame_rate"])
        duration = number(float(s["duration"]))
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError("missing usable nominal rate/duration") from exc
    if not 0 < rate <= LIMITS["nominal_fps"] or not 0 < duration <= LIMITS["duration_seconds"]:
        raise ValueError("rate/duration exceeds export limits")
    declared = s.get("nb_frames")
    if declared not in (None, "N/A"):
        if type(declared) is not str or not declared.isdigit() or int(declared) <= 0:
            raise ValueError("invalid declared frame count")
        declared = int(declared)
        if declared > LIMITS["decoded_frames"]:
            raise ValueError("declared frame count exceeds limit")
    else:
        declared = None
    return s, rate, declared


def media_command(video, count):
    return [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-xerror",
        "-threads",
        "1",
        "-filter_threads",
        "1",
        "-noautorotate",
        *input_args(video),
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        "-dn",
        "-threads",
        "1",
        "-pix_fmt",
        "rgb24",
        "-fps_mode",
        "passthrough",
        "-frames:v",
        str(count),
    ]


def count_frames(video, stream, budget, timeout, diagnostics):
    pixels = stream["width"] * stream["height"]
    # Reserve two source passes, lookahead, and five selected-native operations
    # (encode, verification, sheet decode/resize and final verification).
    cap = min(
        LIMITS["decoded_frames"],
        ((LIMITS["pixel_work"] - 20 * SHEET_PIXELS) // pixels - 2 * LOOKAHEAD - 5 * 120) // 2 - 1,
    )
    if cap < 1:
        raise ValueError("pixel work budget cannot accommodate input")
    command = media_command(video, cap + 1)
    position = command.index("-pix_fmt")
    del command[position : position + 2]  # Decode native surfaces, not every frame's RGB.
    raw = capture(
        command + ["-progress", "pipe:1", "-stats_period", "1", "-f", "null", "-"],
        budget,
        timeout,
        limit=65536,
        diagnostics=diagnostics,
    )
    count, ended = 0, False
    for line in raw.decode("ascii").splitlines():
        if len(line) > 256 or "=" not in line:
            raise ValueError("invalid bounded decode progress")
        key, value = line.split("=", 1)
        if key == "frame":
            if not value.isdigit() or int(value) < count:
                raise ValueError("invalid decoded frame count")
            count = int(value)
        elif key == "progress":
            ended = value == "end"
    if not ended or count < 1:
        raise ValueError("incomplete decoded frame count")
    if count > cap:
        raise ValueError("decoded count/pixel work exceeds limit (sentinel reached)")
    return count


def selection_filter(selected):
    # A linear sum of 120 eq() terms exceeds some ffmpeg expression-parser
    # recursion limits. Balanced sums retain the exact ordinal set.
    def balanced(values):
        if len(values) == 1:
            return f"eq(n\\,{values[0]})"
        mid = len(values) // 2
        return f"({balanced(values[:mid])})+({balanced(values[mid:])})"

    return "select=" + balanced(selected)


def extract(video, selected, stream, rate, store, timeout, diagnostics):
    Image = pillow()[0]
    width, height = stream["width"], stream["height"]
    frame_bytes = width * height * 3
    buffer, ordinal, frames = bytearray(), 0, []

    def consume(chunk):
        nonlocal ordinal
        buffer.extend(chunk)
        while len(buffer) >= frame_bytes:
            store.budget.check()
            if ordinal < len(selected):
                source_index = selected[ordinal]
                raw = bytes(buffer[:frame_bytes])
                rgb_sha = hashlib.sha256(raw).hexdigest()
                image = Image.frombytes("RGB", (width, height), raw)
                relative = f"native/frame-{len(frames) + 1:03}.png"
                entry = store.write(
                    relative,
                    lambda f: image.save(f, format="PNG", compress_level=1),
                    png_limit(width, height),
                )
                image.close()
                entry["relative_path"] = relative
                grid = Fraction(source_index, 1) / rate
                frames.append(
                    {
                        "frame_index": source_index,
                        "grid_time_seconds": str(grid),
                        "grid_time_seconds_approximate": float(grid),
                        "native": entry,
                        "dimensions": [width, height],
                        "decoded_rgb24_sha256": rgb_sha,
                    }
                )
            del buffer[:frame_bytes]
            ordinal += 1

    capture(
        media_command(video, len(selected))
        + ["-vf", selection_filter(selected), "-f", "rawvideo", "-"],
        store.budget,
        timeout,
        limit=len(selected) * frame_bytes,
        sink=consume,
        diagnostics=diagnostics,
    )
    if buffer or ordinal != len(selected) or len(frames) != len(selected):
        raise ValueError("missing selected frames or incomplete RGB frame")
    for frame in frames:
        verify_native(store.root / frame["native"]["relative_path"], frame, store.budget)
    return frames


def validate_inventory(root, entries, frames, budget):
    expected = {e["relative_path"] for e in entries}
    observed = set()
    for path in root.rglob("*"):
        budget.check()
        if path.is_symlink():
            raise ValueError("symlink in output inventory")
        relative = str(path.relative_to(root))
        if path.is_dir():
            if relative not in ("native", "sheets", "private"):
                raise ValueError("unexpected output directory")
        elif relative != "private/diagnostics.jsonl":
            observed.add(relative)
    if observed != expected:
        raise ValueError("missing or extra output artifacts")
    for entry in entries:
        now = identity(
            root / entry["relative_path"], entry["size_bytes"], budget, expected=entry["sha256"]
        )
        if now["size_bytes"] != entry["size_bytes"]:
            raise ValueError("output artifact changed")
        if entry["relative_path"].startswith("sheets/"):
            Image = pillow()[0]
            with Image.open(root / entry["relative_path"]) as sheet:
                if (
                    sheet.format != "PNG"
                    or sheet.mode != "RGB"
                    or list(sheet.size) != entry["dimensions"]
                ):
                    raise ValueError("invalid sheet PNG geometry or mode")
                sheet.load()
            budget.check()
    for frame in frames:
        verify_native(root / frame["native"]["relative_path"], frame, budget)


def export_run(
    from_run,
    output_dir,
    *,
    frame_indices=None,
    all_frames=False,
    tool_timeout=30,
    operation_timeout=180,
    cancel_event=None,
):
    if not 0 < number(tool_timeout) <= 120 or not 0 < number(operation_timeout) <= 300:
        raise ValueError("tool_timeout must be (0,120], operation_timeout (0,300]")
    budget, started_utc = Budget(operation_timeout, cancel_event), utc_timestamp()
    pillow_version = pillow()[3]  # Fail before input hashing or media work.
    inputs = origin(from_run, budget)
    output = Path(output_dir).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("output must be a new directory")
    if any(p.is_symlink() for p in output.parents):
        raise ValueError("output ancestors must not be symlinks; use their canonical path")
    output = output.resolve()
    if output.is_relative_to(Path(inputs["receipt"]["path"]).parent):
        raise ValueError("output must be outside the retained run")
    output.mkdir(mode=0o700)
    for directory in ("native", "sheets", "private"):
        (output / directory).mkdir(mode=0o700)
    with DiagnosticTimeline(
        output / "private/diagnostics.jsonl", context={"operation": "run_frame_evidence"}
    ) as diagnostics:
        with diagnostics.phase("probe"):
            versions = {
                name: capture(
                    [name, "-version"], budget, tool_timeout, limit=65536, diagnostics=diagnostics
                )
                .decode("utf-8", "replace")
                .splitlines()[0]
                for name in ("ffmpeg", "ffprobe")
            }
            versions["Pillow"] = pillow_version
            video = inputs["video"]["path"]
            stream, rate, declared = probe_video(video, budget, tool_timeout, diagnostics)
        with diagnostics.phase("count"):
            count = count_frames(video, stream, budget, tool_timeout, diagnostics)
            if declared is not None and count != declared:
                raise ValueError("declared and decoded frame counts disagree")
            selected = selection(frame_indices, count, all_frames)
            pixels = stream["width"] * stream["height"]
            if pixels * len(selected) > LIMITS["selected_pixels"]:
                raise ValueError("selected native pixel budget exceeded; choose sparse indices")
            pages = math.ceil(len(selected) / 12)
            work = (
                pixels * (count + selected[-1] + 1 + 2 * LOOKAHEAD + 5 * len(selected))
                + 2 * pages * SHEET_PIXELS
            )
            if work > LIMITS["pixel_work"]:
                raise ValueError("pixel work budget exceeded")
            reserved = (
                len(selected) * png_limit(stream["width"], stream["height"])
                + pages * png_limit(1020, 1068)
                + 8 * MIB
            )
            if shutil.disk_usage(output).free < reserved:
                raise ValueError("insufficient free storage for conservative PNG reservation")
            # 1 MiB is separately reserved for fixed-size diagnostic events.
            store = Storage(output, reserved - MIB, budget)
        with diagnostics.phase("native"):
            frames = extract(video, selected, stream, rate, store, tool_timeout, diagnostics)
        with diagnostics.phase("sheets"):
            coverage = coverage_for(selected, count)
            export_id = uuid.uuid4().hex
            sheets = build_sheets(
                output, frames, coverage, inputs["video"]["sha256"], export_id, store
            )
            index = {
                "schema_version": 1,
                "operation": "run_frame_evidence",
                "export_id": export_id,
                "coverage": coverage,
                "frames": frames,
                "sheets": sheets,
                "nominal_rate": str(rate),
                "nominal_rate_source": "avg_frame_rate",
                "grid_notice": "Ordinal / nominal rate; not PTS or CFR certification.",
                "section_mapping": "not provided",
            }
            index_entry = store.json("frames.json", index)
            index_entry["relative_path"] = "frames.json"
        with diagnostics.phase("revalidation"):
            entries = [index_entry, *sheets, *(f["native"] for f in frames)]
            validate_inventory(output, entries, frames, budget)
            if origin(from_run, budget) != inputs:
                raise ValueError("inputs changed during export")
        try:
            product_version = version("manim-simple-render")
        except PackageNotFoundError:
            product_version = None
        # The manifest must snapshot the optional sink after its last write and
        # close. There can be no later publication/close error hidden by a false
        # diagnostics_incomplete flag; publication itself is outside this log.
        diagnostics.event("publication_ready")
        diagnostics.close()
        result = {
            "schema_version": 1,
            "operation": "run_frame_evidence",
            "status": "success",
            "export_id": export_id,
            "manifest_path": str(output / "manifest.json"),
            "inputs": inputs,
            "artifacts": entries,
            "coverage": coverage,
            "probe": stream,
            "decoded_frame_count": count,
            "count_method": "bounded decoded passthrough null output with max+1 sentinel",
            "nominal_rate": str(rate),
            "nominal_rate_source": "avg_frame_rate",
            "cfr_verified": False,
            "colorimetry_verified": False,
            "pixel_aspect_policy": (
                "Unspecified SAR interpreted as square; " "declared non-square rejected."
            ),
            "section_mapping": "not provided",
            "selection": {
                "mode": (
                    "all" if all_frames else "explicit" if frame_indices is not None else "overview"
                ),
                "requested_indices": frame_indices,
                "selected_indices": selected,
            },
            "versions": dict(versions, product=product_version),
            "limits": LIMITS,
            "pixel_work_reserved": work,
            "storage_reserved_bytes": reserved,
            "observation_started": started_utc,
            "observation_finished": utc_timestamp(),
            "elapsed_seconds": time.monotonic() - budget.started,
            "transform": "RGB native PNG; compression 1. Sheet tiles <=320x180, "
            "LANCZOS, aspect preserved, no crop/upscale; embedded default font.",
            "private_diagnostics": "private/diagnostics.jsonl",
            "diagnostics_incomplete": diagnostics.failed,
            "diagnostics_cutoff": "before_manifest_publication",
            "render_completion_verified_by_exporter": False,
            "visual_acceptance": False,
            "semantic_acceptance": False,
            "continuous_playback_performed": False,
            "audio_reviewed": False,
            "human_review_recorded": False,
            "readiness": "not_determined",
            "notice": "Trusted local media only. Hash integrity is not authentication. "
            "Decode/count guards include a sentinel and lookahead allowance, not "
            "decoder RSS, hard realtime or descendant containment. "
            "Exported frames are not inspected or approved frames.",
        }
        store.json(".manifest-complete.tmp", result)
        budget.check()
        os.link(output / ".manifest-complete.tmp", output / "manifest.json")
        try:
            (output / ".manifest-complete.tmp").unlink()
        except OSError:
            # A complete manifest already exists: do not misreport absence.
            result = dict(result, publication_warning="complete temporary file retained")
        return result
