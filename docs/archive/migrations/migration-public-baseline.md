# Selected migration onto the public baseline

> Historical record. Original path: `docs/migration-public-baseline.md`.
> Findings, commands and validation counts describe the recorded revision and environment,
> not current acceptance. Historical command paths may refer to the original layout.
> See the [current guide](../../README.md) and [archive index](../README.md).

Base: public `80891ab5969e90ba3b19c4dbaac36f0b04650219` (including PRs #21-23).
Reference: the isolated `agent-visual-repair-lab/source` candidate descended from
the old `72c3c9536d5331238413096bf9ae200eb63c9627` history, through fixes 26 and 27.
The two Git histories are unrelated. This is a selective code migration, not a
merge, rebase, or replacement of the public source tree.

## Included

| Old reference | Public adaptation | Decision and contract |
| --- | --- | --- |
| `app/render/subtitle_draft.py`, `caption_events.py` | Same Qt-free modules; `scene_production/captions.py` | Strict explicit integer-frame annotations, external SRT, coverage, and a separate sidecar bound to a successful public render. No second renderer or implicit storyboard rewrite. |
| `caption_edit_review.py` | `captions.verify_text_revision` | A narrower check: exactly one text edit, unchanged event structure/timing, and the identical original receipt/video bytes. Cross-encoding full-video equivalence was not transplanted. |
| `voice_bridge.py`, selected `voice_tools/*` policies | `scene_production/audio.py` | Explicit existing PCM WAV per cue, validation, frozen inputs and separate preview. No TTS or automatic file discovery. An audio preview is not eligible for the existing silent revision contract. |
| `staged_caption.py` protocol/context ideas | `scene_production/agent.py` | Wrap existing synchronous render/reinspect APIs with token-bound JSONL and durable attempt files. Terminal outcome follows API return/exception and artifact checks, never polling a legacy intermediate `failed` receipt. |
| `visual_frames.py`, safety fixes 26/27 | `scene_production/visual_frames.py` | Bounded exact-index samples, RGB24 equality, caption JSON Pointers and retained source identity. No claim of causal source-line/object navigation. |
| Fix 26 image2 path policy | Existing production `_inspect_video` | Pass a fixed basename and explicit output `cwd`; user-selected ancestor paths never enter the image2 output pattern. Both traced and untraced inspection use this path. |

The source primitives were adapted rather than copied wholesale. New code uses
the public receipt, source/timeline/video hashes and existing tool/architecture
boundaries. The archived local handoff records exact file hashes and regression
results for this migration; repository documentation does not imply publication.

## Deliberately not migrated

> This list describes the scope at base `80891ab`. The later
> [local handoff layer](../../agent-handoff.md) added leases and task coordination
> separately. Consult the [current index](../../README.md) for today's capabilities.

- The old `staged_caption.run/verify` production chain and
  `external_caption_runtime` launch path. They duplicate public scene production
  and use incompatible saved-project/token assumptions.
- Old GUI preview/controller/worker changes. Qt-free services are available
  first; GUI integration needs its own interaction and cancellation tests.
- The full old caption-edit checker, raw diagnostic excerpts and invocation
  scripts. The new narrow text-only contract avoids pretending to prove visual
  equivalence between separately rendered videos. Agent diagnostics keep paths,
  structured failure categories and the public fixed font hints; they do not
  serialize environment secrets or arbitrary exception text to JSONL.
- Old code replacing public `explainer`, portable handoff, isolated launcher,
  font diagnostics, scene revision, frame tracing or motion codegen. Existing
  implementations and their regression tests remain the baseline.
- Automatic speech synthesis, subtitle burn-in, storyboard synchronization,
  fact/visual/audience approval, an approval database, queue, leases, webhooks,
  MCP server or automatic retry. Those require separate designs and acceptance
  criteria; a token/hash does not grant execution or publication permission.

## Integration boundaries

Existing `python -m scene_production`, `scene-production`, reinspection,
portable bundles and revision commands retain their public interfaces.
Caption/audio/frame helpers are opt-in modules and create additional artifacts
outside retained runs. `scene_production.agent` is also opt-in and reserves a
new attempt directory. See each helper document for the distinction between an
output parent (new child created) and an exact new output directory.

The old receipt status behavior remains for compatibility: `failed` may be
written before checks finish. Consumers using the original CLI must wait for its
exit result. The JSONL wrapper supplies a stronger attempt-level terminal rule;
it does not retroactively make arbitrary old receipts reliable process state.
Missing terminal events, stale running attempts and interrupted transport must
be reconciled, not automatically retried.

## Safety and validation scope

All helpers assume trusted local files, installed tools and no concurrent edits.
Hashes detect byte changes but are not signatures; this is not a sandbox.
New output directories and exclusive files prevent accidental normal-workflow
replacement. Partial outputs are retained on failure and are not success.

Tests cover schema rejection, stale/tampered identity, cue timing, WAV format and
duration, video preservation, protocol termination and path-expansion sentinels.
Real local FFmpeg/FFprobe and a full production smoke supplement mocks. The
baseline suite, formatting and architecture guards must pass before delivery.
Local evidence does not substitute for Linux/Windows CI, a clean dependency
installation, actual listening, visual/teaching assessment or hostile-filesystem
testing. No dependency or package-entry-point change is required by this migration.
