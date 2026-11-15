# First-use pilot protocol and session log

Copy this outside the public checkout. Freeze the protocol before counting results.
Changes create a new protocol version and cohort; retain prior failures. This is a
formative product test, not a statistical claim about all users or platforms.

## Protocol (fill before first counted session)
- Protocol version/date and owner:
- Pinned MSR revision:
- One supported agent/model/version and documented environment:
- Installation cohort: clean / configured; recovery scenarios tracked separately:
- Task selection and participant prerequisites:
- Agreed brief and acceptance questions:
- Session time limit (choose before trials, e.g. 90 minutes):
- Named reviewer role and review method:
- Consent for recording, retention and sharing; permitted materials:
- Narration requirement and authorized input/provider path:

Plan five sessions across at least three developers unfamiliar with MSR. Record
first exposure separately from repeat sessions; repeats do not count as new users.
Recruitment and consent must be secured, not inferred from this template. Internal
fresh-agent rehearsals are a separate cohort and never replace human sessions.

Provisional gate: at least four of five complete the agreed job within the frozen
session limit without maintainer rescue, with no unresolved serious recurring
blocker. Completion requires participant and named reviewer acceptance of the exact
final artifact, requested revision, full playback (and listening if audio),
accessible delivery and recoverable context. Record disagreements, blocked attempts
and abandonment as non-passes, not exclusions. Explain any deviation; do not change
the threshold retroactively. Report both per-session and first-exposure outcomes.

Maintainer rescue means project-team help beyond the published instructions:
choosing an undocumented interpreter, repairing setup or source for the agent,
supplying a missing command or explaining undocumented recovery. Ordinary participant
answers to the brief and their creative feedback are expected, not rescue. Log
both, and classify ambiguous assistance explicitly rather than silently excluding it.
A serious blocker includes data loss, unsafe execution, unrecoverable project state
or inability to complete a required step; record severity and recurrence evidence.

## Per-session log
- Session ID and anonymous participant ID; exposure number:
- Cohort: internal rehearsal / unfamiliar developer:
- Protocol version, commit, environment and agent:
- Start/end timestamps; elapsed vs active time distinction:
- Setup, first useful preview, correction, review and delivery times:
- Brief and acceptance-question references:
- Baseline/candidate hashes and evidence/project-record references:
- Bounded correction outcome; broader-feedback outcome (or not exercised):
- Intervention log: timestamp, action, actor, duration, classification
  (documentation / environment / agent behavior / tool gap / ordinary clarification):
- Commands failed, recoveries and abandoned attempts:
- Exact playback/listening scope and who reviewed (decode/samples are separate):
- Semantic state/caption consistency and pacing findings:
- Participant decision; reviewer decision; rationale:
- Delivery opened and project resumed by a different agent? Evidence:
- Transfer scope: record-only / same-machine unpack / separate-machine render:
- Final result: pass / fail / blocked / abandoned; reason:
- Observed friction vs untested hypothesis; proposed next fix:

Engineering exit: starter, templates and internal rehearsal evidence reviewed, with
known limitations retained. Customer exit: the frozen human pilot gate met. If
participants are unavailable, engineering may finish while customer validation
stays pending. Never equate CI, synthetic fixtures or internal agent success with
customer acceptance.
