# Review the video you actually deliver

[Documentation index](README.md) | [Agent quick start](agent-quickstart.md) |
[Editorial review](editorial-review.md) | [Scene revision](scene-revision.md) |
[Silent review example](silent-review-example.md)

A rendered scene and a final assembled video are different artifacts. This small
source-checkout example overlays an original one-second `160x90` synthetic
moving clip onto the first second of the two-second `854x480` [silent review
fixture](silent-review-example.md), keeping the output duration at two seconds
(30 frames at 15 fps). No user footage, voice provider, keys, or external
download is involved. This is a concrete FFmpeg recipe, not a general
ManimSimpleRender (MSR) compositor.

## Run

Use the same trusted repository checkout and Python render environment as the
[silent example](silent-review-example.md) (`pip install -e ".[explainer]"` or
`".[gui]"` with **Manim 0.22.1**, plus `ffmpeg` and `ffprobe` with `libx264` on
`PATH`; this module runs local Python, Manim, and FFmpeg without a sandbox).
From the repository root, pass a non-existent `--output-dir` inside an existing
parent directory (existing output directories and files are rejected without
overwriting):

```sh
python -m examples.composed_review.run --output-dir /existing/parent/composed-example
```

The command runs the silent workflow to render the two-second baseline and
candidate scenes, generates a one-second `160x90` synthetic moving clip
(`testsrc2`), and spatially overlays that clip at `(674, 20)` across frames
0-14 (`lt(n,15)`) to assemble separate baseline and candidate composites. It
records exact input/output SHA-256 hashes, FFmpeg/ffprobe versions and `argv`
in `assembly.json`, writes `example-result.json` (and prints the same JSON to
stdout), and exits `0` when all fixture checks finish (invalid arguments exit
`2`; failed checks or subprocesses raise an error and exit `1` without writing
`example-result.json`). The evidence encoding is lossless H.264
(`-c:v libx264 -crf 0 -pix_fmt yuv420p`), not a size-optimized shipping preset.

## What is checked

- The [MSR raw-scene revision audit](scene-revision.md) runs on the
  uncomposited baseline and candidate renders first (`scene-workflow/`) and
  requires a passing `status: "unchanged_non_target"` (all non-target raw/decoded
  frames unchanged, recorded as `raw_scene_audit` in `example-result.json`;
  otherwise the run raises an error and stops); it stays separate from and does
  not verify the assembled composites.
- Both composites decode to 30 frames at 15 fps (`avg_frame_rate` `15/1`),
  `854x480`, without audio.
- Every decoded frame of the cropped `(674, 20)` `160x90` overlay region in the
  first second (frames 0-14) matches the synthetic moving source's FFmpeg
  `framemd5` `yuv420p` record (pixel MD5 and decoder-normalized timestamps),
  and the source contains at least two distinct frames.
- Baseline and candidate full-frame `framemd5` signatures match across the
  protected first second (frames 0-14, covering the silent fixture's unedited
  `context` interval plus the shared overlay) and differ across the edited
  second second (frames 15-29, covering the edited `lookup` interval). These
  checks apply only to this fixture's hardcoded overlay rectangle, 15-frame
  split and decoder-normalized `framemd5` timestamps; they do not inspect raw
  container packet timestamps or localize arbitrary spatial edits.
- Comparing the baseline composite's [editorial record](editorial-review.md)
  (`composite-review-baseline.json`) against the changed candidate composite
  bytes in memory marks that prior record stale (`assessment_current: false`,
  `candidate_requires_fresh_assessment: true`). A fresh placeholder fixture
  record (`composite-review-candidate.json`, `reviewer:
  example-fixture-not-an-independent-reviewer`) is bound to the candidate
  composite with `decision: needs_review`, and comparing it against the
  candidate composite reports `readiness: not_determined` (summarized in
  `example-result.json` as `baseline_review_current_for_candidate`,
  `candidate_review_current`, `candidate_decision` and `readiness`).

Raw-scene audits, `framemd5` checks and fixture records do not approve the
composite or establish visual clarity; watch `candidate-composite.mp4` with
independent playback before recording an actual review via [editorial
review](editorial-review.md).

## Retained outputs

- `example-result.json`: completion summary with composite paths, raw audit
  status, protected frame count and editorial comparison state (also printed to
  stdout; `candidate_requires_fresh_assessment` is verified in memory rather than
  copied into this summary).
- `assembly.json`: `ffmpeg`/`ffprobe` version lines, synthetic insert hash and
  generation command (`recipes.synthetic_motion_argv`), and exact `argv` plus
  input/output SHA-256 hashes for the baseline and candidate assembly steps.
- `baseline-composite.mp4` and `candidate-composite.mp4`: two-second assembled
  H.264 composites (`candidate-composite.mp4` is the final candidate to watch).
- `synthetic-motion.mp4`: one-second `160x90` synthetic moving source clip.
- `composite-review-baseline.json` and `composite-review-candidate.json`:
  fixture `needs_review` editorial records bound to the two composite MP4s (no
  standalone comparison JSON files are written at the top level).
- `scene-workflow/`: nested [silent review workflow](silent-review-example.md)
  directory containing the uncomposited renders, raw revision baseline/audit
  and raw-scene fixture reviews.
- `*.framemd5` and `*.log`: decoded frame signature tables and per-command
  FFmpeg logs retained for inspection.

## Reproduction and limits

Run this module again into a **new directory** to rebuild the recipe from
source. `assembly.json` records provenance, not a portable replay command: its
`argv` entries use absolute paths and `-n` exclusive outputs, so they cannot be
re-executed as-is. Output bytes can differ across machines, fonts and FFmpeg
versions; retain inputs and tool versions if exact replay matters.
User-supplied footage, custom assembly workflows, editable project formats and
arbitrary clip layouts are out of scope.

Any failed subprocess or check raises an error without retry and leaves
available logs and evidence in `--output-dir`. The run completes only when
`example-result.json` is written; treat a partial output directory as
incomplete.

## Focused headless checks

```sh
python -m unittest discover -s tests -p 'test_composed_review_example.py' -v
```

These unit tests run in a plain Python environment without Manim or FFmpeg by
mocking subprocesses and media to check non-reuse of existing output directories,
single-attempt failure logging, input-mutation rejection, stream/frame-count
probe guards, `needs_review` fixture labeling, exclusive JSON record creation
and frozen or mismatched insert rejection; they do not replace a real-media run
or playback review.
