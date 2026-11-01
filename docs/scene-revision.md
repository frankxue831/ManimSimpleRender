# Explicit scene revisions

`python -m scene_production.revision` is a small helper for trusted local, silent
Manim productions. The caller selects a stable scene ID and writes the code edit.
It does not understand feedback, author animations, judge explanations, or render
only part of a video. Render the complete scene with the existing CLI after edits.

Add paired comments inside ordinary Python source:

```python
# scene:start lookup
# normal Manim code for this section
# scene:end lookup
```

The manifest Scene class must match the canonical render command; a different
class in the same source file is rejected. Reinspection receipts without an
original render command are not accepted as capture baselines.

A UTF-8 manifest next to the source describes the audience, goal, primary sources,
pitfalls, and each section's key captions, object identities and state dependencies.
It names source, inspection timeline, Scene class, and local dependency files.
All files must be inside the manifest directory; dependencies must be distinct.
The ordered IDs must exactly match the inspection timeline labels and source
markers. Dependencies reference earlier sections. These are author-maintained
mappings, not automatic semantic or dependency inference. The timeline remains
inspection configuration, not an animation language.

Minimal manifest shape (one section):

```json
{"version":1,"source":"scene.py","timeline":"timeline.json","scene":"Demo",
 "dependencies":["visuals.py"],
 "brief":{"audience":"programmers","goal":"explain lookup",
          "sources":["primary source"],"pitfalls":["not all queries are faster"]},
 "segments":[{"id":"lookup","captions":["key caption"],
              "objects":["key13","heap row"],"depends_on":[]}]}
```

Run from an environment with the existing renderer/ffmpeg available:

```sh
python -m scene_production.revision capture --manifest manifest.json --run output/scene-RUN/result.json --store versions
python -m scene_production.revision locate --baseline versions/version-ID/snapshot.json --target lookup --feedback 'Explain the heap access' > plan.json
python -m scene_production.revision apply --plan plan.json --edits edits.json
# Render the full scene using the existing scene_production CLI.
python -m scene_production.revision verify --baseline versions/version-ID/snapshot.json --run output/scene-NEW/result.json --target lookup
python -m scene_production.revision restore --baseline versions/version-ID/snapshot.json --run output/scene-NEW/result.json
```

`edits.json` maps exactly the plan's target ID to the complete replacement body,
including Python indentation and line endings. The replacement cannot insert
markers. An obsolete baseline, changed manifest/dependency, changed code outside
the target, or mismatched rendered input identity is rejected. If locating finds
transitive dependent scenes, explicitly pass `--acknowledge ID` for each when
creating the plan. They are regression-checked, not silently authorized for edits.
A shared helper change requires a new explicit baseline/scope, not bypassing guards.

Capture creates a new version directory, retains copies and hashes, and never
rewrites earlier versions. Locate returns source lines, captions, objects,
dependent IDs, time interval and verified native preview references. Apply checks
preimages and Python syntax before replacing source through an exclusive temp
file. It does not update semantic caption mappings on the author's behalf.

Verify checks unedited source text, dependencies, input/video identities, frame
rate and geometry/SAR. It compares **every decoded RGB frame**, relative PTS,
duration and size in non-target intervals. Stable IDs map intervals when target
length changes; non-target durations must stay equal and boundaries must align
to the nominal frame grid. Nominal/average rate differences above 0.1% are rejected;
small container rounding is tolerated. This is intended for ordinary Manim frame-grid
output, not arbitrary VFR media. Audio-bearing videos are rejected. The decoded
frame checks cannot establish meaning, reader understanding, or visual quality.

Restore requires current source/timeline to match the specified rendered candidate,
then restores the baseline copies; candidate videos and records remain available.
It is a normal-operation guarded restore, **not a crash-atomic multi-file transaction**.
On disk/permission failure, retained copies may need manual recovery. Snapshots
and receipts are unsigned local records; their hashes do not authenticate them.
Dependency hashes are captured at snapshot time, not proof of the render environment.
No performance or cost saving is promised without a fair measured baseline.

## Optional observed frame evidence

For a full ordinary Cairo Scene render, `scene-production ... --record-scene-frames`
records SHA-256 hashes of the exact uint8 RGBA arrays passed to Manim's writer,
before video encoding. Add ordinary `self.next_section("stable-id")` calls at the
beginning of each marked scene region. The first section must begin before any
frame, IDs must be unique, and the observed order must match the manifest. The
flag is opt-in: existing renders and legacy `revision verify` keep their behavior.
Only one Scene/writer, full non-skipped rendering and bounded traces (144,000
frames) are supported. This path uses the installed Manim writer API; OpenGL and
other Manim versions/platforms require separate validation.

Use `python -m scene_production.revision audit --baseline SNAPSHOT --run RESULT
--target ID` for separate exact evidence layers: native frame bounds, raw RGBA,
decoded MP4 frames and inspection timeline alignment. Exit 0 means these machine
layers are unchanged outside the target; exit 2 means valid evidence needs review;
exit 1 means invalid evidence or a guard failure. Encoding-only differences remain
visible and require review. There is no image tolerance and no automatic semantic
acceptance. Wrong words, missing teaching objects or bad target content can pass
scope checks: an author/reviewer still judges them.

`scene_production.frame_clock.hold_until_frame(scene, integer_deadline)` supplies
an explicitly frozen pause up to a video-frame deadline and rejects overruns. It
does not retime animations, preserve updaters or render only part of a source.
Integer frame rates from 1 through 240 are supported; use it only when a frozen
pause is intended. With the recording flag it uses the emitted-frame counter;
otherwise it uses the renderer clock and checks the result. For independent
sections, anchor a duration to that section's observed start rather than a fixed
whole-film deadline when earlier durations may change. Inspection timelines remain
separate author-maintained check configuration, never animation drivers.

A stable Python variable name does not isolate Manim object state. Repeated
`Transform` can retain aligned point/submobject history; a later `become` is not a
general state reset. Where downstream typography must be independent, remove the
shared caption and create a fresh canonical Text at the boundary. Persistent
teaching objects can remain shared; declare dependencies and compare actual output.

Receipts and hashes are local unsigned evidence, not proof of semantic correctness,
provenance security or a complete reproducible environment. Reinspection carries
and validates the optional trace but does not re-run Manim or manufacture raw
observations for an older untraced video.

Traced previews select an exact decoded frame index and record
`native_frame_index`/`native_time`; `time` remains the requested check time.
`native_time` is the frame-grid coordinate, not an observed encoded packet PTS.
Untraced previews use timestamp seeking and record only the request. The locator
uses observed frame indices when available. A PNG requested near a section boundary
is never proof of its exact timestamp by itself.

For audit alignment only, JSON float seconds may be mapped to the nearest integer
frame within eight ULPs of the frame-coordinate representation; the original
fraction, error and bound are included in the report. This absorbs finite decimal
representation error (for example 31/24 seconds), not an image tolerance or a
fraction-of-frame editing allowance. Legacy strict `verify` is unchanged.

## Executable revision example

From a source checkout with the existing render dependencies installed:

```bash
python -m examples.revisions.run_flow --output /tmp/new-revision-flow
```

The output directory must not exist. The example copies its authoring files there,
then renders a17-second baseline, captures a version, locates the authored target,
applies an authored replacement and an explicit inspection timeline, renders the
19-second revision and audits both. Finally it actually restores and replays the
source and timeline and checks exact replay identity. It leaves both full videos,
receipts, source versions and the audit locally. It performs full renders and no
network/API calls. A machine success still requires human semantic/visual review.
The script, feedback, captions and edits were written by an assistant; the tool
does not understand the feedback or design this animation automatically.

Manifest captions, objects and dependencies are author-maintained descriptions,
not an automatically extracted transcript or complete runtime dependency graph.
The regression audit still checks every non-target section, even when a dependency
was omitted. An `encoded_output_changed` classification names the observed layer;
it cannot distinguish harmless compression from harmful postprocessing and never
auto-accepts either.

The example checks installed-font coverage for each actual caption before
constructing Text, including newly supplied edit text. `SCENE_FONT` requests an
installed family. This example-local helper is not automatic glyph validation
for arbitrary Scene scripts; media decoding alone cannot detect a tofu glyph.
