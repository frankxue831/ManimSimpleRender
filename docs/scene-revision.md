# Explicit scene revisions

[Documentation index](README.md) | [Agent quick start](agent-quickstart.md) | [Scene production](scene-production.md)

`python -m scene_production.revision` scopes single-section source edits in
trusted, local, silent Manim productions. The tool does not interpret feedback,
author animations, judge explanations, or render partial videos; you select a
stable section ID, write the replacement code, and re-render the complete scene
with `python -m scene_production` (or `scene-production` /
`python -m scene_production.agent render`).

## Source markers and manifest

Bracket each editable section inside ordinary Python source with paired markers:

```python
# scene:start lookup
# Normal Manim code for this section
# scene:end lookup
```

Create a UTF-8 manifest (for example, `manifest.json`) in the directory containing
your scene source, inspection timeline, and local dependency files, or in an
ancestor directory. All referenced paths must reside inside the manifest directory,
and every file role (`manifest`, `source`, `timeline`, and each entry in
`dependencies`) must resolve to a distinct file.

Manifest constraints (`manifest.json`):

- `version`: integer `1`.
- `source`, `timeline`: relative paths to the scene Python file and inspection
  timeline JSON file inside the manifest directory.
- `scene`: ASCII Manim `Scene` class identifier (`^[A-Za-z_][A-Za-z0-9_]*$`).
  `capture`, `verify`, and `audit` match this field against the original render
  receipt (`result.json` `command`) and reject receipts rendered for any other
  `Scene` class. (See [Provenance, previews, and alignment](#provenance-previews-and-alignment)
  for why reinspection receipts are also rejected.)
- `dependencies`: list of zero or more nonblank relative paths (`[]` allowed) to
  local helper files under the manifest directory.
- `brief`: object with nonblank strings `audience` and `goal`, and nonempty lists of
  nonblank strings `sources` and `pitfalls`.
- `segments`: ordered list of `1` to `64` section objects, each with:
  - `id`: unique ASCII identifier (`^[a-z][a-z0-9_-]*$`, length `<= 100`) matching
    both the source `# scene:start`/`# scene:end` order and the `label` order in
    `timeline.json`.
  - `captions`: list of zero or more nonblank strings (`[]` allowed for caption-free
    sections; do not invent placeholder text). Manifest `captions` declare author
    intent only; inspect rendered frames to verify on-screen text and legibility.
  - `objects`: nonempty list of nonblank strings naming key visual objects.
  - `depends_on`: list of zero or more earlier segment IDs (`[]` allowed).
    Dependencies are author-maintained mappings, not inferred runtime graphs.

Minimal `manifest.json` (one section):

```json
{
  "version": 1,
  "source": "scene.py",
  "timeline": "timeline.json",
  "scene": "Demo",
  "dependencies": ["visuals.py"],
  "brief": {
    "audience": "programmers",
    "goal": "explain lookup",
    "sources": ["primary source"],
    "pitfalls": ["not all queries are faster"]
  },
  "segments": [
    {
      "id": "lookup",
      "captions": ["key caption"],
      "objects": ["key13", "heap row"],
      "depends_on": []
    }
  ]
}
```

Matching `timeline.json` (see [scene production](scene-production.md) for schema and
limits; timelines configure inspection checks, not animation timing):

```json
{
  "version": 1,
  "segments": [
    {"label": "lookup", "start": 0, "end": 3, "samples": [1.5]}
  ]
}
```

## Revision workflow and edit rules

Run from an environment with Manim, `ffmpeg`, and `ffprobe` installed (include
`--record-scene-frames` on both renders if you will run `audit`; `verify` runs
with or without raw traces):

```sh
# 1. Render baseline run -> creates runs/baseline/scene-RUN/result.json
python -m scene_production --scene-file scene.py --scene Demo \
  --timeline timeline.json --output-dir runs/baseline --record-scene-frames

# 2. Capture immutable baseline snapshot -> prints {"snapshot": ".../snapshot.json"}
python -m scene_production.revision capture --manifest manifest.json \
  --run runs/baseline/scene-RUN/result.json --store versions

# 3. Locate target context and check transitive depends_on dependents -> plan.json
python -m scene_production.revision locate \
  --baseline versions/version-ID/snapshot.json --target lookup \
  --feedback "Explain the heap access" > plan.json
# If plan.json "dependent_segments" is nonempty, re-run with --acknowledge ID:
# python -m scene_production.revision locate \
#   --baseline versions/version-ID/snapshot.json --target lookup \
#   --feedback "Explain the heap access" --acknowledge summary --acknowledge tail > plan.json

# 4. Apply target replacement body -> edits.json replaces lines strictly between markers
python -m scene_production.revision apply --plan plan.json --edits edits.json

# 5. If section duration changed, update timeline.json AFTER apply and BEFORE re-render:
#    shift subsequent non-target segments' start/end/samples by the target delta
#    so segment label order and non-target durations stay identical, with samples inside [start, end].

# 6. Render full candidate scene
python -m scene_production --scene-file scene.py --scene Demo \
  --timeline timeline.json --output-dir runs/candidate --record-scene-frames

# 7. Audit (recommended for traced runs) or verify (same flags for untraced decoded checks)
python -m scene_production.revision audit \
  --baseline versions/version-ID/snapshot.json \
  --run runs/candidate/scene-NEW/result.json --target lookup
```

Subcommand contracts and file schemas:

- **`capture`**: creates a new `versions/version-*/` directory holding numbered file
  copies (`000.*`, `001.*`, ...), writes `snapshot.json` (`version`, `manifest`,
  `source`, `timeline`, `files` hashes, `receipt`/`receipt_sha256`, `video_sha256`,
  `segments`, `notice`), and prints `{"snapshot": "/abs/path/versions/version-.../snapshot.json"}`.
  `capture` never rewrites earlier versions. Snapshots and receipts are unsigned
  local records; their hashes verify snapshot-time file bytes, not Python packages,
  fonts, or system tools-keep the render environment unchanged between baseline and
  candidate runs.
- **`locate`**: prints plan JSON to stdout (redirect to `plan.json` in step 3)
  containing `version` (`1`), `snapshot`, `snapshot_sha256`, `target`, `feedback`,
  `scope` (`[target]`), `dependent_segments` (transitive `depends_on` descendants),
  `acknowledged_dependents`, `dependency_policy`, `notice`, and `context` (segment
  metadata, `first_line`/`last_line`, current `code`, timeline `time`, `observed_window`
  which is `null` when untraced, and `previews` with hash-verified preview PNG
  metadata). If `dependent_segments` lists IDs, re-run `locate` with `--acknowledge ID`
  for each one before `apply`; `apply` exits `1` if any dependent segment is
  unacknowledged. Acknowledgement never authorizes editing dependent sections or shared
  dependencies.
- **`edits.json` and `apply`**: `edits.json` maps only the single `target` ID to the
  complete replacement string (for example, `{"lookup": "        # body lines\n"}`)
  for lines strictly **between** `# scene:start <id>` and `# scene:end <id>`. Leave
  markers outside the replacement string, and preserve `self.next_section(...)` or
  `mark_section(...)` inside traced replacements. `apply` verifies baseline hashes
  (snapshot, manifest, dependencies, `source`, and `timeline`), validates Python
  AST syntax, checks unchanged non-target text and marker order, requires a non-empty
  diff, atomically replaces `source` via a temporary file, and prints a JSON receipt
  (`feedback`, `target`, `snapshot`, `before_source_sha256`, `after_source_sha256`,
  `semantic_author`, `scope`). Do not modify `manifest.json` or `dependencies` after
  `capture`; `locate`, `apply`, `verify`, `audit`, and `restore` reject changes to them
  (only `timeline.json` may change after `apply` if section duration changed).
- **`verify` and `audit`**: `verify` compares `timeline.json` intervals and decoded MP4
  frames across non-target sections without requiring raw traces; `audit` requires
  `--record-scene-frames` traces on both baseline and candidate render receipts and
  also checks raw RGBA hashes and timeline-to-trace alignment (see
  [Verification and rollback](#verification-and-rollback) and
  [Audit layers and exit codes](#audit-layers-and-exit-codes)). If edits intentionally
  alter non-target sections or shared helpers, expect `verify`/`audit` to flag it;
  review the candidate manually and run `capture` to start a new baseline.

Exit codes and stdout summary across subcommands (details on `restore` appear in
[Verification and rollback](#verification-and-rollback)):

| Subcommand | Exit `0` | Exit `1` | Exit `2` |
| --- | --- | --- | --- |
| `capture`, `locate`, `apply`, `restore` | Success (JSON on stdout) | Validation, guard, or I/O error | Argument usage error |
| `verify` | Non-target checks pass (JSON on stdout) | Changed non-target code/media or guard error | Argument usage error |
| `audit` | `"status": "unchanged_non_target"` (JSON on stdout) | Missing traces, invalid evidence, or guard error | `"status": "needs_review"` (JSON) or argument usage error (no JSON) |

On exit `1` and argument-usage exit `2`, commands print a plain-text error message to
stderr rather than JSON.

## Verification and rollback

`verify` checks that non-target source text, markers, and dependencies match the
baseline snapshot, validates receipt and file identities, and enforces matching
geometry, SAR, and frame rate on silent video (rejecting audio tracks and
nominal-to-average rate differences above `0.1%`, while tolerating container rounding).
It compares every decoded RGB frame, relative PTS, frame duration, and byte size
across non-target intervals and records per-segment checks under `"scenes"`. Stable
section IDs keep non-target comparisons aligned when the target duration changes,
provided non-target durations stay equal and boundaries align to the nominal frame
grid. `verify` does not require `--record-scene-frames` traces and compares
`timeline.json` intervals and decoded MP4 frames rather than raw RGBA hashes or
observed trace bounds (any `frame_trace` and native preview metadata attached to a
receipt is still validated for integrity and consistency). Unlike `verify`, `audit`
requires `--record-scene-frames` traces on both baseline and candidate render
receipts and exits `1` if either lacks one. Non-target comparison in both commands
checks every section outside the target even when `depends_on` omits a dependency.

After reviewing step 7:

- **To edit a different section after accepting a candidate**, run `capture` on the
  candidate run to create a new baseline first.
- **To revise `edits.json` and re-run `apply` against the same baseline**, run
  `restore` first so `source` and `timeline.json` match the baseline snapshot again.
  (`restore` leaves candidate runs in place; if the candidate render failed before
  producing a valid `result.json`, restore `source` and `timeline.json` manually from
  the hash-checked copies named in `snapshot.json`.)

```sh
# Roll back a candidate whose result.json exists
python -m scene_production.revision restore \
  --baseline versions/version-ID/snapshot.json \
  --run runs/candidate/scene-NEW/result.json
```

### Retention and restore verification

Keep the version store **and both the baseline and candidate render run directories**
intact (`result.json`, retained `source.py`, `timeline.json`, video, and optional
trace). The version store is not a standalone disaster-recovery archive: `restore`
verifies baseline artifacts and confirms current `source` and `timeline` match the
candidate receipt before copying, re-hashes restored files afterward, and fails
explicitly on partial writes.

Because `restore` copies `source` and `timeline` sequentially, it is **not a
crash-atomic multi-file transaction**. If files drift after a failed copy, a run
artifact is corrupted, or an I/O error interrupts writing, `restore` refuses
subsequent runs (there is no `--force` option); recover manually by inspecting and
copying the hash-checked files listed in `snapshot.json`.

## Optional observed frame evidence

Enabling `--record-scene-frames` and running `scene_production.revision audit` adds
pre-encoding raw RGBA hashes, native section bounds, and timeline-to-trace alignment
checks on top of untraced `verify`.

### Traced rendering requirements

The native frame recorder requires **Manim `0.22.1`** (Cairo backend only), matching
`pyproject.toml` render extras (`.[explainer]` or `.[gui]`), because it hooks
private `SceneFileWriter` APIs. Before executing scene code or installing hooks, the
isolated render process checks the imported Manim version; on mismatch, it logs the
detected version and an install hint in `<output-dir>/scene-.../render.log` without
installing or retrying. Untraced renders skip this version guard. Repository CI
exercises native traced Cairo renders on Ubuntu Linux (`Python 3.12`/`3.13`) only;
validate local fonts and macOS/Windows visual output directly on your target
environment.

For a full Cairo `Scene` render, pass `--record-scene-frames` to
`python -m scene_production` (or `scene-production` /
`python -m scene_production.agent render`) to record SHA-256 hashes of the exact
`uint8` RGBA arrays passed to Manim's writer before video encoding:

```python
# scene:start lookup
self.next_section("lookup")
self.play(...)
# scene:end lookup
```

- Place `self.next_section("stable-id")` (or `mark_section(self, "stable-id")`) at
  the start of each marked section, with the first call **before emitting any frame**
  (including any opening `self.wait(...)`).
- Use the exact segment IDs from `manifest.json` in manifest order.
- When updating code inside `# scene:start`/`# scene:end` via `apply`, preserve the
  section's `self.next_section(...)` or `mark_section(...)` call in `edits.json`.
- Tracing supports only single-`Scene` Cairo renders (`SceneFileWriter`,
  `skip_animations=False`) and rejects OpenGL/non-Cairo or non-`uint8` writers,
  zero-frame sections, `> 64` sections, `> 144,000` frames, trace JSON `> 16 MiB`,
  pixel dimensions outside `1..8192`, and rates outside `0 < rate <= 240`.

### Audit layers and exit codes

```sh
python -m scene_production.revision audit \
  --baseline versions/version-ID/snapshot.json \
  --run runs/candidate/scene-NEW/result.json \
  --target lookup
```

`audit` separates baseline vs. candidate evidence into distinct layers (manifest/source
boundaries, observed native frame bounds, raw RGBA hashes, decoded MP4 frame records,
and inspection-timeline alignment):

- **Exit `0` (`"status": "unchanged_non_target"`)**: every non-target section has
  `"classification": "unchanged"` across timing, raw RGBA, and decoded frames **and**
  `"inspection_windows_agree"` is `true` across **all** declared segments (including
  the edited target segment in both baseline and candidate timelines).
- **Exit `2` (`"status": "needs_review"`)**: valid evidence requires review because a
  non-target layer changed or `"inspection_windows_agree"` is `false` (for example, a
  segment's `timeline.json` window disagrees with observed trace bounds). If only
  `timeline.json` drifted against observed trace bounds on the candidate, fix
  `timeline.json` and re-render the candidate (`audit` rejects reinspection receipts).
- **Exit `1`**: invalid evidence, missing `--record-scene-frames` trace on either
  render, or scope/guard failure.
- **Section `"classification"` literals**: `"unchanged"`, `"timing_changed"`,
  `"render_output_changed"`, or `"encoded_output_changed"` (differences introduced after
  raw rendering, such as encoding or postprocessing). Comparisons use exact byte/hash
  matching with zero pixel tolerance; passing `audit` confirms non-target machine
  invariance only, not target content quality.

### Deterministic pauses and state isolation

`scene_production.frame_clock.hold_until_frame(scene, integer_deadline)` inserts a
frozen pause up to `integer_deadline` (an absolute 0-based cumulative emitted-frame
index across the render, supporting integer frame rates `1..240`). It pauses without
retiming animations or running updaters, and works in both traced renders (reading
the trace frame counter) and untraced renders (deriving frames from renderer time).
`hold_until_frame` raises `ValueError` on frame-deadline overruns (plus traced
renders reject zero-frame sections). Call `scene_production.frame_clock.mark_section(scene, "id")`
at the start of a section to emit `scene.next_section("id")` and return its starting
emitted-frame count (`scene_production.frame_clock.frame_count(scene)`), for example:

```python
start = mark_section(self, "tail")
# ... animations for this section ...
hold_until_frame(self, start + 72)
```

A stable Python variable name does not isolate Manim object state across sections.
Repeated `Transform` calls can retain aligned point/submobject history, and `become`
does not perform a general state reset. Where downstream typography must remain
independent, remove the shared caption and construct a fresh `Text` object at the
section boundary; share persistent objects intentionally and declare `depends_on`
entries.

### Provenance, previews, and alignment

Reinspection (`scene-production inspect`) copies and validates an existing `frame_trace`
from an original render receipt without re-running Manim. Because reinspection receipts
omit the original render `command`, `capture`, `verify`, and `audit` reject them;
always pass original render receipts to those commands. When a trace is attached,
inspection previews select exact decoded frame indices (`native_frame_index` and
`native_time`), whereas `time` retains the requested check timestamp (`native_time` is
the nominal frame-grid coordinate, not encoded packet PTS). Without a trace, previews
use timestamp seeking near boundaries; enable `--record-scene-frames` when exact
boundary frames matter.

`frame_clock.frame_coordinate` converts JSON float seconds to an exact fraction and
accepts the nearest integer frame within `8` ULPs of that coordinate (recording the
exact fraction, error, and bound under `audit`'s `inspection_alignment` output; use full
binary64 values in `timeline.json` such as `31 / 24` = `1.2916666666666667` rather
than coarse decimals). Both `audit` inspection-window alignment and traced inspection
preview selection use this `8`-ULP tolerance (outside `8` ULPs, `audit` reports
misalignment and preview selection rounds up with `ceil`). By contrast, `verify`
passes `timeline.json` segment boundaries directly to decoded frame comparison, which
requires exact integer `seconds * fps` values without `8`-ULP tolerance (`audit`
uses observed trace bounds for decoded comparison and accepts `31/24` in `timeline.json`).

## Optional changed-frame navigation

Add `--localize-frames` to `revision audit` to add a top-level `frame_localization`
object to `audit`'s stdout JSON (without changing `audit`'s `"status"`, section
classifications, or exit code):

```sh
python -m scene_production.revision audit \
  --baseline versions/version-ID/snapshot.json \
  --run runs/candidate/scene-NEW/result.json \
  --target lookup --localize-frames
```

Top-level `frame_localization` response shapes (`audit`'s outer `"status"` and exit
code remain unchanged in both cases):

- **When `"status"` is `"available"`**: contains `version` (`1`), `status`
  (`"available"`), `scope` (`"non_target_sections"`), rational string `frame_rate`
  (such as `"24/1"`), `inspection_alignment` (`true`/`false`), `selection`
  (`"deterministic_navigation"`), `representative` (`false`), `visual_acceptance`
  (`false`), `sections`, and `notice`.
- **When limits are exceeded**: contains only `status` (`"unavailable_resource_limit"`),
  `scope` (`"non_target_sections"`), `frame_rate`, and `inspection_alignment`
  (omitting `version`, `selection`, `representative`, `visual_acceptance`, `sections`,
  and `notice`).

Abbreviated 1-section `frame_localization` shape inside `audit` stdout JSON (showing
`raw_rgba_hash` from the five `layers` keys and omitting top-level `notice`):

```json
{
  "frame_localization": {
    "version": 1,
    "status": "available",
    "scope": "non_target_sections",
    "frame_rate": "24/1",
    "inspection_alignment": true,
    "selection": "deterministic_navigation",
    "representative": false,
    "visual_acceptance": false,
    "sections": [
      {
        "id": "tail",
        "before_frames": [100, 200],
        "after_frames": [124, 224],
        "alignment": "section_local_ordinal",
        "non_pixel_record_changes": false,
        "layers": {
          "raw_rgba_hash": {
            "compared_count": 100,
            "changed_count": 2,
            "first_changed_offset": 10,
            "last_changed_offset": 11,
            "offset_ranges": [[10, 12]],
            "total_range_count": 1,
            "returned_range_count": 1,
            "truncated": false
          }
        },
        "samples": [
          {
            "section_offset": 10,
            "before_frame": 110,
            "after_frame": 134,
            "pixel_layers": ["raw_rgba_hash", "decoded_rgb_md5"]
          },
          {
            "section_offset": 11,
            "before_frame": 111,
            "after_frame": 135,
            "pixel_layers": ["raw_rgba_hash", "decoded_rgb_md5"]
          }
        ],
        "sample_candidates": 2,
        "omitted_sample_candidates": 0
      }
    ]
  }
}
```

Per-section structure and selection limits:

- When `alignment` is `"section_local_ordinal"` (matching baseline and candidate frame
  counts), `layers` reports `raw_rgba_hash`, `decoded_rgb_md5`, `decoded_relative_pts`,
  `decoded_duration`, and `decoded_byte_size` (`decoded_equal` compares the full
  `(relative_pts, duration, byte_size, rgb_md5)` tuple), each holding
  `total_range_count`, `returned_range_count`, and `truncated`. Unmatched-length
  sections report `alignment: "unavailable_count_changed"` (or decoded mismatch
  variants) with `samples: []`, `sample_candidates: 0`, `omitted_sample_candidates: 0`,
  and omit `layers` and `non_pixel_record_changes`.
- Range offsets are half-open section-local indices `[start, end)`; add
  `before_frames[0]` or `after_frames[0]` to obtain 0-based `before_frame` and
  `after_frame` source video ordinals.
- Localization retains up to `64` ranges per section layer and `256` ranges total
  across the report.
- For each pixel-eligible changed section (`alignment == "section_local_ordinal"` with
  changed pixels), localization picks up to `6` deduplicated offsets (first, middle,
  and last changed frames in raw/decoded pixel layers), returns up to `12` paired
  samples round-robin across pixel-eligible sections in manifest order, and counts
  `sample_candidates` and `omitted_sample_candidates`. Samples are navigation hints, not
  severity scores or statistically representative frames.
- Localization caps the `frame_localization` JSON payload at `256 KiB` (with an
  internal `512`-section guard, though manifests and traces cap at `64` sections) and
  reuses existing decoded vectors without extra rendering, decoding, or disk writes.

Export decoded PNG frames for a differing `before_frame` / `after_frame` pair by
querying each corresponding run separately via
[caption-free frame evidence](scene-frame-samples.md#caption-free-evidence-packages)
(subject to that exporter's own frame, resolution, and duration limits):

```sh
python -m scene_production.visual_frames \
  --from-run runs/baseline/scene-RUN/result.json \
  --output-dir frame-evidence/before --frames 110
python -m scene_production.visual_frames \
  --from-run runs/candidate/scene-NEW/result.json \
  --output-dir frame-evidence/after --frames 134
```

## Executable revision example

Run from a source checkout with Manim `0.22.1`, `ffmpeg`, and `ffprobe` installed:

```sh
python -m examples.revisions.run_flow --output /tmp/new-revision-flow
```

`--output` must point to a nonexistent path (missing parent directories are created
automatically). The example runs locally from pre-authored inputs without network,
API, or LLM calls, writing videos, receipts, a version store, `plan.json`,
`audit.json`, and `example-result.json` under `--output`: it renders a 17-second
baseline; runs `capture`, `locate` (acknowledging `tail`), and `apply`; writes a
19-second revision timeline; renders the candidate; runs `audit`; executes `restore`;
and replays the edit to verify exact file identity.

Before constructing `Text` objects, the example checks glyph coverage via
`examples/scenes/scene_font.py`; set `SCENE_FONT` to request an installed font family.
This helper applies only to the example script; `ffmpeg` decoding alone does not
detect missing-glyph boxes in arbitrary scenes.
