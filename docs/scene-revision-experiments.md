# Scene revision experiment evidence

This is local evidence from a bounded exploration, not a claim of automatic video
authoring, semantic verification or measured workflow efficiency. The earlier
closed evaluation (one passing case, one failing index case) remains unchanged.
The new controlled baselines below do not retroactively pass that failure.

## Three experiment rounds

| Round | Question and controls | Observed result |
| --- | --- | --- |
| 1 | Are index differences only encoding noise? Repeat original/revised renders; observe pre-encode RGBA without replacing frames; factorial clock/state interventions. | Repeats were exact. At 78 s, raw output differed in 196 pixels, maximum channel delta 14/255. Native boundaries were also one frame early. Only clock correction plus fresh canonical downstream Text made all non-target raw and decoded frames exact. |
| 2 | Can a scoped index revision preserve actual non-target output? New baseline and candidate use the same explicit clock/state isolation. | Authored selectivity edit applied through locate/apply. Four non-target sections, 2040 frames, matched raw and decoded output; native bounds matched check configuration. Guarded rollback/replay restored exact source bytes. |
| 3 | Does this generalize to longer captions, duration changes, shared state and bad content? | A 17 s flow became 19 s. Tail moved 360..510 to 420..570 with the same 150 frames. An absolute film-end deadline first shortened the tail and failed duration inspection. Red shared-dot state was detected downstream. A false target subtitle passed scope checks but independent semantic review rejected it. |

The root control measured different Text histories (7892 vs 8128 points) despite
equal bounding boxes. An independent no-encoding reproduction isolated the same
196-pixel difference; constructing fresh canonical Text removed it. Integer-frame
frozen pauses remove the separate truncation issue in Manim's static wait, whose
`int(duration / dt)` can truncate one frame after floating-point subtraction.
This is an explicit frozen pause policy, not a general animation retimer.

The observation hook forwarded the original frame and repeat count. Independent
partial-video byte checks (43 original and 51 revised fragments) were identical
between normal and observed runs. This supports the control's non-perturbation
for those samples; it does not certify every renderer/version.

## Cross-case and negative evidence

| Case | Machine result | Independent review / limit |
| --- | --- | --- |
| Existing 168 s project-introduction example, revision_paths target | Six non-target sections, 3540 frames, raw/decoded/native timing unchanged. | Authored content makes render versus reinspection concrete; no feedback interpretation model was added. |
| Index selectivity revision | Four non-target sections, 2040 frames unchanged. | Few/many-hit explanation sampled as consistent with PostgreSQL B-tree ordinary index scan; no universal speed claim. |
| Separate index btree repair | Four non-target sections, 2100 frames unchanged. | Review found an old return-arrow ambiguity shared by both versions. Explicitly scoped repair points inside 13 and keeps a green outline; sampled 31-40 s repair resolves confusion with 8. |
| Flow target duration +2 s | Setup 150 frames and tail 150 frames unchanged at mapped native windows. | Timeline remains separately authored check configuration. Full render still required. |
| Deliberate red-dot state leak | Non-target tail reports render_output_changed and needs_review. | Tail says blue but dot is red: semantic/visual rejection agrees with machine evidence. |
| Deliberately false target teaching | Non-target machine evidence unchanged. | Review rejects “changing check times requires redrawing all animation.” Target correctness is outside regression equality. |
| Real post-encode compression control | All 570 raw frames exact; non-target decoded output differs; needs_review. | Explicit logged custom-runner postprocess; no fabricated receipts. Classification does not auto-accept compression. |
| Container metadata only | New MP4 hash, unchanged raw and decoded frame sequences; unchanged_non_target. | Hashes bind identity; unequal file bytes alone do not imply changed frames. |
| Helper changed after baseline render but before capture | Dependency hash unchanged since capture, but non-target raw/decoded changes require review. | Captured helper identity is version-local; historical execution dependencies are not proven. |
| Deliberate post-encode color inversion | All 570 raw frames exact; non-target decoded output differs; needs_review. | Same classification can contain harmful postprocessing. Raw equality alone cannot accept a deliverable. |

Source/metadata guards reject wrong targets, edits outside scope, stale baselines,
changed dependencies and wrong Scene identities. New tests also cover source
mutation during decoding, self-consistent trace/receipt changes during decoding,
concurrent edits before source replacement, source marker order, marker-looking
caption strings, malformed manifest types and native-preview metadata.

Independent review found and verified fixes for three important guard issues:
comparison identities could be mixed after a concurrent receipt update; apply
could overwrite a concurrent non-target edit before replacement; marker order
was not enforced. These are ordinary local stale-input guards, not OS atomic CAS
or a concurrent-editor transaction. Restore is also not crash-atomic across files.

## Frame clocks and preview boundaries

- Independent arithmetic checks covered 479760 deadline cases across 1..240 fps.
- Twenty-four real Cairo renders (two repeats at 1,2,7,15,24,25,30,48,50,60,120,240 fps;
  320 x 180 probes) passed actual native counts, explicit fractional-second sample
  indices, repeated raw hashes and MP4 byte identity.
- A real 24 fps,38 frame probe with a 7 frame final section now extracts indices
  4/28/32/37, including the last frame. Exact 31/24 s selects frame 31; independent
  PNG RGB comparison matched a separate exact-index extraction byte-for-byte.
- Only JSON float representation error within 8 ULPs of a native frame coordinate
  is snapped; .001 frame and 1 frame errors reject. Pixel comparison remains exact.
- Traced preview metadata distinguishes request time, native index and grid time;
  grid time is not an observed encoded packet PTS. Legacy untraced seek behavior
  is retained and can still fail on a very short final segment.

## Reproduction and costs

The portable authored example is executable without changing repository files:

```bash
python -m examples.revisions.run_flow --output /tmp/new-revision-flow
```

It renders both versions, locates/applies a supplied edit, audits, actually restores
and replays source/timeline and checks exact replay. No model/provider is called.
The assistant supplied the brief, target, edits, timing/configuration and semantic
judgment. Independent agents reviewed facts, visuals and code. These human/agent
steps were not replaced by the tool.

Eight serial 19 sScene pipeline measurements produced the same MP4 SHA with or
without tracing. First three overlapped another render and are excluded from
fair timing comparison. The final balanced four-run subset had median full
render/check times 2.479 s untraced and 2.743 s traced on this Mac; trace size 38537 bytes.
This includes different preview extraction modes, two observations per mode and
uncontrolled background load. It is not isolated hash overhead or a human-workflow
baseline. No efficiency or cost savings are established.

Exact serving model identifier, token counts and monetary costs were unavailable
and remain unknown. No new paid provider, credentials or software were installed.
Current local environment: macOS arm 64, Python 3.13.0, Manim 0.20.1, FFmpeg 9.0.2.
Windows/Linux, Python 3.12 and other Manim versions were not executed locally.
Cairo single-writer/full-render is the tested path; OpenGL is not certified.

The comparison film was fully decoded and reviewed through 206 one-second samples
plus original-size key frames. This is temporal sampling, not a claim of full
real-time human playback. Viewer understanding has not been measured.

## Final local verification

At the implementation/example commits:

- Full headless suite after the caption guard: 913 tests run, OK (2 skipped).
- Separate existing Python 3.13 without Manim: 57 targeted core/architecture/example tests passed.
- Black checked 134 files; flake8 and `git diff --check` passed.
- Wheel built with `pip wheel --no-deps --no-build-isolation`; running its CLI from
  outside the checkout rendered the real 24 fps probe and selected exact frame 31.
  The `build --no-isolation` frontend reported an unavailable `wheel` dependency;
  the existing pip/backend route succeeded without installing packages.
- Independent clean-room `git archive` reproduction pinned to commit `196c651`
  asserted imports came from archived source, rendered both example versions,
  audited and actually rolled back/replayed files. Its full suite also passed
  912 tests, OK (2 skipped). With current authoring source made syntactically invalid,
  real CLI reinspection still succeeded with the retained video/trace identities.
  One-byte video corruption was rejected before media-tool calls or output creation.
  Stale apply, unknown target, out-of-scope edit and guarded-restore negatives also
  rejected; all deliberately changed bytes were restored.

No remote CI was triggered: this work remained local. The archived reproduction
uses the same Mac and installed dependencies, not a second platform/environment.

## Index teaching sources

The index example limits itself to PostgreSQL 18 B-tree equality/range behavior
([Index Types](https://www.postgresql.org/docs/18/indexes-types.html)). The ordinary
index-scan example reads `note` outside the index and therefore visits the heap
([Index-Only Scans](https://www.postgresql.org/docs/18/indexes-index-only-scans.html)).
Plan choice depends on estimated costs and the data; real plans should be inspected
with [EXPLAIN](https://www.postgresql.org/docs/18/using-explain.html). The authored
16-row/page diagram is a teaching illustration, not a measured I/O or timing result.

## Edited-caption glyph boundary

A late actual probe supplied a new caption containing unassigned U+10FFFF. The
old example chose a font from a manually maintained character whitelist: rendering
and media inspection succeeded, but the PNG visibly contained a white square.
The flow example now calls `scene_font(value)` for each actual caption before
constructing Text, so new edit text cannot bypass that coverage check. The real
negative now fails with the missing-coverage error; the valid 17/19-second example
still renders, audits and rolls back/replays. A headless regression failed against
the old example and passes with this change; independent real-Manim smoke agreed.

This guard is local to the authored example's label function, not automatic glyph
or OCR validation for arbitrary trusted Scenes. Per-caption lookup overhead and
possible family differences are not benchmarked; visual consistency still needs
review and the actual-output regression check.

The final comparison is 206 seconds after a readability revision: shorter headers,
an 18-second evidence hold, a 16-second limits hold and a clearer 10-second ending.
Nine unchanged assembled shots (4080 frames) were also compared at mapped frame
windows. This private FFmpeg-assembly check reuses decoded comparison only, with
actual chunk counts probed; it is not a Scene source-scope audit or a new editor.
