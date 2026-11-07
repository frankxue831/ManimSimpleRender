# Explicit caption sidecars

`scene_production.captions` attaches author-supplied caption annotations to an
existing successful public `scene_production` render receipt. It does not render
again, execute the recorded or current scene source, call OCR/TTS, modify a
storyboard, or alter any file in the original run.

**Annotations are claims by their author.** A valid sidecar proves consistency of
retained bytes, declared integer frame rates, and decoded frame counts. It does not prove that
caption text appears on screen or that all visible captions were annotated.
Hashes provide integrity, not authenticity. Imported modules, dependencies, and
the render environment are not fully snapshotted by the original run.

## Events format and frame contract

An explicit JSON record contains integer `fps` (1..1000), positive integer
`duration_frames`, and a nonempty ordered `events` list (at most 10000 entries):

```json
{
  "fps": 15,
  "duration_frames": 45,
  "events": [
    {"id": "question", "start_frame": 0, "end_frame": 15, "text": "Which boundary?"},
    {"id": "answer", "start_frame": 20, "end_frame": 45, "text": "Move hi."}
  ]
}
```

Intervals are half-open `[start_frame, end_frame)`. Adjacent events are allowed;
overlap and out-of-order events are rejected. Frames must be nonnegative
integers, the end must exceed the start, and no end may exceed the duration.
IDs must be unique nonblank strings. Each text is one visible Unicode line;
control characters, line separators, lone surrogates and invisible-only text are
rejected. SRT millisecond boundaries use nearest-integer rounding from the frame
clock. Fractional-rate video such as 30000/1001 is outside this version's contract.

Unknown event or record fields, duplicate JSON keys, nonfinite constants, and
files over 2 MiB are rejected. Two optional top-level fields are accepted:
`notice` (a string) and `uncovered_frame_ranges` (which must exactly match the
computed gaps). Coverage counts *explicitly annotated frames*, not visible
captions. Unannotated gaps do not mean the screen is caption-free.

For author instrumentation, `app.render.caption_events.CaptionEvents(fps)`
provides `start(id, text, scene_time_seconds)`, `end(id, scene_time_seconds)` and
`stage(directory, duration_frames)`. Times must land on frame boundaries. The
caller is responsible for supplying actual emitted-frame scene times; this
helper neither observes the renderer nor infers caption object lifetimes.
`stage` creates `events.json` exclusively and refuses to overwrite an existing
file. An unfinished event prevents staging.

For a standalone unverified SRT draft, without binding to a video:

```sh
python -m app.render.subtitle_draft events.json --output draft.srt
```

This command never overwrites an existing draft. Its output remains unverified
until separately bound and inspected; binding still does not establish that the
text is visible on screen.

## Stable Python API

```python
from scene_production.captions import bind, verify_sidecar, verify_text_revision

manifest = bind(
    from_run="/absolute/render-run/result.json",
    events="/absolute/events.json",
    output_dir="/absolute/caption-sidecars",
    tool_timeout=30,
)
checked = verify_sidecar(manifest["manifest_path"])
```

`bind` accepts only an original successful render receipt. Failed receipts and
reinspection chains are rejected, even if they retain a completed artifact.
It rechecks the canonical receipt, retained `source.py`, retained `timeline.json`
and video SHA-256 identities. The current external source file is not imported,
executed or needed. Original run files must stay available and unchanged.

`ffprobe -count_frames` independently checks the selected video's decoded frame
count, nominal and average integer rates, and dimensions. The saved frame count
must exactly equal `duration_frames`; the stored and decoded frame counts must
agree when both are available. Optional original frame-trace evidence is also
validated against the same stream. This is metadata/count verification, not a
frame-by-frame presentation timestamp analysis or semantic visual acceptance.
The contract uses frame indices: it does not certify constant frame intervals or
zero-based presentation timestamps.

Each call creates a new exclusive `captions-*` directory beneath `output_dir`.
Output inside the original run is rejected, including a symlink resolving there.
The directory contains the unchanged input `events.json`, derived `draft.srt`,
recorded `probe.json`, and finally `manifest.json`. Errors never publish a success
manifest. A late failure may retain an incomplete sidecar directory for diagnosis.

The version-1 manifest contains:

- `schema_version: 1`, `operation: "captions"`, `status: "success"`, `run_dir`.
- `origin_receipt`, `source`, `timeline`, `video`: absolute `path` and `sha256`
  records identifying retained original run files. No video copy is made.
- `events`, `srt`, `probe`: absolute `path` and `sha256` records for sidecar files.
- `events_origin`: the original input's absolute path, as provenance only. The
  input may later disappear or change; the exact retained events snapshot is used.
- `fps`, `duration_frames`, `width`, `height`.
- `cues`: copies of each validated event plus `event_pointer`, an RFC 6901 JSON
  Pointer such as `/events/0` into the retained events snapshot.
- `coverage`: `covered_frames` and `uncovered_frame_ranges`.
- `notice`: explicit limits on claims and provenance.

Both API functions return this dictionary with an additional absolute
`manifest_path` convenience field; that field is not persisted in the manifest.
Unknown manifest fields or versions are rejected rather than guessed. Manifest
reads have a separate 16 MiB limit to accommodate derived cue/provenance fields.

`verify_sidecar` revalidates original and sidecar hashes, derivations, SRT text,
coverage, pointers, timing, geometry and saved probe consistency. It does not run
media tools again: the recorded probe is reused only for the identical video
hash. It is not a portable bundle verifier and does not authenticate an arbitrary
third-party manifest. Downstream audio or visual inspection should bind the
manifest file hash and use the returned `cues`, not reverse-parse SRT text.

## Narrow text revision check

```python
review = verify_text_revision(old_manifest_path, new_manifest_path)
```

The function requires identical origin receipt, source, timeline and video
identities, identical event IDs/order/timing and exactly one changed `text`.
It reports the changed text pointer (for example `/events/0/text`). It writes
nothing. It refuses changed timing, multiple text changes, no-op edits and
another encoded video. It never claims pixel equivalence or acceptance across
different encodings and does not update source or storyboard content.

## Migration scope

The small Qt-free author-event and SRT primitives were selected from the local
prototype's `app/render/caption_events.py` and `subtitle_draft.py`, with their unit
tests. Validation and coverage calculations now share the same draft contract.
The production adapter is new and targets the public successful-run layout.

The prototype's `staged_caption` renderer, GUI integration, external caption
runtime, caption preview renderer, automatic text-to-scene editing and broad
cross-video caption acceptance were deliberately not migrated. There is one
production rendering path, with these annotations attached afterward.
