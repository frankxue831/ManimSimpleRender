# Caption-linked frame samples

[Documentation index](README.md) | [Agent quick start](agent-quickstart.md)

The optional sampler builds on [caption sidecars](scene-captions.md), not the
old staged-caption renderer. It reads a completed public production run through
the caption sidecar's receipt and artifact identities. It never renders source.

```bash
python -m scene_production.visual_frames \
  --captions /absolute/captions/manifest.json \
  --output-dir /absolute/new-frame-samples --frames 0 14 29
```

The output directory must be new, with an existing parent, and outside both the
retained run and caption sidecar. Without `--frames`, one midpoint per explicit
cue is selected. At most 12 distinct frames are allowed. The initial supported
budget is 15 or 30 fps, no more than 120 seconds, and at most 1280 by 720 pixels.
These are sampling limits, not limits on the existing production renderer.

The sampler checks the sidecar before and after extraction. It uses exact decoded
frame indices, compares the exported PNG RGB24 hashes and sample geometry with
the selected source frames, and records each PNG hash and active cue's JSON
Pointer. It preserves the old candidate's image2 safety fix: a fixed relative
`frame-%03d.png` pattern inside an exclusively created output directory, supplied
via subprocess `cwd`. A user directory containing `%03d` never enters the output
pattern. `-n` alone is not treated as protection against path expansion. The same
strategy is now used by the public renderer's existing inspection screenshots.

`manifest.json` is published only after all checks succeed. Failure may leave an
incomplete, owned directory without a success manifest. Do not reuse it, remove
unrelated files, or interpret PNG existence as successful completion.

## What the evidence does not prove

- External SRT text is not burned into PNGs. Active cues are author annotations,
  not text detected in the picture. Uncovered time means unannotated time.
- Frame index divided by fps is not a promise about encoded packet PTS.
- Navigation identifies retained `source.py` and the copied events record. It
  does not find the responsible Python line/object, locate a current authoring
  checkout, or snapshot helper imports/assets. Copy retained input before edits.
- Sampling is not continuous playback, visual approval, fact checking, a
  listening test or evidence that an audience understands the video.
- Hashes are integrity checks, not authentication. This is a trusted local-file
  workflow without concurrent writes, not a hostile-filesystem sandbox.

The existing production frame-trace and revision locator retain their original
contracts. This sampler neither replaces them nor extends silent revision to
audio previews.

## Caption-free evidence packages

For an original successful run whose API/agent completion you have already
accepted, no caption sidecar is needed:

```sh
python -m scene_production.visual_frames --from-run /absolute/run/result.json \
  --output-dir /absolute/new-evidence
python -m scene_production.visual_frames --from-run /absolute/run/result.json \
  --output-dir /absolute/new-boundary-evidence --frames 0 14 15 30 44
python -m scene_production.visual_frames --from-run /absolute/run/result.json \
  --output-dir /absolute/new-all-frames --all-frames
```

The Python entry point is `visual_frames.export_run(from_run, output_dir, *,
frame_indices=None, all_frames=False, tool_timeout=30, operation_timeout=180,
cancel_event=None)`. `cancel_event`, if supplied, is a threading-style event.
The original `export(...)` caption interface, operation, midpoint selection and
limits are unchanged. `--all-frames` and `--operation-timeout` require `--from-run`.
Import, help and the caption path do not import Pillow, Manim or Qt.

Caption-free export requires optional `Pillow>=11` (the `[review]` extra, also
provided by the supported Manim extras), plus local ffmpeg/ffprobe with
`-fps_mode` support. From a trusted checkout, install the review-only Python
dependency with `python -m pip install -e ".[review]"`; ffmpeg/ffprobe must be
installed separately. Missing tools/dependencies fail explicitly; export never
installs software, executes saved source, contacts providers, or falls back to
`-vsync`. Pillow's embedded default font is used; without FreeType the embedded
bitmap font is enlarged for readable labels. No host fonts or browser are needed.

The new private, exclusively created output directory contains:

- `native/frame-001.png`, etc.: full-resolution RGB24 pixels decoded from the
  selected zero-based video ordinals. Filename numbering is one-based sequence
  numbering, not the original ordinal.
- `sheets/sheet-001.png`, etc.: up to 12 chronological previews per page. Native
  imagery fits in 320x180 without crop or upscaling; labels are outside tiles.
- `frames.json`: schema version 1, operation `run_frame_evidence`, frame-to-sheet
  mappings, identities, nominal grid times, and exact omitted intervals.
- `manifest.json`: version 1 success record, input and generated-artifact inventory,
  byte counts/hashes, tool versions, enforced limits, and export-only coverage.
- `private/diagnostics.jsonl`: best-effort phase/owned-child observations, not
  advertised as share-safe. It is not part of the content inventory. It ends
  before manifest publication: `diagnostics_cutoff=before_manifest_publication`.
  The sink is closed before `diagnostics_incomplete` is frozen, so that field
  includes failures of the final diagnostic event and close. No later log writes
  occur; publication itself is established by the completed manifest, not the log.

Native PNG compression level 1 preserves exact pixels. File hashes identify
produced bytes, not reproducibility across Pillow/ffmpeg versions. Source,
receipt, timeline, video and optional trace are referenced, not copied. The
package is not a self-contained video or authoring project. Trace bytes are
identity-checked only; section mapping and causal-source navigation are not
provided. HTML, per-frame PTS, a player and implementation fingerprints are not
part of this operation.

### Selection, coverage and timing

Default selection uses K=min(12,N), with index floor(j*(N-1)/(K-1)) for K>1 and
index 0 for N=1. Explicit selection requires 1..120 distinct non-boolean integers
in [0,N) and is sorted. All-frame selection is explicit and still subject to all
budgets; it is never silently sampled or downscaled.

For N=45 and indices 0,14,15,30,44, omitted half-open intervals are exactly
[1,14), [16,30), [31,44). Export coverage is not viewing coverage. Even an
exhaustive export leaves every review, approval, render-completion-verification,
audio and continuous-playback flag false. No reviewer or readiness is invented.

The exact rational grid coordinate is ordinal / reported `avg_frame_rate`;
its decimal counterpart is approximate. For example ordinal 8 at 15 fps is
labelled 8/15 seconds, not a historic requested 0.5-second seek. Grid time is not
PTS, presentation time, or CFR certification. Valid variable-timing inputs are
not refused merely because N/rate differs from stream duration. Reported rates,
duration and counts are preserved; a valid declared count must match the actual
bounded decode count.

### Supported local inputs and limits

Use trusted retained normal MSR H.264 8-bit yuv420p MP4 files only. This is not a
sandbox for hostile media or concurrently edited runs. A supported MP4 brand is
required and MOV demuxing is forced. External MOV data references and absolute
reference paths are disabled, in addition to restricting protocols to file/pipe.
Playlists, URLs, unsupported codecs, known HDR/unsupported color transforms,
rotation and declared non-square pixels are refused. Normal Manim outputs may
omit SAR: the original missing value is retained and previews use encoded raster
geometry, interpreting unspecified SAR as square. This is not independent
certification of display aspect or SDR/colorimetry. No autorotation/scaling is
applied to native evidence. Audio may exist but is excluded and unreviewed.

Policy limits, not renderer limits or throughput promises:

- Receipt and saved source: 2 MiB each; timeline: 64 KiB; trace: 16 MiB;
  video: 512 MiB. Metadata is gated before large artifact hashing; hashes use
  bounded chunks and the operation deadline.
- Geometry up to 3840x2160; positive rational nominal rate up to 60; selected
  stream duration up to 120 seconds; decoded frames up to 7200.
- Selected frames up to 120 and 100 million total native pixels. Twelve 4K
  frames fit; 45 all-4K frames do not.
- Cumulative pixel-work reservation up to 100 billion. It includes both source
  decode passes, a 64-frame lookahead allowance per pass, and five selected-native
  encode/read/resize operations, plus sheet composition and final decode. The count pass limit is reduced when needed
  to leave this headroom. A hard max+1 ffmpeg output-frame sentinel ends the
  count decode before an overlong input can run unbounded. Count uses decoded
  passthrough null output and structured final progress, not header counts or
  packet counts. Only selected RGB travels through Python during extraction.
- Storage is reserved conservatively from RGB scanline bytes plus 1 MiB PNG
  overhead per native/sheet, and 8 MiB metadata/private-log headroom. Every
  generated content byte has a pre-write aggregate/file cap. Fixed diagnostic
  event counts reserve 1 MiB separately. Free-space checking cannot reserve
  space against other processes; disk-full failures preserve incomplete output.

One selected native image and one sheet are processed at a time; decoder RSS is
not bounded by these policies. Pipe reads use 64 KiB chunks with a four-item
queue and fixed capture caps. Buffered overshoot is at most seven chunks plus OS
pipe buffers; it is not accumulated in the result. Owned children are terminated
then killed/reaped with up to two seconds of waits plus two 0.2-second reader
joins. This supervises direct ffmpeg/ffprobe children, not descendant trees.

Tool timeout defaults to 30 seconds, maximum 120; operation timeout defaults to
180, maximum 300. The remaining operation budget is applied to reads, hashing,
phases and writes. These are cooperative bounds, not hard real-time guarantees
against kernel/filesystem stalls. Longer high-resolution inputs may need:

```sh
python -m scene_production.visual_frames --from-run /absolute/run/result.json \
  --output-dir /absolute/new-evidence --tool-timeout 120 --operation-timeout 300
```

A local synthetic 4K30 42-second and 1080p30 90-second sparse-export smoke is part
of development verification. Its single-host results are not CI, a throughput
SLA, a detection benchmark, or evidence that any particular audience accepts a
video. Use local timing to choose tool/operation budgets.

### Completion and actual review

The intended workflow is accepted render completion -> export -> actually open
relevant sheets/native images -> explicit scoped assessment. A page header says
how many pictures it displays and how many the package exports; opening one page
does not imply opening the others. Reduced sheets cannot reveal every native
pixel. Open the identified native file where resolution matters. Neither stills
nor an exhaustive export establishes continuous playback/audio review.

Inputs and all required outputs are revalidated before publication. Manifest
publication links a fully written temporary file exclusively to its final name.
The output filesystem must support hard links within that directory. If it does
not, publication fails without a success manifest; choose a supported filesystem
and a new output directory rather than reusing the partial export.
Failures, cancellation, missing/corrupt artifacts, or changed inputs do not
publish a valid success manifest; owned partial output remains for inspection.
An existing directory, symlink output/ancestor, or output inside the retained run
is refused. Resolve benign platform path aliases before calling the exporter.
The new path writes generated PNG names directly through Pillow, so user path
percent signs never enter an image2 pattern; the legacy fixed-pattern safeguards
are unchanged.

CLI exit 0 establishes package finalization only. If stdout fails after final
manifest publication, delivery is uncertain but the package may already exist:
inspect the complete manifest and inventory before deciding what to do. Never
overwrite/retry into that directory automatically. A retained `.manifest-complete.tmp`
alone is not final success. Attaching a manifest to editorial review does not
recursively validate its referenced pictures; attach the relevant files and
state exactly what was actually opened and assessed.
