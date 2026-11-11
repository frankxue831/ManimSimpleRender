# Silent review and revision example

[Documentation index](README.md) | [Editorial review](editorial-review.md) |
[Scene revision](scene-revision.md) | [Agent completion](scene-agent.md)

This executable source-checkout example connects two **real silent renders** to
an explicitly authored **fixture finding**, a single-section edit, a non-target
frame audit, and version-bound editorial records. It does not call a reviewer,
LLM, TTS service, private voice tooling, or external API. The final candidate
always has `decision: needs_review` and `readiness: not_determined`, even when
all machine checks pass. It is not a product-ready explanation.

## Run

Use a trusted checkout and its supported Python 3.12/3.13 render environment with
**Manim 0.22.1**, `ffmpeg`, and `ffprobe` on PATH. Follow the repository's normal
[installation instructions](../README.md#quick-start). The native frame recorder
uses Manim writer internals; an older installed Manim is not interchangeable.
An incompatible version fails before scene execution, with an install hint in
the retained `render.log`. Keep the failed attempt and use the supported
environment for a fresh run. An untraced render and decoded-only verification can
provide separate, weaker evidence but cannot count as this traced audit.
The fixture uses ordinary `Text`, two rectangles, and an arrow. It requires no
LaTeX, local fonts or media assets, narration, credentials, or network access.

From the checkout root, using the render environment's Python:

```sh
python -m examples.silent_review.run --output-dir /absolute/existing-parent/new-example
```

The output parent must already exist and `new-example` must not exist. Every run
copies the public fixtures into a fresh `project/` directory and renders fresh
`baseline/` and `candidate/` attempts. It never edits the checked-in fixtures or
reuses an earlier attempt. The two-second `ql` scene is intentionally fixed to
15 fps and 30 frames. Changing quality or the authored timing requires updating
the scene and timeline together.

Exit 0 means the demonstration ran and the non-target audit was unchanged,
**not that the video was approved**. Exit 2 means the completed machine audit
needs review. An exception stops the workflow and retains existing output for
investigation. There is no automatic retry or cleanup. A missing terminal or
uncertain process outcome must be investigated before starting any further work.

## What happens

1. Render the baseline through `scene_production.agent` with native frame
   recording. Wait for the actual subprocess exit and pass that observed return
   code and captured stdout to `scene_production.completion.read_completion`.
   The reader obtains the token from the actual `started` event and requires a
   matching successful terminal. No receipt polling or terminal-derived exit
   code is used. The example separately rechecks retained artifact hashes.
2. Capture a revision baseline and create `review-baseline.json`. Its reviewer,
   scope and finding explicitly say **fixture**, not independent review. The
   canned finding is that the displayed lookup result lacks a selected-row cue.
   Attached receipt, source, timeline, trace and PNG files are evidence bytes,
   not a claim that anyone watched the video or inspected those images.
3. Select the stable ID `lookup`, locate its source region, and apply the authored
   `candidate-body.txt`. The edit adds a yellow arrow pointing to the second
   row. The one-second `context` region is outside the edit scope. The manifest
   describes the planned cue and remains unchanged; no semantic mapping is
   inferred. Both sections are independent, and no later dependent section is
   changed.
4. Render the whole candidate through the same completion rule. Run the
   silent-only revision audit for unchanged source, dependencies, native timing,
   raw RGBA and every decoded frame outside `lookup`. Changed encoding or
   timing is not silently accepted. These layers do not assess the target's
   wording, layout, motion, teaching value or reader understanding.
5. Compare the baseline review against candidate video bytes. The intended
   visual change must make the old assessment non-current and set
   `candidate_requires_fresh_assessment: true`.
6. Create a new fixture `needs_review` record linked to the previous record and
   finding `F1`. The finding remains outstanding. `addresses: ["F1"]` records
   only the author's intended response, not resolution. Its byte-current
   comparison still reports `readiness: not_determined`.

## Retained outputs

- `example-result.json`: artifact paths and machine/review status summary.
- `baseline.stdout.jsonl` / `candidate.stdout.jsonl`: actual captured stdout.
- `*.stderr.log` and `*.completion.json`: captured stderr, observed process exit,
  interpreted status and captured invocation token.
- `baseline/` / `candidate/`: agent attempts, checked run receipts, MP4s,
  sampled PNGs, native frame traces, source snapshots and render diagnostics.
- `versions/`: immutable revision baseline copies.
- `plan.json`, `edits.json`, `applied.json` and `audit.json`: explicit scope,
  replacement body, applied change and separate machine comparisons.
- `review-baseline.json`, `review-candidate.json`, `comparison-previous.json`
  and `comparison-candidate.json`: fixture assessments and their byte-current
  comparisons. Earlier records remain intact.

The actual MP4 and PNG paths are available in each completed invocation's
terminal artifact map, and the MP4 paths also appear in `example-result.json`.
Review the full candidate's playback and relevant source before creating a real
assessment with the [editorial helper](editorial-review.md). Do not relabel the
fixture record as a real review. The editorial helper's records are unsigned
trusted-local data, not authenticated reviewer decisions; this example supplies
well-formed records and is not an adversarial validation or approval system.

## Focused headless checks

```sh
python -m unittest discover -s tests -p 'test_silent_review_example.py' -v
```

These tests exercise the actual completion reader on synthetic captured events,
fresh-directory refusal, retained-byte checks, scoped source mutation and
version-bound review records. Media rendering/decoding is mocked in these tests;
they cannot establish successful real rendering or visual quality. Run the
command above in a supported render environment for a real integration check.
