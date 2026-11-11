# Local multi-agent task handoff

`python -m scene_production.handoff` adds an opt-in, local coordination layer
over `scene_production.agent`. The existing render, inspect, revision and GUI
interfaces are unchanged. Run `describe` for the machine-readable entry point.

Start with the [agent quick start](agent-quickstart.md) for a single rendering agent.
Use this coordination layer when local workers need durable dependencies and claims.
See the [documentation index](README.md) for other workflows.

This first phase provides durable dependency records, exclusive task claims,
leases and fencing, authorization-source handoff, and a local pull inbox with
explicit acknowledgement. It does **not** connect to Grok Bot, send messages,
create automations, operate PostgreSQL, open a network port, or launch another
agent. A pending event means a record is available, not that a person or bot
received it.

## Why this boundary

The supplied November 7 Grok Bot collaboration review described normal Task
completion and Mac Shell invocation, but did not establish a stable public
failure/approval subscription, offline delivery guarantee, or recipient-acceptance
ACK. Its MSR analysis targeted `80891ab`; at the time this layer was introduced,
the `006f3e9` baseline already had the JSONL agent wrapper. This layer reuses that
wrapper rather than creating another renderer or treating internal Grok protocols
as supported APIs.

SQLite is embedded, standard-library storage in a new private workflow directory.
It gives this single-machine workflow atomic claims and state/event transactions
without provisioning a database service. No existing PostgreSQL installation or
database is read or changed. This is not a recommendation to share SQLite over a
network filesystem or a claim that PostgreSQL is unnecessary for future use.

## Trust and authorization

Only trusted local callers and scene code are supported. Rendering executes
Python with the caller's privileges and inherited environment. This is not a
sandbox, authenticated service, or permission engine.

An authorization record includes:

- `authorization_id`, `source_kind: "user_message"`, and `source_ref`.
- The user's `original_text` and the preceding `question_text`. Use an empty
  question only when the user's instruction did not answer a question.
- Explicit `allowed_actions`, `allowed_resources`, and `restrictions` lists.
- A revision, active/revoked state, and a revocation source reference.

The **caller must read the actual user source and current changes** before each
claim, then supply the revision it checked. Strings labelled `user_message`
cannot authenticate their author. Agent paraphrases, document instructions, or a
database row saying approved do not create permission. Restrictions are carried
as evidence for the receiving agent; free text is not automatically interpreted.
Resource identifiers are exact workflow labels, not filesystem access controls.
The caller must check that payload files/actions really belong to that scope.

Revocation blocks future claims and successful completion under that record.
At claim time, eligibility checks the task's own authorization and the status
and active authorization of each **direct predecessor**. It does not recursively
check earlier ancestors. Heartbeat and successful finish recheck only the
running task's **own authorization**, along with its lease and fencing state;
they do not revalidate predecessor authorization. For example, revoking a
parent's separate authorization after a child is claimed does not stop that
child's heartbeat or successful finish. Revoking an earlier ancestor also does
not invalidate a completed intermediate task or automatically block its child
when the direct predecessor's authorization remains active. Tasks sharing the
revoked record are blocked by their own authorization check.

This is not recursive authorization or review invalidation. The caller must
inspect relevant upstream authorization and changed instructions before using
their results; external changes unknown to this ledger cannot be detected
automatically. A rejected heartbeat causes bounded process termination attempts,
not a guarantee every descendant process has stopped. This mechanism does not
replace any runtime confirmation policy.

Task payloads retain file paths, not a content-bound approval of those inputs.
The ledger does not freeze referenced scene/timeline files or bind manual review
results or authorization to their hashes. Changing a file after a manual review
does not itself revoke that review or invalidate the task. Retained render
artifact hashes verify recorded output bytes, not that the inputs were the
version a user or reviewer approved. Callers must compare the actual inputs and
reviewed version themselves; content-bound input approval remains later work.

No real approval or private project transcript should be committed to source
control. Keep local workflow files private. The examples below use invented,
explicitly illustrative evidence; they cannot authorize real work.

## A draft -> render -> reviewer flow

Create a new workflow, then register locally prepared JSON records:

```sh
python -m scene_production.handoff --workflow /absolute/new-workflow init
python -m scene_production.handoff --workflow /absolute/new-workflow \
  authorization-add --input /absolute/authorization.json
python -m scene_production.handoff --workflow /absolute/new-workflow \
  task-add --input /absolute/draft-task.json
python -m scene_production.handoff --workflow /absolute/new-workflow \
  task-add --input /absolute/render-task.json
python -m scene_production.handoff --workflow /absolute/new-workflow \
  task-add --input /absolute/reviewer-task.json
```

Authorization example, **illustrative only**:

```json
{"authorization_id":"demo-permission-v1","source_kind":"user_message",
 "source_ref":"example-only:user-2","original_text":"Yes, prepare a local draft.",
 "question_text":"May I draft, render and review this local demo?",
 "allowed_actions":["draft","render","review"],
 "allowed_resources":["demo-v1"],"restrictions":["Do not publish or send files."]}
```

Each task has `task_id`, `title`, `owner`, `kind` (`manual` or `render`), `action`,
`resource`, `authorization_id`, `depends_on` and `payload`. Add parents before
children. Dependencies are immutable and must already exist, so cycles cannot
be introduced. Use distinct tasks for new work; this phase has no retry command.

Render task example:

```json
{"task_id":"render-v1","title":"Render local draft","owner":"producer",
 "kind":"render","action":"render","resource":"demo-v1",
 "authorization_id":"demo-permission-v1","depends_on":["draft-v1"],
 "payload":{"scene_file":"/absolute/scene.py","scene":"Demo",
            "timeline":"/absolute/timeline.json","quality":"ql","timeout":120}}
```

The reviewer is a manual task with action `review` and
`depends_on: ["render-v1"]`. A human or agent performs the actual review; a task
record cannot conduct it or certify semantic quality.

```sh
python -m scene_production.handoff --workflow /absolute/new-workflow \
  claim-manual --task-id draft-v1 --worker writer --authorization-revision 1
# Perform the authorized draft work. Retain the returned attempt_id and fence.
python -m scene_production.handoff --workflow /absolute/new-workflow \
  finish-manual --attempt-id ATTEMPT --fence 1 --status succeeded \
  --input /absolute/draft-result.json
python -m scene_production.handoff --workflow /absolute/new-workflow \
  run --task-id render-v1 --worker mac-renderer --authorization-revision 1
python -m scene_production.handoff --workflow /absolute/new-workflow \
  events --owner reviewer
python -m scene_production.handoff --workflow /absolute/new-workflow \
  handoff --id reviewer-v1
python -m scene_production.handoff --workflow /absolute/new-workflow \
  ack --event-id EVENT --owner reviewer
```

Manual work must finish within its lease. For longer manual tasks, renew with
`heartbeat-manual --attempt-id ID --fence N --lease-seconds 60` or the Python API.
`run` renews its own lease. CLI manual completion cannot fabricate a render
result. `handoff` returns the original authorization and current/predecessor
attempts, including verified render artifact paths and
hashes. `attempt --id ID` and `attempts --task-id ID` expose the recorded outcomes
and evidence even when a task has no dependent reviewer.
ACK records receipt of a particular immutable event, not acceptance of content,
review PASS, ownership authentication, or permission to publish.

## Consumer loop and failure visibility

An inbox is an advisory wake signal, not the authoritative work list. ACK and
claim are separate transactions: if a consumer ACKs a ready event and crashes
before claiming, the task can remain pending and eligible with no unacknowledged
ready event. Restarting or polling does not recreate that event while eligibility
stays unchanged. Durable event storage therefore does not guarantee execution.

A robust consumer must scan durable tasks on startup, reconnect, and **each
processing cycle**, even when its inbox is empty. Query
`tasks --owner NAME --eligible`, then fetch `handoff --id TASK` for each candidate.
Verify the original authorization and current restrictions before claiming; the claim
transaction rechecks eligibility, so a scan or old ready event is not a reserved
execution slot. This is a consumer requirement, not an installed polling daemon
or automatic task execution. It closes the ACK-before-claim discovery gap, not
the separate problem of recovering already claimed, expired or unknown work.

A failed or unknown predecessor's terminal event is addressed to **that
predecessor's owner**, not automatically to the owners of dependent tasks.
Downstream tasks remain pending with `dependency_blocked`; their consumers may
receive no new inbox event explaining the failure. Downstream consumers must
also inspect their pending tasks and query `handoff` for current direct
predecessor statuses and attempt results, rather than relying on inbox contents
alone. Follow predecessor task IDs when earlier ancestry matters, and fetch
their `authorization` records when checking upstream permission. The handoff is
a current-state snapshot, not recursive approval verification or a failure
notification fan-out.

CLI commands return one JSON response. `run` exits 0 for succeeded, 1 for failed,
and 3 for unknown; inspect the structured result as well as the exit code.
Rejected requests exit 1 without echoing private input or exception text.

Each new runner attempt also writes a private diagnostic phase timeline and
retains a fixed failure phase/cause when possible. These records do not change
claim, completion or recovery rules. See
[logging-and-diagnostics.md](logging-and-diagnostics.md) for the complete log
inventory and how to separate waiting, rendering, checking and handoff time.

## Execution and recovery rules

- Claim and state/event changes use SQLite transactions. Two workers cannot
  claim the same pending task. Dependencies must all succeed before eligibility.
- One running or unknown render holds the render slot **within this ledger**.
  Separate ledgers/direct Manim invocations are not globally coordinated.
- The runner launches a fixed Python module with argv, never a shell or a
  caller-selected command. It correlates started/terminal tokens, observed exit,
  disk state/journal, receipt and retained artifact hashes before success.
  It continues renewing the lease while draining logs, hashing artifacts, and
  writing process/exit evidence. On interruption it attempts bounded group
  termination followed by forced group cleanup even if the leader has exited;
  detached descendants and unbounded storage stalls remain outside its guarantee.
- A legacy `result.json` can temporarily say failed during media inspection.
  It is never used as a liveness signal, failure trigger, or retry instruction.
- Completion and downstream ready events commit together. Repeated polling
  returns unacknowledged records; duplicate ACK is harmless. No remote send
  receipt or consumer side effect is invented.
- Ready events are advisory. If another task takes the render slot, claim
  rechecks current eligibility. Regaining eligibility creates a fresh ready
  event; acknowledging an older event does not suppress this new handoff.
- `reconcile` marks expired attempts unknown and advances the fencing token.
  Late completion cannot overwrite them. It does not kill processes or retry.
- `runner.reconcile_evidence(output_directory)` is a read-only proposal based on
  recorded observed exit and rechecked protocol/artifacts. It does not trust a
  receipt alone, un-fence a task, release an unknown render slot, or resume work.
  Inspect process ownership and private logs before deciding what to do next.

### Audited abandonment of an unknown local render

`resolve-unknown --input /absolute/resolution.json` is an explicit operator
decision to abandon a task, **not** a success override, retry or cancellation
command. It is supported only for render attempts made by the new local runner.
It leaves the attempt and its unknown result unchanged, appends an audit record,
advances the task fence and marks only that task `abandoned`. Its dependents stay
blocked. Unrelated pending renders can become eligible, with their ready events
committed in the same transaction. New work still needs its own task and valid
authorization. No permission, review PASS or external completion is inferred.

The command independently requires one of these narrow proofs:

1. The runner's durable launch gate is still `not_started`. Every supported
   runner must atomically commit `spawn_intent` before launching the agent.
   Abandonment fences the old worker, so it cannot subsequently cross the gate.
   A missing process file alone is **not** this proof.
2. `spawn_intent` was recorded, and the current OS reports the same machine but
   a different boot-session identity. This establishes that the old local
   processes cannot still be running. It does not establish what they completed,
   or whether they triggered remote effects. macOS uses `IOPlatformUUID` and
   `kern.bootsessionuuid`; Linux uses machine-id and the kernel boot ID. Machine
   identifiers are hashed before storage. Probe failures never supply proof.

An operator's name, source reference, evidence references and explanation are
audit context, not authenticated authority or proof of stopped processes. The
command does not accept `execution_stopped` assertions. Legacy attempts with no
guard, general/external API claims and manual attempts remain blocked. For
`spawn_intent` attempts, same-boot, different-host or unavailable identity
evidence cannot release the slot. The unused launch-gate proof does not require
machine identity.
Observed wrapper exit, successful artifacts and a missing PID do not substitute
for the supported proof. `reconcile_evidence` remains read-only. Do not edit the
database, fabricate identity data, or create another ledger to bypass uncertainty.

Illustrative audit input, with IDs and the **current task fence** obtained from
`task` and `attempts` after inspection and `reconcile` if needed:

```json
{"resolution_id":"inspection-1","task_id":"render-v1",
 "attempt_id":"ACTUAL-ATTEMPT-ID","expected_fence":2,
 "operator":"local-operator","source_ref":"example-only:operator-decision",
 "reason":"Abandon this unknown attempt without adopting its outcome.",
 "evidence_refs":["example-only:inspection-record"],"decision":"abandon"}
```

Keep the decision and references truthful and private. Reusing the same
`resolution_id` with identical input safely returns the existing audit record;
different input is rejected. A competing resolution or stale fence is rejected.
`resolution --id ID`, `resolutions --task-id ID` and `handoff` expose the audit,
machine-derived proof and authorization snapshot. Never invent a success or
repeat rendering because an acknowledgement was lost.

These proofs assume trusted local callers, one live local SQLite database and
one non-cloned machine installation. They do not cover malicious local writers,
concurrently used backup copies, VM snapshot restoration, cloned machine IDs,
remote executors, or arbitrary remote effects from trusted scene code. The gate
is not an OS security boundary. Existing legacy unknowns intentionally cannot be
retrofitted with proof. No reboot, process killing or external action is performed
by the recovery command.

A restart preserves evidence and pending events, but **automatic execution
recovery is not implemented**. There is no scheduler, polling daemon, automatic
reminder, delivery transport, automatic retry, or claim of exactly-once external
side effects. WAL/network filesystems, hostile local writers, power-loss fault
injection, actual reboot recovery and Mac sleep/wake are not certified. Boot
transition tests use injected identities; they do not claim a real-machine reboot
test.

Task completion and retained-byte verification are distinct from fact checking,
visual quality, audience understanding, version-bound review withdrawal and
release eligibility. Those review records, complete input/runtime freezing,
new-version supersession and remote integration remain later work.

## Verified Grok integration boundary

Checked November 7, 2026: the official
[routines and automations documentation](https://docs.x.ai/grok-bot/skills-routines-and-automations)
supports an inbound webhook as a possible future MSR-to-Grok wake signal.
It accepts HTTP POST with a Bearer secret and optional JSON. Its stated contract
is: "A `200` response means Grok Bot accepted the call and started a run".
Acceptance is not completion or a recipient's application ACK. Rate-limit
failures are not automatically retried. That page does not establish provider
idempotency, durable offline redelivery, or an owner-acceptance protocol.

No webhook was created or invoked for this implementation. A later adapter
must preserve `event_id` across retries, distinguish HTTP acceptance from owner
ACK, treat a timeout after transmission as uncertain, and never restart a render
merely because notification failed. A minimal approved envelope could contain
only `event_id`, `task_id` and `attempt_id`; private authorization text, paths,
logs and media should not be included by default.

The [computer documentation](https://docs.x.ai/grok-bot/computer-and-apps)
describes a cloud execution environment. A cloud Bot cannot be assumed to read
this Mac-local workflow or localhost. Its ability to invoke these CLI commands
on the specific Mac must be verified separately, using the currently supported
Grok tools and appropriate user authorization. The installed app's internal
SendToAgent schemas are not a supported public integration contract; their
parameter spellings differ from the supplied review's examples.

`tests/test_handoff_consumer_flow.py` exercises a no-send integration boundary:
each producer/consumer command runs in a separate local interpreter. It verifies
that an ignored poll survives restart, original authorization and predecessor
results can be fetched, duplicate ACK is idempotent, wrong-owner ACK is rejected,
revocation blocks a later claim even after an earlier ready event and ACK, and
an ACK-before-claim crash is recovered by scanning durable owner task state.
This is our CLI contract, **not a Grok runtime or actual delivery test**.

Real integration still needs an explicitly chosen Grok recipient/routine,
approved payload and test invocation, secure credential provisioning, and a
verified way for that Bot to query the Mac-owned state. Creating routines or
persistent credentials, enabling background polling, sending a test event, and
consuming Grok usage are not performed or authorized by these examples.

## Tests

```sh
python -m unittest discover -s tests -p 'test_handoff*.py'
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests
python -m black --check app explainer scene_production tests main.py
python -m flake8 app explainer scene_production tests main.py
```

Focused tests cover multiprocess claim races, expiry/fencing, revocation,
transaction rollback, last-dependency ready events, duplicate ACK, restart
persistence, corrupt subprocess protocol, bounded logs and artifact tampering.
Recovery tests cover audit/CAS rollback and races, stale launch rejection,
same-boot and unavailable-evidence refusal, injected host/boot transitions,
idempotent audit queries, and manual lease renewal across CLI processes.
Fake subprocess tests are transport tests, not real-video quality acceptance.

### Cleanup uncertainty at the scheduling boundary

The scheduler requires explicit `render_cleanup_state=not_started` or `complete`
for failed/1 render terminals, and `complete` plus all existing byte checks for
succeeded/0. Legacy render terminals without this evidence remain readable by the
pure protocol reader but are classified unknown by the scheduler, with
`cleanup_evidence_missing`. They gain no artifacts/verification claim and are not
retroactively rewritten as safe. Contradictory or invalid protocol also fails
closed.

A correlated cleanup-unknown terminal is validated against the observed agent
exit, attempt state and journal and retained as unknown, not treated as transport
corruption. The wrapper can have exited normally while the render session remains
unverified. Read-only reconciliation preserves unknown. The render slot remains
held, and dependents remain blocked. Existing fenced finish/eligibility rules,
launch gates and audited abandonment proofs are unchanged; wrapper exit, missing
PIDs, operator assertions and same-boot scans are not new release proofs.

### Reproducible real-Manim handoff regression

The public two-second fixture in `tests/support/real_handoff_scene.py` uses only
geometric objects. With the existing render prerequisites installed, run from the
trusted checkout root (the chosen output directory must not already exist):

```sh
MSR_HANDOFF_EVIDENCE_DIR=/existing/parent/new-handoff-fixture \
  python -m unittest -v tests.e2e.test_real_handoff
```

This is deterministic local **multiprocess integration**, not independent-agent
review, human review or semantic acceptance. Authorization strings are explicitly
invented fixture data, not actual user quotes or permission for other work.

The regression creates draft -> render -> review tasks in one ledger. A consumer
process ACKs both created and ready events, then exits normally before claiming.
Fresh CLI processes verify an empty inbox, no attempt, and discovery via the durable
eligible-task scan. This tests the ACK-before-claim interruption gap, not an OS crash
or recovery of already-running/unknown work.

Two local contender processes rendezvous at a test-only pipe barrier, then each
invokes the ordinary handoff CLI once. The harness requires observed invocation
overlap, one successful process and one rejected claim, one render attempt/output,
and matching started/terminal tokens, observed exit, durable state, receipt and
current artifact hashes. The winner is deliberately unspecified. The barrier does
not make SQLite transactions simultaneous or install a scheduler/consumer daemon.
Inputs are also compared with their prepared fixture hashes; this caller check
does not add content-bound authorization to the ledger.

`summary.json` reports API/fixture success and `decision: needs_review`. The manual
review task remains pending and eligible, without a claim. Nobody has assessed the
pixels, animation meaning or audience understanding. Frame count/duration checks
and retained PNGs are not a visual PASS or permission to publish.

Without the environment variable, successful temporary evidence is removed;
failed evidence is retained and its location printed. An explicit directory retains
both successes and failures and is never reused. Preserve failures and investigate;
there is no automatic render retry, database repair or unknown-state resolution.
Timeout cleanup is bounded and best-effort, not proof all descendants stopped.
The per-PR `readme-quickstart` job requires the real dependencies, bounds this step
to three minutes and retains only public fixture inputs, command/protocol/exit logs,
media and summaries for seven days, including on failure. It excludes the ledger
database. Other test jobs skip this regression when render tools are absent.
