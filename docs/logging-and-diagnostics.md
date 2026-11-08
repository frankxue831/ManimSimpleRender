# Render logging and diagnostics

This inventory and audit covers the GUI, public scene production/reinspection,
agent protocol, local handoff runner and explainer render adapter. The starting
revision was `c449fd2`. No historical EP3 log files were available in this
checkout audit; timing guidance below describes what new runs can establish.

## What is recorded

| Entry point | Existing evidence | Added diagnostic evidence |
| --- | --- | --- |
| GUI | Per-batch `render-*.log` under the selected output directory; command and merged Manim output; in-memory scene results and capped log view | UTC time on each received line; batch ID; job start/end, exit code, outcome, monotonic duration; explicit skipped-job records; persistence warnings; complete disk-log export |
| `scene-production` / `scene_production.render` | Exclusive `scene-*/` run; `render.log`, frozen source/timeline, `result.json`, video/frame hashes, optional frame trace | Private `diagnostics.jsonl` with prepare/render/provenance/artifact/probe/decode/frames/integrity/publication phases; render outcome/exit code; distinct deadline, cancellation and tool-timeout reasons |
| Public reinspection | New run and receipt referencing the original video; copied inputs/media and newly checked frames | Private phase timeline covering copy, inspection and receipt publication |
| `scene_production.agent` | `attempt.json`, `events.jsonl`, private `api.log`; `run_token`; verified artifacts; exactly `started` and `terminal` on stdout after a normal claim | UTC time, monotonic elapsed time and wrapper phase on existing events; private `diagnostics.jsonl` for initial persistence, started delivery, API execution and retained-byte verification; production run linkage |
| `scene_production.handoff` | SQLite task/attempt/event state with IDs, claim fences, ready/claimed/terminal/ACK times; `process.json`, `exit.json`, bounded `protocol.log` and `stderr.log`; nested agent attempt | Private runner phase timeline and explicit unknown phase/cause; sanitized agent error/stage retained for verified failed attempts |
| `explainer render` | Callback/console output; temporary generated source/media kept for known render failures, otherwise cleaned | Private `.explainer-logs/run-*/render.log` and `diagnostics.jsonl` beside the requested output; retained on success and unexpected failure; location announced to callback or stderr |

Caption, audio, visual-frame and revision helpers retain their existing
manifests/receipts and artifact relationships. They have not been given a new
phase timeline in this change. `concept_render` is also unchanged. The shared
subprocess core still exposes diagnostics through its callback/result; it does
not silently create files for every caller.

## Finding a problem

For the GUI, use **Save Log** after a run. It copies the complete persisted log
for the latest batch, including lines no longer in the 5000-block view. During
a run it exports a byte-length snapshot; later lines are not part of that copy.
If persistence failed, the GUI labels the export as partial, or explicitly falls
back to the capped view. Startup failures clear the previous batch's log link.
The first worker output announces the on-disk file location. Output is shown as
literal text and sparse output is flushed approximately every 200 ms.

For scene production, start with `result.json` **after the synchronous API has
returned or raised**, then read the adjacent `diagnostics.jsonl` and `render.log`.
Failed receipts now include `exception_type` and a fixed `failure_reason`:
`render_timeout`, `cancelled`, `executable_not_found`, `render_failed`,
`tool_timeout`, or `exception`. `stage` identifies the observed failure stage;
the raw `error` field and raw log remain private local diagnostics. A late
deadline callback cannot relabel an already observed return as a timeout.

For an agent attempt, retain the `run_token`, process exit and both stdout
events. The terminal `phase` distinguishes argument/claim/persistence/transport,
API operation and artifact verification failures. `diagnostics.stage`, when
available, narrows an API failure to a production stage, and
`diagnostics.failure_reason` contains only recognized fixed reasons. Follow
`diagnostics.timeline` to the wrapper timeline; a successful verification also
records the nested `scene-*` run ID there. `timeline_write_failed` indicates that
this optional sidecar could not be fully written. The sidecar ends before final
protocol persistence/delivery, so its `operation_outcome` is not the terminal
protocol result.

Early API validation and artifact-verification exceptions also retain their
tracebacks in private `api.log`, including cases where no production receipt
could be created. Exception messages stay out of stdout and the timing sidecar.

For handoff, use the existing local CLI queries:

```sh
python -m scene_production.handoff --workflow /absolute/workflow attempt --id ATTEMPT
python -m scene_production.handoff --workflow /absolute/workflow handoff --id TASK
python -m scene_production.handoff --workflow /absolute/workflow events --owner OWNER
```

Follow the recorded attempt directory to its runner `diagnostics.jsonl`,
`process.json`, `exit.json`, `protocol.log`, `stderr.log`, then the nested `agent/`
directory. Use `diagnostic_phase` and `diagnostic_code` to distinguish launch,
waiting, draining, protocol consistency, retained-byte verification and
persistence problems. A diagnostic reason is not authorization or proof that
all descendants stopped. Keep following the recovery rules in
[agent-handoff.md](agent-handoff.md).

A verified failed agent attempt also exposes `agent_error_code`, `agent_stage`
and `agent_failure_reason` when their values belong to the supported allowlists.

For explainer, retain the announced diagnostics directory. `render.log` includes
received output, supervision diagnostics and unexpected exceptions; the phase
timeline identifies preparation, rendering, output discovery and publication.
Early storyboard/citation/codegen/missing-Manim preflight failures preserve the
existing no-output behavior and do not create a diagnostics directory. Public
scene production likewise validates inputs before creating its run directory;
use the agent wrapper when those early failures need an attempt record.

To inspect a JSONL timeline without additional packages:

```sh
python - /absolute/run/diagnostics.jsonl <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    for line in stream:
        row = json.loads(line)
        print(row["timestamp"], row["event"], row.get("phase", ""),
              row.get("duration_seconds", ""), row.get("exception_type", ""))
PY
```

A truncated final line can occur after a crash or disk failure. Preserve the
file and inspect its complete preceding records; do not fabricate a finish time.

## Interpreting timing, including an EP3-style retrospective

JSONL records use `schema_version: 1`, a per-file `sequence`, UTC `timestamp`,
and process-local monotonic `elapsed_seconds`. Phase end/failure records carry
`duration_seconds`. GUI lifecycle records use `elapsed_ms`/`duration_ms` inside
timestamped text records instead. Raw output timestamps mean **received by the
application**, not generated inside Manim; child buffering can still delay them.
`phase_finished` means that scope returned normally, not that the video passed
semantic review.

| Question | Evidence and limits |
| --- | --- |
| How long did input preparation take? | Recorded `prepare` scope. Validation before public run creation is outside that scope; agent `operation` includes it. |
| How long did the rendering subprocess take? | Production `render` scope, including supervision/cleanup. GUI job duration also includes command preparation and logging; it is not a CPU-utilization measure. |
| How long did media checking take? | Separate probe, decode, frames and integrity scopes. This is machine inspection, not a person's visual QA time. |
| How long did eligible work wait to be claimed? | Ledger `ready` and matching `claimed` event times. Readiness can be revoked/recreated; use the applicable generation and task history. ACK time is receipt, not work start. |
| What happened during handoff? | Runner/agent phase timelines plus task, attempt, token, process and ledger event IDs. Agent waiting includes all child preparation/render/checking, not just rendering. |
| How much was active review, waiting for a person, or rework? | Manual task claims/completions bound an attempt but do not measure active attention. Record separate authorized manual tasks for draft/review/revision; this implementation cannot infer unrecorded work or causation. |

Do not sum nested wrapper and production durations: those scopes overlap. For
parallel tasks, compute the union of intervals for elapsed coverage rather than
summing their durations. UTC helps correlate processes but can jump with clock
adjustments; monotonic clocks measure durations only within one process. A gap
between records is unobserved time, not automatically idle time. File mtime,
the timestamp of a UI batch and a receipt's provisional status are not substitutes
for task boundaries. These records do not retroactively fill holes in EP3.

## Compatibility, privacy and remaining limits

The agent stdout protocol remains version 1 with the same event count. Timing
and phase fields are additive. Handoff still requires matching stdout, durable
state/journal, process exit and artifact evidence before accepting success.
The public receipt's provisional `status: failed` during inspection is unchanged.
Neither diagnostic JSONL nor a receipt poll establishes termination. Unknown
attempts keep their conservative fencing/recovery semantics and are never
automatically retried.

New diagnostic files and raw logs are created with mode `0600` on POSIX;
exclusive run/attempt directories remain private. Existing raw logs, receipts,
scene code and workflow databases may contain paths, source text, arbitrary
child output, exception details and original authorization. They must not be
treated as safe-to-share bundles. Structured phase writers receive fixed labels,
numeric facts and correlation identifiers, never environment values, raw argv,
authorization text or arbitrary log excerpts. Identifiers themselves may still
be sensitive. The generic helper is not a redactor for arbitrary caller input.

Sidecar writes flush on each event but do not fsync, certify crash durability,
rotate files, impose retention, or install a background service. Optional
timeline failures and GUI/explainer raw-log failures warn or are exposed in
diagnostics without changing a successful media operation into failure.
Production raw-log and agent capture failures retain their existing error
behavior; mandatory agent/ledger state persistence failures still produce
unknown. A blocked filesystem can still delay logging and lease
renewal. Missing phase finishes after a crash remain incomplete evidence.

Handoff checks `diagnostics_incomplete` before committing the ledger result.
A sidecar failure during the final event or close only warns and cannot rewrite
that committed result. Absence of the flag does not certify a complete timeline.

Tests include fake media tools, actual subprocess failure paths and minimal
real Manim/ffmpeg renders. They do not certify semantic video quality, real
human review, power loss, Windows ACL behavior, remote delivery, reboot recovery
or disk-stall bounds. The real logging smoke uses no TTS or LaTeX.

## Verification of this change

The initial lightweight run on November 8, 2026 used Python 3.13 on macOS with
Qt offscreen and ran 1184 tests:
1165 passed and 19 were skipped for unavailable real Manim/TTS or platform
requirements. Black and Flake8 passed for the same source/example paths checked
by CI; `git diff --check` passed. The citation-gate example was updated to its
new source line range and its `explainer verify` command passed.

Regression coverage includes sparse GUI output, full-log export and same-file
protection, disk write failures, forced QThread termination without a lingering
flusher, late timer callbacks, private early-failure tracebacks, unchanged
two-event agent stdout, handoff protocol inconsistency, and sanitized failure
reason propagation. The complete test suite also exercises the existing
architecture, portable output, revision and conservative recovery contracts.

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m black --check app explainer scene_production tests main.py
.venv/bin/python -m flake8 app explainer scene_production tests main.py
git diff --check
```

The real-render follow-up used Manim 0.20.1 and ffmpeg/ffprobe 9.0.2 in this
worktree's isolated environment. The existing real-render smoke passed. New
`tests/e2e/test_real_logging_smoke.py` runs Agent and handoff success/failure
through actual CLI subprocesses, checking video/frame hashes, phase records,
private-output isolation and exact stdout/state/journal agreement. It gates on
Manim, ffmpeg and ffprobe so the lightweight CI still skips it, while the
existing nightly e2e discovery includes it automatically.

Additional retained local smoke evidence checked a real scene timeout after
entering `construct`, a successful recorded-video reinspection without a new
render, and success/failure through the GUI worker with real subprocesses. The
worker was driven synchronously for this evidence; QThread scheduling and forced
termination are covered separately by isolated regression tests.

Two independent second-pass code reviews covered sensitive data, exception
semantics, optional versus mandatory persistence, thread shutdown and export
behavior. A known-incomplete log exported to itself or a hardlink previously
lost its warning in the GUI status; this was fixed and regression-tested. No
remaining blocking findings were identified within that review scope.

After this fix and the real smoke additions, the full suite ran **1187 tests:
1176 passed, 11 skipped**, in 36.126 seconds. Skips were nine tests requiring
the uninstalled voiceover package, one real Windows process test and one
environment-dependent Qt scrollbar check. Black, Flake8 and `git diff --check`
passed. No real TTS, OpenGL render, full interactive GUI acceptance or human
semantic review is claimed.

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest \
  tests.e2e.test_real_render_smoke tests.e2e.test_real_logging_smoke -v
```
