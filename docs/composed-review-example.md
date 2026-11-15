# Review the video you actually deliver

A rendered scene and a final assembled video are different artifacts. This small
source-checkout example adds an original synthetic moving insert to the existing
silent review fixture. No user footage, voice provider, keys, or external download
is involved. This is a concrete FFmpeg recipe, not a general MSR compositor.

## Run

Use the same trusted environment as the [silent example](silent-review-example.md):
Manim 0.22.1, FFmpeg/ffprobe with libx264, and the repository dependencies.
From the repository root, choose a new directory whose parent already exists:

```sh
python -m examples.composed_review.run --output-dir /existing/parent/composed-example
```

The command renders the two-second baseline and corrected scene, creates a
one-second synthetic moving clip, then assembles separate baseline and candidate
videos. It records exact input/output hashes, FFmpeg versions and argv in
`assembly.json`. The evidence encoding is lossless H.264, not a size-optimized
shipping preset. Do not overwrite previous attempts.

## What is checked

- Existing MSR raw-scene revision audit remains separate.
- Both composites decode to 30 frames at nominal 15 fps, 854x480, without audio.
- Every decoded first-second inserted crop matches the synthetic moving source,
  including normalized decoded timestamps; the source contains distinct frames.
- Baseline and candidate first-second full-frame signatures match; their edited
  last-second signatures differ. This is this fixture's scope, not a general
  spatial revision audit or certification of original container timestamps.
- The baseline editorial record becomes stale for the candidate. A fresh fixture
  record is bound to the candidate, but remains `needs_review`, with readiness
  `not_determined`. Nothing is automatically marked visually approved or repaired.

The source fixture, synthetic input, raw renders, final composites and their
reviews must not be confused. Use independent playback to assess the actual
final video. Machine equality does not establish a clear explanation.

## Reproduction and limits

Run this module again into a **new directory** to rebuild the recipe from source.
`assembly.json` is provenance, not a standalone replay command: its argv uses
absolute paths and exclusive outputs. It cannot simply be executed in another
folder. Cross-machine/font/FFmpeg-version byte identity is not promised. Retain
inputs and tool versions if exact replay matters. Existing user-video assembly
recipes, editable project formats and arbitrary clip layouts are out of scope.

Failure stops the example without retry and leaves available logs/evidence in
the output directory. Only a completed `example-result.json` reports completion;
a partial directory is not success. Trusted local media/code only, not a sandbox.
