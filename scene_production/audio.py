"""Explicit local PCM WAV binding and independent, non-acceptance audio previews.

Inspired by the former voice_bridge/voice_tools checks, without their GUI,
caption pipeline, TTS, subprocess entry point or video re-encoding.
"""

from collections.abc import Mapping
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import struct
import subprocess

from . import digest, pairs
from .captions import verify_sidecar

NOTICE = (
    "Timing and byte identity only; not listened to or semantically checked. "
    "Audio previews are not eligible for the silent-only revision workflow."
)
MAX_WAV_BYTES = 512 * 1024 * 1024
SAMPLE_RATES = {8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000, 64000, 88200, 96000}
BINDING_FIELDS = {
    "schema_version",
    "operation",
    "status",
    "caption_manifest",
    "video",
    "video_info",
    "fps",
    "duration_frames",
    "cues",
    "semantic_acceptance",
    "listened",
    "revision_eligible",
    "notice",
}


def _same(left, right):
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def _timeout(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 300:
        raise ValueError("tool_timeout must be finite and in (0, 300] seconds")
    return value


def _tool(argv, timeout):
    result = subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        timeout=timeout,
        check=True,
    )
    if result.stderr.strip():
        raise ValueError(
            "media tool reported errors: " + result.stderr.decode(errors="replace")[:500]
        )
    return result.stdout


def _read(path):
    with path.open("rb") as handle:
        raw = handle.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("audio manifest exceeds 4 MiB")

    def reject(value):
        raise ValueError("nonfinite JSON number: " + value)

    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=reject)


def _write(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def _entry(path):
    return {"path": str(path), "sha256": digest(path)}


def _checked(entry):
    path = Path(entry["path"])
    if not path.is_absolute() or not path.is_file() or digest(path) != entry["sha256"]:
        raise ValueError("bound input SHA-256 mismatch or missing file")
    return path.resolve(strict=True)


def _new_output(value, protected):
    path = Path(value).expanduser().absolute()
    if path.exists() or path.is_symlink():
        raise ValueError("output directory already exists; choose a new directory")
    path = path.parent.resolve(strict=True) / path.name
    if any(path == root or path.is_relative_to(root) for root in protected):
        raise ValueError("output must be outside the source run and sidecar directories")
    path.mkdir()
    return path


def _pcm(path):
    """Validate RIFF boundaries and integer PCM declarations without decoding."""
    size = path.stat().st_size
    if not 44 <= size <= MAX_WAV_BYTES:
        raise ValueError("WAV file size must be 44 bytes..512 MiB")
    with path.open("rb") as handle:
        header = handle.read(12)
        if header[:4] != b"RIFF" or header[8:] != b"WAVE":
            raise ValueError("only ordinary RIFF WAVE is supported")
        declared = struct.unpack("<I", header[4:8])[0] + 8
        if declared != size:
            raise ValueError("WAV declared size differs from file size")
        position, fmt, data_size = 12, None, None
        while position < size:
            handle.seek(position)
            chunk = handle.read(8)
            if len(chunk) != 8:
                raise ValueError("incomplete WAV chunk header")
            kind, length = chunk[:4], struct.unpack("<I", chunk[4:])[0]
            end = position + 8 + length + length % 2
            if end > size:
                raise ValueError("truncated WAV chunk")
            if kind == b"fmt ":
                if fmt is not None or not 16 <= length <= 40:
                    raise ValueError("duplicate or unsupported WAV format chunk")
                fmt = struct.unpack("<HHIIHH", handle.read(16))
            elif kind == b"data":
                if data_size is not None or fmt is None:
                    raise ValueError("WAV needs one data chunk following its format")
                data_size = length
            position = end
    if fmt is None or not data_size:
        raise ValueError("WAV needs a format and nonempty PCM data")
    encoding, channels, rate, byte_rate, alignment, bits = fmt
    if encoding != 1 or channels not in (1, 2) or bits not in (16, 24, 32):
        raise ValueError("only integer PCM 16/24/32-bit mono/stereo WAV is supported")
    if rate not in SAMPLE_RATES:
        raise ValueError("WAV sample rate must be an AAC-supported rate from 8000..96000 Hz")
    if alignment != channels * bits // 8 or byte_rate != rate * alignment:
        raise ValueError("inconsistent PCM block alignment or byte rate")
    if data_size % alignment:
        raise ValueError("WAV data contains a partial sample frame")
    samples = data_size // alignment
    if samples > rate * 600:
        raise ValueError("WAV exceeds 600 seconds")
    return dict(
        channels=channels,
        sample_rate=rate,
        bits_per_sample=bits,
        sample_frames=samples,
        duration_exact=str(Fraction(samples, rate)),
    )


def _rows(captions, mapping, root=None):
    cues = captions["cues"]
    if not 1 <= len(cues) <= 64:
        raise ValueError("audio binding requires 1..64 caption cues")
    if not isinstance(mapping, Mapping) or set(mapping) != {cue["id"] for cue in cues}:
        raise ValueError("WAV mapping must cover every cue exactly, with no unknown IDs")
    fps, frames = captions["fps"], captions["duration_frames"]
    rows, layout, previous_end = [], None, 0
    for index, cue in enumerate(cues):
        path = Path(mapping[cue["id"]]).expanduser().resolve(strict=True)
        if not path.is_file() or path.suffix.lower() != ".wav":
            raise ValueError("each cue must explicitly name a local .wav file")
        if root is not None and path != root / f"cue-{index + 1:03d}.wav":
            raise ValueError("frozen WAV must remain inside its audio directory")
        before = digest(path)
        pcm = _pcm(path)
        if digest(path) != before:
            raise ValueError("WAV changed during measurement")
        current_layout = (pcm["channels"], pcm["sample_rate"], pcm["bits_per_sample"])
        if layout is not None and current_layout != layout:
            raise ValueError("all WAVs must have the same channels, sample rate and bit depth")
        layout = current_layout
        start, end = cue["start_frame"], cue["end_frame"]
        if not previous_end <= start < end <= frames:
            raise ValueError("caption cues must be ordered, nonoverlapping and inside video")
        previous_end = end
        rate = pcm["sample_rate"]
        offset = Fraction(start * rate, fps)
        if offset.denominator != 1 or Fraction(frames * rate, fps).denominator != 1:
            raise ValueError("cue start and video end must align with the WAV sample grid")
        if Fraction(pcm["sample_frames"], rate) > Fraction(end - start, fps):
            raise ValueError("WAV exceeds its caption cue window: " + cue["id"])
        rows.append(
            dict(
                id=cue["id"],
                text=cue["text"],
                start_frame=start,
                end_frame=end,
                offset_samples=int(offset),
                wav={"path": str(path), "sha256": before},
                pcm=pcm,
            )
        )
    return rows


def _video_info(path, timeout, *, audio=False, expected_pcm=None):
    probe = json.loads(
        _tool(
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-count_frames",
                "-show_streams",
                "-of",
                "json",
                str(path),
            ],
            timeout,
        )
    )
    streams = probe["streams"]
    expected = ["audio", "video"] if audio else ["video"]
    if sorted(stream["codec_type"] for stream in streams) != expected:
        raise ValueError("expected one video and " + ("one audio stream" if audio else "no audio"))
    video = next(stream for stream in streams if stream["codec_type"] == "video")
    base = Fraction(video["time_base"])
    duration = int(video["duration_ts"]) * base
    if duration <= 0 or duration > 600 or Fraction(video.get("start_time", "0")) != 0:
        raise ValueError("video must start at zero and last at most 600 seconds")
    metadata = {key: video[key] for key in ("codec_name", "width", "height", "pix_fmt")}
    metadata.update(
        fps=str(Fraction(video["avg_frame_rate"])),
        frames=int(video["nb_read_frames"]),
        duration_exact=str(duration),
    )
    packets = json.loads(
        _tool(
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-select_streams",
                "v:0",
                "-show_packets",
                "-show_data_hash",
                "sha256",
                "-show_entries",
                "packet=pts,dts,duration,size,data_hash,flags",
                "-of",
                "json",
                str(path),
            ],
            timeout,
        )
    )["packets"]
    if not packets:
        raise ValueError("video has no packets")
    identity = [
        [
            *(str(int(packet[key]) * base) for key in ("pts", "dts", "duration")),
            int(packet["size"]),
            packet["data_hash"],
            packet["flags"],
        ]
        for packet in packets
    ]
    metadata["packet_sha256"] = hashlib.sha256(
        json.dumps(identity, separators=(",", ":")).encode("ascii")
    ).hexdigest()
    if audio:
        sound = next(stream for stream in streams if stream["codec_type"] == "audio")
        if expected_pcm is not None and (
            sound["codec_name"] != "aac"
            or sound["channels"] != expected_pcm["channels"]
            or int(sound["sample_rate"]) != expected_pcm["sample_rate"]
            or Fraction(sound["start_time"]) != 0
        ):
            raise ValueError("preview changed the audio layout, rate or start time")
        audio_duration = int(sound["duration_ts"]) * Fraction(sound["time_base"])
        if abs(audio_duration - duration) > Fraction(1024, int(sound["sample_rate"])):
            raise ValueError("preview audio duration differs by more than one AAC frame")
    return metadata


def _validate_video_info(info, captions):
    """Validate retained metadata, not remeasure or authenticate its video."""
    fields = {
        "codec_name",
        "width",
        "height",
        "pix_fmt",
        "fps",
        "frames",
        "duration_exact",
        "packet_sha256",
    }
    if type(info) is not dict or set(info) != fields:
        raise ValueError("invalid recorded video metadata fields")
    for key in ("codec_name", "pix_fmt", "fps", "duration_exact"):
        if type(info[key]) is not str or not info[key].strip() or info[key] != info[key].strip():
            raise ValueError("invalid recorded video metadata string: " + key)
    for key in ("width", "height", "frames"):
        if type(info[key]) is not int or info[key] <= 0:
            raise ValueError("recorded video metadata requires positive integers: " + key)
    if (
        type(info["packet_sha256"]) is not str
        or re.fullmatch(r"[0-9a-f]{64}", info["packet_sha256"]) is None
    ):
        raise ValueError("invalid recorded video packet SHA-256")
    expected_duration = Fraction(captions["duration_frames"], captions["fps"])
    if (
        info["fps"] != str(Fraction(captions["fps"]))
        or info["frames"] != captions["duration_frames"]
        or info["duration_exact"] != str(expected_duration)
        or not 0 < expected_duration <= 600
    ):
        raise ValueError("video clock differs from caption clock")
    if any(info[key] != captions[key] for key in ("width", "height")):
        raise ValueError("video geometry differs from caption probe")


def bind_audio(captions_manifest, wav_by_cue, output_dir, tool_timeout=30):
    """Freeze a complete cue-to-WAV mapping. Never create media or run TTS."""
    timeout = _timeout(tool_timeout)
    caption_path = Path(captions_manifest).expanduser().resolve(strict=True)
    caption_entry = _entry(caption_path)
    captions = verify_sidecar(caption_path)
    rows = _rows(captions, wav_by_cue)
    video = _checked(captions["video"])
    info = _video_info(video, timeout)
    _validate_video_info(info, captions)
    run = Path(captions["origin_receipt"]["path"]).resolve().parent
    root = _new_output(output_dir, [run, caption_path.parent])
    for index, row in enumerate(rows):
        source = Path(row["wav"]["path"])
        target = root / f"cue-{index + 1:03d}.wav"
        with source.open("rb") as reader, target.open("xb") as writer:
            shutil.copyfileobj(reader, writer)
        if digest(target) != row["wav"]["sha256"] or digest(source) != row["wav"]["sha256"]:
            raise ValueError("WAV changed while freezing inputs")
        row["wav"]["path"] = str(target)
    _checked(caption_entry)
    verify_sidecar(caption_path)
    _checked(captions["video"])
    result = dict(
        schema_version=1,
        operation="audio_binding",
        status="checked_requires_listening",
        caption_manifest=caption_entry,
        video=captions["video"],
        video_info=info,
        fps=captions["fps"],
        duration_frames=captions["duration_frames"],
        cues=rows,
        semantic_acceptance=False,
        listened=False,
        revision_eligible=False,
        notice=NOTICE,
    )
    _write(root / "manifest.json", result)
    return dict(result, manifest_path=str(root / "manifest.json"))


def verify_audio(manifest_path):
    """Recheck the caption provenance and frozen WAV bytes; no media tool calls."""
    path = Path(manifest_path).expanduser().resolve(strict=True)
    result = _read(path)
    if (
        path.name != "manifest.json"
        or type(result) is not dict
        or set(result) != BINDING_FIELDS
        or type(result.get("schema_version")) is not int
        or result["schema_version"] != 1
        or result.get("operation") != "audio_binding"
        or result.get("status") != "checked_requires_listening"
        or result.get("notice") != NOTICE
        or any(
            result.get(key) is not False
            for key in ("semantic_acceptance", "listened", "revision_eligible")
        )
    ):
        raise ValueError("not a checked audio binding")
    captions = verify_sidecar(_checked(result["caption_manifest"]))
    for key in ("video", "fps", "duration_frames"):
        if not _same(result[key], captions[key]):
            raise ValueError("audio binding differs from its caption source")
    _validate_video_info(result["video_info"], captions)
    mapping = {row["id"]: row["wav"]["path"] for row in result["cues"]}
    if not _same(_rows(captions, mapping, path.parent), result["cues"]):
        raise ValueError("frozen audio metadata or SHA-256 mismatch")
    return dict(result, manifest_path=str(path))


def preview_audio(audio_manifest, output_dir, tool_timeout=30):
    """Stream-copy the entire silent video and add aligned AAC, in a new folder.

    The original run, captions and WAVs are never changed. A failed directory
    may contain incomplete media but has no success manifest.
    """
    timeout = _timeout(tool_timeout)
    path = Path(audio_manifest).expanduser().resolve(strict=True)
    binding_entry = _entry(path)
    binding = verify_audio(path)
    captions = verify_sidecar(binding["caption_manifest"]["path"])
    video = _checked(binding["video"])
    original_info = _video_info(video, timeout)
    if original_info != binding["video_info"]:
        raise ValueError("video stream identity differs from checked binding")
    run = Path(captions["origin_receipt"]["path"]).resolve().parent
    root = _new_output(output_dir, [run, path.parent, Path(captions["manifest_path"]).parent])
    preview = root / "preview.mp4"
    rows = binding["cues"]
    rate = rows[0]["pcm"]["sample_rate"]
    total_samples = binding["duration_frames"] * rate // binding["fps"]
    filters = [
        f"[{index + 1}:a:0]adelay={row['offset_samples']}S:all=1[a{index}]"
        for index, row in enumerate(rows)
    ]
    filters.append(
        "".join(f"[a{index}]" for index in range(len(rows)))
        + f"amix=inputs={len(rows)}:normalize=0:dropout_transition=0,"
        + f"apad=whole_len={total_samples},atrim=end_sample={total_samples}[audio]"
    )
    inputs = [part for row in rows for part in ("-i", row["wav"]["path"])]
    _tool(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-n",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(video),
            *inputs,
            "-filter_complex_threads",
            "1",
            "-filter_complex",
            ";".join(filters),
            "-map",
            "0:v:0",
            "-map",
            "[audio]",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-c:v",
            "copy",
            "-threads",
            "1",
            "-c:a",
            "aac",
            "-ar",
            str(rate),
            "-ac",
            str(rows[0]["pcm"]["channels"]),
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(preview),
        ],
        timeout,
    )
    made = _video_info(preview, timeout, audio=True, expected_pcm=rows[0]["pcm"])
    if made != original_info:
        raise ValueError("preview changed video packets, timestamps, frames or duration")
    _tool(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-err_detect",
            "explode",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(preview),
            "-map",
            "0:a:0",
            "-f",
            "null",
            "-",
        ],
        timeout,
    )
    _checked(binding_entry)
    verify_audio(path)
    _checked(binding["video"])
    result = dict(
        schema_version=1,
        operation="audio_preview",
        status="preview_created_requires_review",
        binding_manifest=binding_entry,
        preview=_entry(preview),
        video_info=made,
        video_stream_unchanged=True,
        semantic_acceptance=False,
        listened=False,
        revision_eligible=False,
        notice=NOTICE,
    )
    _write(root / "manifest.json", result)
    return dict(result, manifest_path=str(root / "manifest.json"))
