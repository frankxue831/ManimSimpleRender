# Scene production agent protocol

[Documentation index](README.md) | [Agent quick start](agent-quickstart.md)

`python -m scene_production.agent describe|render|inspect` is an opt-in,
Qt-free JSONL interface over the existing synchronous `render` and `reinspect`
APIs. It does not replace `scene-production` or `python -m scene_production`.
It does not introduce another renderer, a database, a queue, automatic retries,
a remote service, or an authorization layer.

Run only trusted scene code and tools. Rendering executes Python with the
caller's privileges and inherited environment. Inspection is machine decoding
and sampled-frame extraction, not visual or semantic approval.

## Start here

From a checkout using its Python environment:

```sh
python -m scene_production.agent describe
python -m scene_production.agent render \
  --scene-file /absolute/project/scene.py --scene Demo \
  --timeline /absolute/project/timeline.json \
  --output-dir /absolute/existing-parent/new-attempt \
  --quality qm --timeout 120 --tool-timeout 30
python -m scene_production.agent inspect \
  --from-run /absolute/render-attempt/production/scene-IDENTIFIER/result.json \
  --timeline /absolute/project/corrected-timeline.json \
  --output-dir /absolute/existing-parent/new-inspection-attempt
```

`--record-scene-frames` is also supported by `render`. Quality is one of
`ql`, `qm`, `qh`, `qp`, `qk`. The public API validates timelines and timeouts.
`inspect` applies a corrected timeline to a recorded video; source edits require
a new render. It never calls Manim. Use the original render's `result.json`, not
`attempt.json` and not another inspection's receipt.

**Unlike the original CLI's output root, this interface's `--output-dir` must
not exist. Its parent must exist.** The adapter claims it with an exclusive
mkdir and never reuses or removes it. An existing directory, file or symlink
is rejected without reading or associating its logs with this attempt. Every
invocation gets a fresh 32-hex-character `run_token`, including usage failures.

`describe` returns one terminal event containing a `description`. Each command
has an `invocation` mapping suitable for `subprocess.run(**invocation)` after
replacing placeholders:

```json
{
  "args": ["/path/to/python", "-m", "scene_production.agent", "describe"],
  "cwd": "/path/to/checkout-or-installed-package-root",
  "env": null,
  "shell": false
}
```

These are argv elements, not a shell command. `cwd` and the interpreter come
from the running installation; use absolute input paths when launching from a
different directory. `env: null` means inherit the environment, not an empty
one. Environment values are never serialized. `describe` is the machine-readable
help; `--help`, missing arguments and other parse errors produce JSONL terminal
usage errors rather than argparse text.

## Events and completion

Every event contains `protocol_version: 1`, `event`, `run_token`, `operation`
and `retry_safe: false`. A claimed render/inspect normally emits `started` with
`status: running`, then exactly one `terminal`. Usage and pre-claim failures
have only a terminal. Description is a terminal success without an attempt.

Terminal states and process exit codes:

- `succeeded`, exit 0: the synchronous API returned, its success receipt matched
  the return value, and retained source/timeline/video/frames and optional frame
  trace were hash-checked inside this attempt's run directory.
- `failed`, exit 1: a synchronous operation or verification failed. Partial
  artifacts may exist. This is not permission or a recommendation to retry.
- `failed`, exit 2: CLI parsing failed. The raw offending argument is not echoed.
- `unknown`, exit 3: persistence or transport failed. Files and media may exist.
- `unknown`, exit 130 or 143: SIGINT/KeyboardInterrupt or SIGTERM was observed.
  `process_cleanup_verified: false` is explicit. The API may have cleaned up a
  child, but the adapter does not certify that all descendants have stopped.

To accept completion, require **process exit 0, exactly one terminal with
`status: succeeded`, and the expected token saved from that invocation**. Keep
that token outside the attempt directory if it is needed for later correlation.
For render/inspect success, `artifacts` identifies the checked receipt, source,
timeline, video, sampled frames and optional trace by absolute path and SHA-256.
`semantic_acceptance: false` and `verification_scope: retained_artifact_bytes`
remain explicit. This is not evidence that helpers, imports or environment were
snapshotted, or that current source edits were applied by reinspection.

**Never poll `result.json.status` to decide that an attempt finished.** The
public render API writes `status: failed` with `completed_artifact_version: 1`
after the video is complete but before inspection has finished. Reinspection
also begins by writing a failed receipt. Both can later succeed. Only synchronous
API completion or exception determines the adapter's operation outcome.

No terminal, a truncated JSONL line, a broken connection, an unexpected process
exit, conflicting files, or an old `running` state means **unknown**, not a
failed job to restart. SIGKILL, process crashes and power loss cannot reliably
produce a terminal. Establish process ownership, stop or await remaining work,
and inspect artifacts before deciding on a new attempt. The adapter never does
this automatically. Tokens and hashes are integrity/correlation aids; they are
not authentication, consent, or permission to run code or send files.

### Read a captured invocation

Use the Qt-free, pure reader for a complete stdout capture and the independently
observed process return code:

```python
from scene_production.completion import read_completion

completion = read_completion(
    process_result.stdout,
    process_result.returncode,
    expected_operation="render",  # Or "inspect" / "describe".
)
if completion.status == "succeeded":
    artifacts = completion.terminal["artifacts"]  # For render/inspect only.
    run_token = completion.run_token
```

`stdout` may be UTF-8 bytes or text. Preserve its final newline; do not call
`strip()`, drop malformed lines, or concatenate different invocations. Supply
`None` for `returncode` if exit has not been observed, never the event's own
`exit_code`. No observed exit means unknown even if stdout claims success.

For render/inspect the reader requires exactly one `started`, then one terminal.
It captures the token from that **actual started event**, checks that the
terminal matches it and the expected operation, and checks protocol version,
field types, outcome/exit-code agreement and success manifest shape. If the
caller already retained the token when `started` arrived, pass it as
`expected_token=...` for an additional correlation check. Do not invent a token
before launch or obtain the expected token from the terminal or a receipt.

`Completion` provides `status`, a fixed diagnostic `reason`, `run_token` when
available, and `terminal`. A consistent reported failure remains `failed`;
interruption, persistence and transport failures remain `unknown`. `describe`
success has only a terminal with `description` and no artifacts. Recognized
pre-claim failures also have only a terminal. A usage failure has exit 2,
`error.code: invalid_arguments` and `operation: null`, because parsing never
completed; this is the only accepted null-operation exception.

Malformed or truncated JSONL, duplicate keys (including nested keys), missing or
extra events, token/operation mismatches and unexpected exits return `unknown`
with `terminal=None`. A valid terminal with `status: unknown` is retained, but
still does not authorize a retry or certify process cleanup. Additional timing
and diagnostics fields do not establish completion or liveness.

The reader never opens files, reconciles persisted state/journals, rehashes
artifacts, runs code, retries or creates a new attempt. Its success confirms
consistent captured protocol evidence, **not that artifact bytes still match
now**. The terminal's hashes and verification scope remain the producer's
claims. The separate local handoff runner performs its own durable-state and
retained-byte verification. Neither path establishes semantic acceptance.

## Attempt files and privacy

A render/inspect attempt contains:

```text
new-attempt/
  attempt.json             latest protocol state
  events.jsonl             started + terminal journal, when available
  api.log                  captured Python and native stdout/stderr
  diagnostics.jsonl        optional private phase timing, not protocol state
  production/scene-.../    normal public API result.json, render.log and artifacts
```

The attempt directory is private (`0700` on POSIX); adapter state and log files
are private (`0600`). Each state/journal update uses a fresh temporary file,
flush/fsync and atomic replacement. The two replacements are **not** one atomic
transaction, and stdout is a third independent destination. On a persistence
or transport failure the adapter tries to save an unknown state and never emits
a second terminal. If disk writes fail, persisted files may remain stale.
Always apply the process-exit and event rule, not just one saved file. No
concurrent writer, hostile-scene, filesystem-race or power-loss guarantee is
made. `main()` changes process output descriptors and signal handlers and is
intended for an isolated CLI invocation, not a concurrent in-process service.

Public API stdout/stderr, including native fd writes, goes to `api.log`, not the
protocol stream. Error events contain fixed error codes and diagnostics paths,
not raw exception messages, log excerpts, receipt contents, or environment
values. Logs and the public receipt may themselves contain private information;
treat them as local private diagnostics and review them before sharing.
Diagnostic paths may be absent on early failures. A failure receipt is linked
only if it belongs to this attempt. Font hints use the existing fixed-message
filter, never arbitrary scene log text.

Common error codes: `invalid_arguments`, `output_exists`, `output_unavailable`,
`input_or_tool_missing`, `invalid_input`, `operation_failed`,
`verification_failed`, `persistence_failed`, `transport_failed`, `interrupted`.
`diagnostics.stage`, when present, is a known public pipeline stage. Missing-tool
errors are not guessed from arbitrary log text.

Existing events also carry observed UTC `timestamp`, monotonic `elapsed_seconds`
and wrapper `phase`. No extra stdout events are emitted. See
[logging-and-diagnostics.md](logging-and-diagnostics.md) for the phase sidecar,
failure reasons, privacy limits and timing interpretation.

## Verification and history

See the [archived migration record](archive/migrations/migration-public-baseline.md#agent-migration-scope)
for the prototype origins and original migration scope. This interface uses the
public production and reinspection APIs described above.

`tests/test_production_agent.py` covers argument errors, token/state/journal
consistency, directory exclusivity, stale attempts, raw fd output, SIGINT and
SIGTERM, failed persistence and delivery, fixed font hints, foreign receipts,
symlink/hash/receipt rejection, and both public APIs' temporary failed receipts.
The media dependencies in the integration test are faked; this is protocol and
pipeline-boundary coverage, not a real Manim/ffmpeg quality certification.

```sh
python -m unittest tests.test_production_agent
python -m unittest tests.test_production_completion
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests
python -m black --check scene_production/agent.py tests/test_production_agent.py
python -m flake8 scene_production/agent.py tests/test_production_agent.py
```

### Render-session cleanup evidence

Render terminals and new production receipts include `render_cleanup_state`:
`not_started`, `complete`, or `unknown`. This is independent of operation success.
`not_started` records positive no-acquisition control flow for this invocation.
`complete` records a reaped leader, absent captured POSIX group or an empty owned
Windows Job Object observed before its handle closes, and no live output reader.
No deferred setup step can resume execution after that observation. An invalid or
missing core result state is unknown. A kill request, return code, wrapper exit,
reader EOF, timeout label, or diagnostic string alone does not prove cleanup.

These observations cover the supervised session of trusted scene code, not
arbitrary detached/escaped descendants, external side effects, media-helper
processes, or semantic acceptance. POSIX group visibility (including possible
zombies), permission failures and unowned Windows fallback trees can remain
unknown conservatively. This change does not add containment or recovery proofs.

Unverified render cleanup produces one correlated `unknown` terminal with exit 3,
`error.code=render_cleanup_unverified`, `render_cleanup_state=unknown`, and
`process_cleanup_verified=false`. It has no success artifacts and never permits
an automatic retry. The existing timeout/cancel/failure reason can remain in safe
diagnostics. Known no-spawn and completely cleaned failures remain failed/1.
Success requires complete cleanup plus the existing artifact/hash checks.
Interruption and persistence/transport failure still produce unknown lifecycle
outcomes; rendering completion does not make interrupted protocol evidence safe.
The broad `process_cleanup_verified=true` flag is never emitted.

The pure completion reader retains legacy terminals without the additive field
for protocol compatibility. It does not grant permission to reuse a render slot.
It rejects contradictory safe outcomes with unknown cleanup or a false cleanup
verification flag. Inspect/describe do not inherit a source render's state.
A production receipt, including one with this field, is never a liveness signal.
