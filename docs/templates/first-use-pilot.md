# First-use pilot protocol and session log

Copy this template outside the ManimSimpleRender (MSR) checkout before running
a pilot of the ordinary-`Scene` [starter guide](../first-use-starter.md). Pair
each session with a [video project record](video-project.md); see the
[documentation index](../README.md), [editorial review](../editorial-review.md),
[scene revision](../scene-revision.md) and [portable
projects](../portable-projects.md) for linked workflow details.

Freeze all protocol parameters and thresholds before counting results. Any
parameter change (or code/doc patch for the pilot under test) creates a new
protocol version and cohort; retain prior results rather than resetting counts
or altering thresholds retroactively.

## Protocol (fill before first counted session)

### Protocol fields

- Protocol version/date and owner:
- Pinned MSR commit:
- Fixed external coding agent/model/version and documented environment:
- Setup cohort for the gate (`clean install` / `configured`; planned `recovery` scenarios run in a separate cohort):
- Task selection and participant prerequisites ("unfamiliar with MSR" = no prior MSR setup or authoring runs before entering the pilot):
- Repeat-session policy (count toward five planned sessions: `yes` / `no`; per-participant cap; never counts toward three-developer minimum):
- Brief mode (`fixed` / `participant-supplied`) and acceptance questions:
- Required workflow checks (single-section silent revision: `mandatory` / `on request`; `scene_production.editorial` JSON records: `mandatory` / `optional`; minimum transfer scope: `record-only` / `same-machine unpack & preflight` / `separate-machine preflight` / `separate-machine render`):
- Session time limit (minutes; `active` / `elapsed`; whether reviewer check and fresh-agent recovery run inside or outside this limit):
- Named reviewer role (independent of participant/agent; state whether maintainer-independent and who reviews internal rehearsals) and review method:
- Consent scope for recording, retention and sharing; permitted materials:
- Narration requirement and authorized path (`silent` / `local PCM WAV`; use a separate cohort for [Explainer](../../README.md#explainer-explainer) `gTTS` or user-approved external TTS and mark ordinary-`Scene`-only fields `n/a`):
- Ambiguous-intervention rule (count as `maintainer rescue` or `fail`) and cohort serious-blocker recurrence threshold:

### Cohort, gate and definitions

Recruit participants and record consent before counted sessions. Each human pilot
gate evaluates one setup cohort (`clean install` or `configured`, not mixed):
plan five sessions across at least three unfamiliar developers (at least three
first-exposure sessions). A participant's `repeat` session remains in cohort
`unfamiliar developer`. Planned `recovery` scenarios and internal rehearsals
form separate cohorts and never replace or enter the unfamiliar-developer count;
however, an unscripted setup failure during a planned `clean install` or
`configured` session stays in that starting setup cohort and logs non-pass or
rescue if it fails or receives undocumented help.

Provisional human pilot gate: at least four of the five planned sessions in the
cohort finish with `Final result: pass`, and no serious blocker recurs across the
cohort at or above the frozen recurrence threshold without a documented
self-service workaround usable within the frozen commit and docs (patching MSR
starts a new protocol version and cohort).

Time accounting uses `elapsed` (wall-clock minutes from session start to delivery
or stop) or `active` minutes (excludes scheduled breaks and out-of-session
asynchronous reviewer wait; includes consent, setup, prompting, rendering and
troubleshooting, plus review/resumption when inside the timer). If the frozen
protocol runs named review or fresh-agent recovery outside the session timer,
that asynchronous time does not trigger timeout `fail`.

Completion (`pass`) requires: (1) full continuous playback (and listening if
audio) by both participant and named reviewer; (2) recipient-verified playable
delivery (intended recipient, or named reviewer when acting as recipient, opens
and plays the file outside the authoring command); (3) recoverable context
(verified by facilitator/reviewer when a fresh agent given only the retained
project directory - excluding chat history and this pilot log - accurately states
the brief, baseline/final hashes, review state and next action in a read-only
check); (4) all checks marked mandatory in `Required workflow checks`; and (5)
participant and named-reviewer acceptance of the final SHA-256-identified video
(and audio `preview.mp4` when `local PCM WAV` is used) plus any requested
revision.

Session outcome classification rules:

- `pass`: meets all completion criteria above within the frozen time limit, with zero maintainer rescue, no ambiguous intervention counted as `fail`, no gate violation and no unresolved serious blocker in the session.
- `fail`: exceeds the frozen time limit, requires maintainer rescue (or an ambiguous intervention counted as `fail`), completes only via an undocumented workaround after a serious blocker, triggers a gate violation, misses participant or reviewer acceptance (including disagreement) or omits mandatory checks.
- `blocked`: cannot continue due to an external tool, environment or halting serious blocker.
- `abandoned`: stopped early by the participant or facilitator before completion.

Record disagreements, blocked attempts and abandonment as non-passes, not
exclusions. Distinguish minor in-session deviations (logged explicitly; e.g.
harmless step reordering within agreed scope) from gate violations (forces
`fail`; e.g. skipping a mandatory check or unlogged rescue) and parameter changes
(new protocol version and cohort per the freeze rule above).

Interventions record `disposition` and `cause`. Valid `disposition` values:
`maintainer rescue` (project-team help beyond the [starter
guide](../first-use-starter.md) and linked docs: choosing an undocumented
interpreter, repairing setup/source, supplying a missing command or explaining
undocumented recovery); `ambiguous` (borderline assistance such as pointing to a
section of existing linked docs or host-OS troubleshooting unrelated to MSR);
and `ordinary` (brief/domain answers and creative feedback). Valid `cause` values:
`documentation`, `environment`, `agent behavior`, `tool gap` and `ordinary
clarification`. Never silently exclude ambiguous assistance.

A serious blocker includes data loss, unsafe execution, unrecoverable project
state or inability to complete a required step. Within a frozen cohort, a
serious blocker counts as resolved only if the participant resolves or bypasses
it self-service using documented MSR behavior (without maintainer rescue).

## Per-session log (copy one block per session)

### Session `<N>`

- Session ID and anonymous participant ID; exposure (`first-exposure` / `repeat`) and participant session number:
- Participant cohort (`unfamiliar developer` / `internal rehearsal` / `recovery`):
- Starting setup classification (`clean install` / `configured` / `recovery`):
- Protocol version, commit, environment and agent:
- Start/end timestamps; total `elapsed` and `active` minutes:
- Cumulative minutes (`active` or `elapsed` per protocol; `n/a` if unreached) at milestones (setup ready / first inspected silent draft / audio `preview.mp4` if applicable / revision complete / review complete / delivery):
- Brief and acceptance-question references:
- Silent run and audio `preview.mp4` (if applicable) SHA-256 hashes, receipts and project-record references:
- Revision outcomes (`pass` / `fail` / `not exercised` / `n/a`): single-section silent revision; multi-section or shared-helper edit:
- Interventions (`timestamp | actor | action | duration | disposition | cause`):
- Serious blockers (`none` / blocker ID, concrete impact severity, workaround or resolution status, cross-attempt/cross-session occurrence count):
- Protocol deviations and gate-violation check (`none` / deviations logged; zero gate violations `yes` / `no`):
- Commands failed, recoveries and abandoned attempts:
- Participant and named-reviewer continuous playback/listening scope (log automated decode and sampled PNGs separately):
- Semantic state/caption consistency and pacing findings:
- Participant decision, named-reviewer decision, `scene_production.editorial` record refs and rationale:
- Delivery opened and played by recipient (`yes` / `no`; verification evidence):
- Fresh-agent context recovery from retained project files only (`yes` / `no`; verification evidence):
- Transfer scope achieved (`record-only` [local retained files without portable ZIP] / `same-machine unpack & preflight` / `separate-machine preflight` / `separate-machine render`):
- Final result (`pass` / `fail` / `blocked` / `abandoned`); reason:
- Observed friction vs untested hypothesis; proposed next fix:

## Pilot exit criteria and summary

- Engineering exit: starter guide, templates and at least one end-to-end internal rehearsal (matching the planned setup/narration path and mandatory checks with zero rescue) peer-reviewed, with known limitations retained. If unfamiliar participants are unavailable, engineering review may finish while human pilot validation remains pending.
- Unfamiliar-developer pilot exit: frozen human pilot gate above met. Report both per-session and first-exposure outcomes as formative evidence scoped to the recorded cohort, MSR commit and environment, rather than statistical claims across all users or platforms.

### Pilot summary and exit sign-off

- Counted sessions (`pass`/`fail`/`blocked`/`abandoned` totals) and first-exposure outcomes:
- Cross-session serious-blocker summary (IDs, severity, occurrence count and unresolved count):
- Engineering exit status / Human pilot exit status and reviewer sign-off:
