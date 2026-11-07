# Local multi-agent task handoff

`python -m scene_production.handoff` adds an opt-in, local coordination layer
over `scene_production.agent`. The existing render, inspect, revision and GUI
interfaces are unchanged. Run `describe` for the machine-readable entry point.

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
ACK. Its MSR analysis targeted `80891ab`; the current `006f3e9` baseline already
has the JSONL agent wrapper. This implementation reuses that wrapper rather than
creating another renderer or treating internal Grok protocols as supported APIs.

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
The runner checks during heartbeat and before final recording; external changes
unknown to this ledger cannot be detected automatically. A rejected heartbeat
causes bounded process termination attempts, not a guarantee every descendant
has stopped. This mechanism does not replace any runtime confirmation policy.

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

Manual work must finish within its lease. The Python API provides heartbeat for
longer manual tasks. `run` renews its own lease. CLI manual completion cannot be
used to fabricate a render result. `handoff` returns the original authorization
and current/predecessor attempts, including verified render artifact paths and
hashes. `attempt --id ID` and `attempts --task-id ID` expose the recorded outcomes
and evidence even when a task has no dependent reviewer.
ACK records receipt of a particular immutable event, not acceptance of content,
review PASS, ownership authentication, or permission to publish.

CLI commands return one JSON response. `run` exits 0 for succeeded, 1 for failed,
and 3 for unknown; inspect the structured result as well as the exit code.
Rejected requests exit 1 without echoing private input or exception text.

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

Unknown attempts intentionally remain blocked in this first phase. A restart
preserves their evidence and pending events, but **automatic execution recovery
is not implemented**. There is no scheduler, polling daemon, automatic reminder,
delivery transport, unknown-resolution command, automatic retry, or claim of
exactly-once external side effects. WAL/network filesystems, hostile local
writers, power-loss fault injection and Mac sleep/wake are not certified.

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
and revocation blocks a later claim even after an earlier ready event and ACK.
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
Fake subprocess tests are transport tests, not real-video quality acceptance.
