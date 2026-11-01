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
