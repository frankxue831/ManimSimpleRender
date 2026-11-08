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
