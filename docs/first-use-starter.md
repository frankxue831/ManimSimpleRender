# First useful video: agent starter

This is an ordered guide for a developer working with an existing agent, not an
agent runtime or an automatic pipeline. MSR renders trusted code and retains
production evidence; the agent plans and the user judges the intended result.
Start from a pinned checkout and record its commit. The companion templates are
plain Markdown checklists, not new MSR schemas or inputs to its APIs.

## 1. Agree on the result

Copy the [project record](templates/video-project.md) outside the public source
checkout. Record the audience, one intended learning outcome, format, duration
range, language, narration requirement and observable acceptance questions.
Propose one direction and resolve consequential ambiguity before production.
An agent's assumptions are not user approval. Record decisions and unresolved
questions, not private conversation transcripts. Do not collect credentials here.

For example, a developer might request a short explanation of why retrying a
payment can duplicate a charge. Acceptance questions could ask whether the viewer
can distinguish a lost response from a failed payment and explain what reusing an
idempotency key does. Do not turn this illustrative topic into a required template.

## 2. Establish readiness

Choose and record one interpreter/environment. Follow the [quickstart preparation
and preflight](agent-quickstart.md#agent-runtime-preflight), then its small control
render with that same interpreter. Run from the trusted MSR checkout; resolve
project-relative inputs to actual local paths when invoking commands. Record
commands, exits, errors and recovery actions locally. Never hide a missing
installation by silently switching to a prepared machine and calling it a clean
install. Classify clean installation, configured-runtime and recovery trials
separately. Unknown attempts need investigation, not automatic retries.

The control proves a small render, not that the requested video is useful.
Review all Python before executing it. MSR is not a sandbox.

## 3. Author and produce a draft

The agent authors the Scene and inspection timeline from the brief. The timeline
selects inspection points; it does not animate the Scene. Use the [agent render
and completion contract](agent-quickstart.md#render-and-accept-the-operation-result),
a fresh attempt directory and retained stdout, stderr and exit status. Preserve
render quality, FPS, fonts and dependencies. Store references to the resulting
receipt and artifact identities in the project record rather than copying their
schemas. Verify delivery specifications separately from render success.

Keep descriptive manifest captions synchronized with the visible text. A manifest
is an author assertion, not OCR or proof of what viewers see; do not omit important
captions merely to satisfy an audit.

## 4. Review the exact output

Open the video and inspect relevant frames. Record technical checks, sampled
frame coverage, continuous playback and human/editorial judgment separately.
Use [editorial records](editorial-review.md) for findings tied to actual video
bytes. A successful command or a machine audit is not an approval.
Record who reviewed which hash, what they watched, the acceptance-question answers,
and unresolved issues. If playback is unavailable, say so and leave acceptance
pending. Obtain consent before recording participants or sharing their material.

Review meaning as well as layout: after each state transition, do titles and
captions still describe the current state? For example, an "empty cache" label
must not remain after the animation fills it. Inspect immediately before and after
transitions, then watch the complete video for pacing and comprehension. Full
decoding and a chronological sample sheet do not constitute continuous playback.
Unchanged protected frames can still contain an existing defect; an audit proves
preservation, not that every preserved frame was correct.

## 5. Revise from feedback

Retain the baseline. For a bounded silent Scene correction, follow the
[silent revision walkthrough](silent-review-example.md) and
[revision contract](scene-revision.md). Preserve settings, inspect changed and
protected content, and create a fresh review for the candidate bytes. An author's
claim to address a finding is not its resolution.

For broader creative feedback, explain the new scope and agree on it; produce a
new baseline or use the appropriate workflow. Do not weaken guards, label changed
protected frames unchanged, or force every edit into one section to obtain a pass.
Changing inspection times alone uses reinspection, not source revision.

## 6. Add required audio, then deliver

Decide audio in the brief, not after promising completion. An ordinary Scene's
[existing-WAV preview](scene-audio.md) needs authored caption events and compatible
local clips; it does not synthesize speech or retime it, and the result is outside
the silent revision audit contract. Explainer's network-dependent gTTS is a
separate storyboard workflow, not an ordinary Scene integration. Do not use
private provider credentials or paid services without the applicable approval.
Check timing early if narration is required, and watch/listen to the full final
mix. A silent draft does not satisfy a narrated-final requirement.

Deliver an accessible video with its brief, exact identity, changes, review scope,
remaining issues and editable project location. Keep the original and candidate.
Update the project record's next action. A receiving agent should identify those
facts from retained project files without reconstructing the conversation.
Use [portable projects](portable-projects.md) for actual transfer: relative links
in this record alone do not make existing receipts relocatable or replace their
receiving preflight. Check links again on the receiving machine. A same-machine
unpack/preflight checks packaging, not cross-machine rendering. A receiver must
review trusted inputs, select its own interpreter, run preflight and create a new
local baseline. Preserve original evidence separately; do not rewrite its paths
or claim its receipt-based audit can be replayed unchanged after transfer.

## Validate the journey

Use the [pilot protocol and log](templates/first-use-pilot.md). Engineering readiness
and internal agent rehearsal are useful deliverables but do not complete unfamiliar
human validation. First fix measured friction; do not add a hosted application,
new orchestration service or publishing automation to make this guide work.
