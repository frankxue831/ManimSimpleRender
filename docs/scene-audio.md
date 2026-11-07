# Explicit WAV audio previews

`scene_production.audio` is a Qt-free, trusted-local service for attaching
**existing, explicitly selected WAV files** to an existing caption sidecar.
It never generates speech, uploads files, edits scene source, retimes captions,
or modifies the production run. It does not infer which recording belongs to
which cue.

An audio preview is a separate deliverable for human watching and listening.
**It is not a revision acceptance artifact:** the public revision checker is
silent-only. Both binding and preview receipts explicitly record
`semantic_acceptance: false`, `listened: false`, and `revision_eligible: false`.
Hashes identify bytes, not meaning, voice quality, authenticity, or approval.

## Python API

First create a caption sidecar using the workflow in
[scene-captions.md](scene-captions.md). Use the sidecar's cue IDs, not SRT ordinal
numbers, to map **every** cue exactly once:

```python
from scene_production.audio import bind_audio, verify_audio, preview_audio

checked = bind_audio(
    "caption-sidecars/captions-EXPLICIT-ID/manifest.json",
    {"intro": "recordings/intro.wav", "explanation": "recordings/explanation.wav"},
    "deliverables/audio-inputs-01",  # must not exist; parent must already exist
    tool_timeout=30,
)
verify_audio(checked["manifest_path"])
preview = preview_audio(
    checked["manifest_path"],
    "deliverables/audio-preview-01",  # another new directory
    tool_timeout=30,
)
print(preview["preview"]["path"])
```

`bind_audio(captions_manifest, wav_by_cue, output_dir, tool_timeout=30)` returns a
manifest dictionary plus its absolute `manifest_path`. It checks the retained
successful run and caption provenance using `captions.verify_sidecar`, probes
the actual silent video, validates WAV timing, and freezes each WAV into a new
numbered file. It does **not** create preview media.

`verify_audio(manifest_path)` rechecks the caption source, frozen WAV hashes,
PCM declarations, and derived cue metadata without invoking media tools. It
also checks the retained `video_info` field set and types, positive geometry,
canonical clock strings and frame count against the caption sidecar, and the
format of the recorded packet SHA-256. It returns the same manifest dictionary
plus `manifest_path`. These checks do not remeasure video or recompute packet
identity: the video metadata comes from the retained bind-time probe;
`preview_audio` probes again before composing.

`preview_audio(audio_manifest, output_dir, tool_timeout=30)` verifies the
binding again, stream-copies the **whole** silent video to `preview.mp4`, and
adds one AAC track aligned to the cue starts. Gaps and unused cue tails are
silence. It checks the preview, writes `manifest.json` only on success, and
returns that manifest plus `manifest_path`.

Output directories must be new and outside the original run, caption sidecar,
and, for previews, audio-binding directory. Existing files and symlinks are
never overwritten. Failed output directories may retain partial files for
inspection; **a video file alone is not success**. Retry into a new directory.
All media commands use argument arrays with `shell=False`, not shell command
strings. Spaces, quotes, and percent signs in paths are literal. Each media
command has the specified finite timeout in `(0, 300]` seconds; it is a
per-command limit, not a total workflow deadline.

## Input contract and checks

- One original silent video stream, starting at zero, at most 600 seconds.
  Its frame rate, decoded frame count, and exact duration must match the
  caption sidecar. No selection of a partial clip is inferred.
- Between 1 and 64 ordered, nonoverlapping caption cues. WAV mapping must cover
  all cue IDs, with no extras. The same recording may be explicitly chosen for
  more than one cue. Unannotated video ranges remain unannotated; they are not
  proof that no text appears on screen.
- Ordinary RIFF WAVE with integer PCM, mono or stereo, 16/24/32 bits. The RIFF
  length, chunk boundaries, format, byte rate, block alignment, and complete
  sample count are checked. Empty, truncated, compressed, floating-point,
  RF64/RIFX, and WAVE_FORMAT_EXTENSIBLE inputs are rejected.
- All recordings use the same channels, sample rate, and bit depth. Supported
  sample rates are 8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000,
  64000, 88200, and 96000 Hz. Cue starts and video end must align exactly to
  that sample grid. Use 48000 Hz for common integer video rates.
- Each WAV starts at its cue start and must finish by its cue end. No speeding
  up, truncation, resampling, gain normalization, or automatic extension of
  scenes is performed. PCM conversion for AAC encoding and lossy AAC encoding
  still occur. A recording at a cue's exact time limit is accepted.
- Individual WAVs are bounded to 512 MiB and 600 seconds. Silent recordings
  are not automatically rejected; nobody has listened to the result.

The binding freezes WAV **copies** and hashes their bytes before and after
copying. Later changes to the original WAVs do not affect the frozen binding.
Use a fresh binding to choose updated recordings. The original run and caption
sidecar remain dependencies and are checked again for changes; this is not a
portable standalone bundle.

The preview compares the original and copied video codec, geometry, pixel
format, decoded frame count, frame rate, duration, and a digest of compressed
packet data hashes plus PTS, DTS, duration, size, and flags. Video must remain
identical under that comparison. Audio must have the expected AAC codec,
channel count, sample rate, zero start, and duration within one AAC frame of
the video duration; it is decoded again with error detection. The final
preview file receives its own SHA-256. These are mechanical checks, not
listening or semantic acceptance. AAC is not bit-identical to source PCM.

Local receipts are unsigned, editable records. This workflow detects
inconsistent or changed retained inputs; it is not a sandbox, a signed
provenance system, or protection against a producer rewriting every record.
Do not mutate inputs concurrently with binding or preview creation.

## Migration scope

This service selectively reworks useful contracts from the earlier
`app/render/voice_bridge.py` and `voice_tools/{prepare_from_srt,preflight,make_preview}.py`:
explicit mapping, strict WAV completeness and timing checks, frozen identities,
new output directories, bounded subprocesses, and honest review-required
results. It does not import or copy that whole pipeline.

Not migrated: the old `staged_caption` dependency, Qt dialogs/workers, external
runtime launcher, job recovery UI, automatic TTS, SRT-index mapping, clip
selection, subtitle embedding, and video re-encoding. Captions remain a
separate sidecar; the audio preview contains only the original video and an
audio track. No storyboard or silent-revision integration is implied.

## Verification

```bash
python -m unittest tests.test_production_audio tests.test_architecture
```

The audio tests cover input snapshots, changed bytes, exact cue coverage,
format and duration errors, path isolation, finite subprocess timeouts,
failed/changed previews, and a real ffmpeg/ffprobe integration chain from a
successful-run fixture through `captions.bind` to an AAC preview. The media
integration test skips when ffmpeg or ffprobe is unavailable. It does not run
Manim or perform listening or human visual review.
