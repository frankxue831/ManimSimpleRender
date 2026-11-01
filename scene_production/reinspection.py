"""Explicit recorded-video reinspection; never execute current source or Manim."""

import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile

from . import (
    _failure,
    _inspect_video,
    _verify_file,
    _write_receipt,
    load_timeline,
    number,
    pairs,
)

NOTICE = "不应用源代码修改；画面变化须重新渲染。依赖、导入和环境未完整快照。"


def _read(path, limit):
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("input exceeds byte limit")
    return raw


def _finite(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite JSON number")
    return result


def _origin(from_run):
    path = Path(from_run).resolve(strict=True)
    if path.name != "result.json" or not path.is_file():
        raise ValueError("use canonical run/result.json")
    raw = _read(path, 2 * 1024 * 1024)
    old = json.loads(raw, object_pairs_hook=pairs, parse_float=_finite, parse_constant=_finite)
    if type(old) is not dict or old.get("status") not in ("success", "failed"):
        raise ValueError("expected a render receipt")
    if old.get("operation", "render") != "render":
        raise ValueError("use original rendered run; reinspection chains are not supported")
    marker = old.get("completed_artifact_version")
    if "completed_artifact_version" in old and (type(marker) is not int or marker != 1):
        raise ValueError("unsupported completed_artifact_version")
    if old["status"] != "success" and marker != 1:
        raise ValueError("failed run has no recorded completed video identity; render again")
    root = path.parent
    value = old.get("run_dir")
    if type(value) is not str or not Path(value).is_absolute() or Path(value).resolve() != root:
        raise ValueError("run_dir must match canonical receipt parent")
    if type(old.get("source")) is not str:
        raise ValueError("missing historical source record")
    source = _verify_file(str(root / "source.py"), root, old.get("source_sha256"))
    timeline = _verify_file(str(root / "timeline.json"), root, old.get("timeline_sha256"))
    load_timeline(_read(timeline, 65536))
    video = _verify_file(old.get("video"), root, old.get("video_sha256"))
    if video.stat().st_size <= 0:
        raise ValueError("completed video is empty")
    if "frame_trace" in old:
        entry = old["frame_trace"]
        _verify_file(entry["path"], root, entry["sha256"])
    return path, hashlib.sha256(raw).hexdigest(), old, source, video


def reinspect(from_run, timeline, output_dir, tool_timeout=30):
    """Fresh complete checks of one recorded prior video and a corrected timeline."""
    if not 0 < number(tool_timeout) <= 3600:
        raise ValueError("tool_timeout must be in (0, 3600]")
    raw = _read(Path(timeline), 65536)
    data = load_timeline(raw)
    origin, origin_sha, old, source, old_video = _origin(from_run)
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="scene-", dir=root))
    receipt = {
        "status": "failed",
        "run_dir": str(run),
        "operation": "reinspect",
        "notice": NOTICE,
        "origin_receipt": str(origin),
        "origin_receipt_sha256": origin_sha,
        "source": old["source"],
        "source_sha256": old["source_sha256"],
        "timeline_sha256": hashlib.sha256(raw).hexdigest(),
        "video": str(run / "video.mp4"),
        "video_sha256": old["video_sha256"],
    }
    stage = "copy"
    try:
        _write_receipt(run, receipt)
        shutil.copyfile(source, run / "source.py")
        shutil.copyfile(old_video, receipt["video"])
        (run / "timeline.json").write_bytes(raw)
        if "frame_trace" in old:
            entry = old["frame_trace"]
            trace_copy = run / "frame-trace.json"
            shutil.copyfile(entry["path"], trace_copy)
            _verify_file(entry["path"], origin.parent, entry["sha256"])
            _verify_file(str(trace_copy), run, entry["sha256"])
            receipt["frame_trace"] = {"path": str(trace_copy), "sha256": entry["sha256"]}
        for value, expected in (
            (str(origin), origin_sha),
            (str(origin.parent / "source.py"), old["source_sha256"]),
            (str(origin.parent / "timeline.json"), old["timeline_sha256"]),
            (old["video"], old["video_sha256"]),
        ):
            _verify_file(value, origin.parent, expected)
        for name, expected in (
            ("source.py", receipt["source_sha256"]),
            ("timeline.json", receipt["timeline_sha256"]),
            ("video.mp4", receipt["video_sha256"]),
        ):
            _verify_file(str(run / name), run, expected)
        receipt.update(_inspect_video(Path(receipt["video"]), data, run, tool_timeout, receipt))
        receipt.update(
            status="success",
            provenance="Historical retained source/video copied and hash-checked. "
            "Source changes not applied; helpers/imports/environment not snapshotted. "
            "Integrity is not authentication or a sandbox.",
        )
        receipt.pop("stage", None)
        stage = "publication"
        _write_receipt(run, receipt)
    except BaseException as exc:
        _failure(run, receipt, stage, exc)
        raise
    return receipt
