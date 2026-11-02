# Ordinary Scene production

This optional CLI renders a trusted ordinary Manim `Scene`, then checks the
resulting media. It does not generate scenes or judge teaching quality.

Use Python 3.12 or 3.13 with this project's `.[gui]` or `.[explainer]` extra;
`ffmpeg` and `ffprobe` must be on PATH. From a checkout:

```sh
python -m scene_production --scene-file examples/scenes/netty_eventloop.py --scene NettyPuzzleComparison --timeline examples/inspection/netty.json --output-dir output --quality qm --timeout 300
```

After installing the project, `scene-production` accepts the same arguments.
Paths are resolved from the caller's current directory. Scene sources and their
sibling imports stay in their original location. Each run creates a new directory.

A timeline is inspection configuration, not animation input. It describes expected
video duration and sample times; changing it cannot change the animation:

```json
{"version":1,"segments":[{"label":"intro","start":0,"end":2,"samples":[1]}]}
```

Segments must be contiguous from zero and end within 600 seconds. Limits: 64 KiB
JSON, 64 segments and 256 samples including automatic samples at each segment's
10% and 90% positions. Explicit samples must be unique and strictly inside the
segment. Duplicate keys, booleans and nonfinite numbers are rejected. Expected
end must match video-stream duration within two frames plus 0.05 seconds.

The checker probes video-stream metadata, fully decodes video, extracts each PNG
sequentially at native resolution, and checks PNG dimensions and hashes. Each
subprocess has a finite timeout (`--tool-timeout`, default 30 seconds). At most
259 inspection subprocess calls are made. No batching or performance gain is
claimed. Success means these media checks passed, not that the explanation is
correct or visually effective. Review native-size frames and temporal sequences.

Runs retain source/timeline snapshots, video, frames, hashes, probe metadata and
logs. Failures retain their stage and error when receipt persistence succeeds;
input validation fails before creating a directory. A rendered video's identity
is recorded before checking it; a failed media check never becomes success.
Only trusted Python should be rendered: this is not a sandbox. Source bytes are
compared before and after rendering; transient changes cannot be ruled out.
Imports, fonts, external assets and the environment are not fully snapshotted.

## Reinspect an unchanged recorded video

```sh
scene-production inspect --from-run output/scene-RUN/result.json --timeline corrected.json --output-dir rechecked
```

This does not execute today's Scene or invoke Manim. It accepts an original
canonical render receipt, verifies retained source/timeline/video identities and
root boundaries, copies the video and source snapshot into a fresh run, and uses
the same full decoder and sequential PNG checker. It never changes the old run.
Use it to correct inspection times or repeat a media check. Changes to picture,
text, logic or narration require rendering again. Reinspection chains are rejected.
Old failed receipts without a recorded completed-video identity are rejected;
current hashes cannot reconstruct historical identity. Hashes detect changed
bytes but do not authenticate unsigned receipts. API:
`scene_production.reinspection.reinspect(from_run, timeline, output_dir)`.

## Observed scene revision evidence

For the optional full-Cairo revision path, add native `next_section("stable-id")`
calls and pass `--record-scene-frames`. Call `self.next_section("stable-id")`
(or `mark_section`) before the first emitted frame, including the first `play` or
`wait`; a frame before that section fails the traced render and records the cause
in `render.log`. The ordinary flag-off path has no such tracing requirement. This records actual section frame bounds
and pre-encoding RGBA hashes; traced previews select exact decoded frame indices.
See [scene revision usage and limits](scene-revision.md) and the
[controlled experiment evidence](scene-revision-experiments.md). Ordinary rendering
remains the default, and machine checks do not establish semantic correctness.
