# Two-case local evaluation

> Historical record. Original path: `docs/scene-revision-evaluation.md`.
> Findings, commands and validation counts describe the recorded revision and environment,
> not current acceptance. Historical command paths may refer to the original layout.
> See the [current guide](../../scene-revision.md) and [archive index](../README.md).

This evaluation used ordinary full Manim renders on base `ac2bf85`. No partial
rendering/cache, automatic feedback understanding, or user-comprehension result
is claimed. Each case was limited to two major revision rounds: one semantic
revision and one visual correction. Both stopped at that budget.

| Case | Explicit feedback target | Result |
| --- | --- | --- |
| Existing 168-second project introduction | `revision_paths`: make content edits vs inspection-point edits more visual | Parallel paths; six non-target scenes, 3540 decoded frames and relative frame timings exactly equal; machine checks passed |
| New 90-second PostgreSQL B-tree explanation | `selectivity`: show one match vs many matches and index-plus-heap work | Meaningful new animation, but regression did not pass: one frame before the declared target and all 720 frames of downstream `limits` differ in decoded pixels; budget exhausted |

The index example uses `SELECT note FROM orders WHERE k = 13` and an ordinary
B-tree index on `k`, then contrasts `k >= 5` (12 matching keys spread over four
illustrative heap pages). It does not equate row-access arrows with disk I/O,
benchmark tiny data, or promise every query/index is faster. Sources:
[PostgreSQL 18 B-tree](https://www.postgresql.org/docs/18/indexes-types.html),
[index usage](https://www.postgresql.org/docs/18/indexes-examine.html),
[index-only scans and heap access](https://www.postgresql.org/docs/18/indexes-index-only-scans.html).

Index non-target source and dependency identities remained unchanged. The media
differences are visually subtle, but exact frame comparison rejected them. Their
root cause was not fully established within the budget (shared text transforms
and lossy encoding are possibilities, not conclusions). The `limits` state
relationship was explicitly declared/acknowledged; that does not authorize a
silent pass. Of 2040 non-target frames, 1319 match and 721 differ. Manual decision
or a subsequent separately budgeted iteration is needed. No threshold was relaxed
and no source scope was silently expanded to obtain a pass.

Both cases used actual capture -> locate -> guarded apply -> full render ->
verify calls. Plans contain source ranges, key captions, object identities,
verified previews, feedback and explicit downstream acknowledgements. Maps were
initially too generic; they were corrected and new snapshots captured against
retained original-render bytes, preserving superseded snapshots. This metadata
adaptation was recorded separately from semantic rounds.

Both cases exercised guarded rollback to original source/timeline and replay of
the final edit; replayed source matched the rendered final source hash. Original
and candidate videos/receipts remain retained. Negative runs rejected unknown
IDs, stale baselines and an actual out-of-scope source edit. Unit tests additionally
cover preview tampering, dependency changes, marker injection, wrong video
geometry, relative frame timing, existing temp-file preservation, CRLF and
wrong rendered Scene classes.

Assistant steps remained necessary: choosing the ID from the feedback, writing
replacement Manim code, declaring shared-state dependencies, reviewing meaning,
and fixing layouts. Tool review prompted separate fixes for geometry/SAR,
relative frame timing, verified previews, exclusive temp files, CRLF preservation,
nominal-rate container rounding, and explicit dependency acknowledgement.

Observed verifier times in this local run were about 25.9 seconds for the intro
and 9.2 seconds until the index's first rejected interval. These are not complete
production times or efficiency comparisons. Overall assistant/model latency,
token counts, monetary costs and exact backend model identity were unavailable.
No fair no-tool timing baseline was established, so no saving is claimed.

A separate AI-agent review pass found no remaining mandatory factual/layout issue in the final
targets after the two corrections; it sampled both target intervals at 2fps plus
native key frames. This is not full playback or a test of user understanding.
The retained introduction's original "no product code changed" footer describes
that earlier production; this iteration adds an external revision helper while
leaving the ordinary render/check CLI unchanged.

Reproduction uses the commands in [scene-revision.md](../../scene-revision.md), with
retained local case manifests, `plan-final.json`, `repair-plan.json`, edits,
canonical run receipts, append-only version directories and `acceptance.json`.
Artifacts and videos are deliberately outside Git; the product commit contains
the reusable helper, tests and these documented limits only.
