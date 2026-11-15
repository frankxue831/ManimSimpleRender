# First-use agent starter

[Documentation index](README.md) | [Agent quick start](agent-quickstart.md)

This ordered guide covers the ordinary Python `Scene` (`scene_production`)
workflow for a developer working with an external coding agent:
ManimSimpleRender (MSR) does not provide an agent runtime or an automatic
pipeline. The developer sets requirements and judges the delivered video, the
agent plans and authors code, and MSR executes reviewed Python scenes while
retaining hash-identified local evidence. Start from a pinned MSR checkout (see
[repository installation](../README.md#quick-start) and ordinary-Scene
[environment preparation](agent-quickstart.md#prepare-the-environment-and-inputs))
and record its commit in the project record created in Step 1.

## Happy-path checklist

For a standard silent `Scene` project, work through Steps 1-6 in order (optional
branches link out to dedicated guides):

1. **Scope ([Step 1](#1-agree-on-the-result))**: Copy
   [`templates/video-project.md`](templates/video-project.md) outside the MSR
   checkout and agree on the brief, format, and acceptance questions.
2. **Preflight ([Step 2](#2-establish-readiness))**: Run
   `python -m app.environment --json` and render the
   [control scene](agent-quickstart.md#prepare-a-control-scene) with one chosen
   interpreter.
3. **Draft ([Step 3](#3-author-and-produce-a-draft))**: Author the `Scene` and
   inspection `timeline.json` (plus [revision markers and
   `manifest.json`](scene-revision.md#source-markers-and-manifest) if planning
   scoped edits or portable export), then render into a fresh `--output-dir` via
   [`scene_production.agent`](agent-quickstart.md#render-and-accept-the-operation-result).
4. **Review ([Step 4](#4-review-the-exact-output))**: Inspect transition frames,
   watch continuous playback, and bind an
   [editorial review record](editorial-review.md) to the video SHA-256.
5. **Revise if needed ([Step 5](#5-revise-from-feedback))**: Snapshot the
   baseline, apply a scoped edit, re-render, audit, and record a fresh editorial
   decision via [scene revision](scene-revision.md).
6. **Deliver ([Step 6](#6-add-required-audio-then-deliver))**: Optionally attach
   [captions](scene-captions.md) and [local PCM WAV audio](scene-audio.md) (then
   re-review `preview.mp4`), verify recipient playback, and optionally package
   for transfer with [portable projects](portable-projects.md).

## 1. Agree on the result

Copy the [project record](templates/video-project.md) into a separate project
directory outside the MSR checkout; the linked Markdown templates are manual
checklists, and MSR APIs do not consume them. Record the audience, one intended
learning outcome, duration range, dimensions, FPS, language, narration
requirement and observable acceptance questions. Choose the supported workflow
path up front: Steps 2-6 cover a silent ordinary `Scene` or an ordinary `Scene`
paired with pre-recorded local PCM WAVs (Step 6), whereas source-citing
storyboards with built-in network `gTTS` use
[Explainer](../README.md#explainer-explainer) and silent concept JSON drafts use
[concept rendering](concept-render.md) (see the [workflow
index](README.md#choose-a-workflow)). Propose one direction and confirm open
requirements with the user rather than treating agent assumptions as approval.
Record decisions and unresolved questions, omitting credentials and full
conversation transcripts.

For example, a developer might request a short explanation of why retrying a
payment can duplicate a charge. Acceptance questions could ask whether the
viewer can distinguish a lost response from a failed payment and explain what
reusing an idempotency key does.

## 2. Establish readiness

Choose and record one Python interpreter/environment. Follow [environment
preparation](agent-quickstart.md#prepare-the-environment-and-inputs), run
[agent runtime preflight](agent-quickstart.md#agent-runtime-preflight), and
render the [control scene](agent-quickstart.md#prepare-a-control-scene) using
the [completion contract](agent-quickstart.md#render-and-accept-the-operation-result)
with that same interpreter. Run module commands from the trusted MSR checkout
directory and pass resolved paths to files in your separate project directory.
Record the setup classification (`clean install` for installing into a fresh
environment, `configured` for reusing an existing prepared runtime, or
`recovery` for repairing a failed setup), commands, exits, errors and recovery
steps in the project record.

`python -m app.environment --json` runs non-rendering tool probes (Python,
`manim --version`, `ffmpeg` and `ffprobe`, plus optional `latex`/`dvisvgm` with
`--require-latex`; `render_smoke_test: not_run`), and the basic control scene
omits frame tracing, LaTeX, custom fonts and audio. If you plan to run traced
silent revision audits in Step 5, verify `manim==0.22.1` now ([observed frame
evidence](scene-revision.md#optional-observed-frame-evidence)). Review all
Python before executing it; MSR runs scene code with caller privileges, not
inside a sandbox.

## 3. Author and produce a draft

From the project record's `Brief and decisions` section, the agent authors the
Manim Python `Scene` subclass and the JSON inspection timeline (contiguous
segments and sample timestamps; see [scene production](scene-production.md) and
the [control scene example](agent-quickstart.md#prepare-a-control-scene)). The
timeline selects inspection points; it does not animate the Scene. If local WAV
narration will be attached in Step 6, choose an integer FPS aligned with the
WAV sample rate and size scene cue windows so each pre-recorded WAV fits inside
its frame-timed cue ([existing-WAV preview](scene-audio.md)).

If the project may use bounded silent revisions (Step 5) or portable transfer
(Step 6), include paired `# scene:start <id>` / `# scene:end <id>` markers,
`self.next_section("<id>")` calls (with the first before the first emitted
frame), and a revision `manifest.json` up front; pass `--record-scene-frames` on
the baseline render when planning traced `revision audit` (see [source markers
and manifest](scene-revision.md#source-markers-and-manifest) and [traced
rendering requirements](scene-revision.md#traced-rendering-requirements)).

Invoke rendering through the [agent render and completion
contract](agent-quickstart.md#render-and-accept-the-operation-result) using a
fresh `--output-dir` per attempt while retaining stdout, stderr and the observed
process exit code. If an attempt finishes `unknown` (exit `3`, `130` or `143`,
or missing/incomplete JSONL events per [agent events and
completion](scene-agent.md#events-and-completion)), investigate processes and
retained files instead of retrying automatically. Record the chosen render
quality, FPS, font requirements and dependencies in the project record, along
with paths and SHA-256 hashes for the canonical `result.json` receipt and
terminal `artifacts` map rather than copying full JSON payloads.

## 4. Review the exact output

Open the rendered video and inspect relevant frames. Record technical checks,
sampled frame evidence (from the render receipt or [caption-free frame evidence
packages](scene-frame-samples.md#caption-free-evidence-packages)), continuous
playback and human/editorial judgment separately. Verify target delivery
format requirements (such as H.264 profile, `yuv420p` pixel format, dimensions
and FPS) separately; render inspection checks decode and probe metadata, not
delivery encoding profiles ([scene production](scene-production.md)).

Use [editorial records](editorial-review.md) to bind findings to exact video
SHA-256 bytes. A passing render or inspection check is not editorial approval.
Record who reviewed which hash, what they watched, the acceptance-question
answers and unresolved issues. If playback is unavailable, state that in the
record and leave acceptance pending.

Review meaning as well as layout: after each state transition, check that titles
and captions still match the visible state (for example, an "empty cache" label
must not remain after the animation fills it). Inspect frames immediately before
and after transitions, then watch the complete video for pacing and
comprehension. Full media decoding and chronological preview sheets
(`sheets/sheet-*.png` from `scene_production.visual_frames`) do not replace
continuous playback.

## 5. Revise from feedback

Before editing source files for a single-section silent revision, capture an
immutable version snapshot from the unmodified Step 3 baseline run with
`python -m scene_production.revision capture` and retain both the baseline run
and snapshot. Follow the [revision contract](scene-revision.md) (using the
[silent revision walkthrough](silent-review-example.md) as a worked reference):
keep render settings consistent, inspect both the edited target section and
non-target ("protected") intervals, and record a fresh [editorial
assessment](editorial-review.md) for the candidate video bytes; naming a prior
finding in `--addresses` records an author claim, not reviewer resolution.
Unchanged non-target frames can still carry an earlier defect: a revision audit
verifies frame preservation outside the target, not that preserved frames were
free of defects. Keep descriptive `captions` in the revision manifest aligned
with current visible text manually and note any mismatch in the project record;
revision checks do not run OCR or compare `captions` against pixels.

For edits spanning multiple sections or changing shared helpers, agree on the
expanded scope first, then either apply the edit directly and render and review
a new baseline via Steps 3-4 (capturing a fresh `revision capture` snapshot if
single-section edits will resume) or continue with full re-renders via
`scene_production.agent` without `revision` audits (or choose a different
authoring path in the [workflow index](README.md#choose-a-workflow)). Do not
drop `depends_on` declarations, rewrite baseline snapshots or receipts, or
widen `# scene:start` / `# scene:end` markers to hide unrelated edits; record
audit failures accurately in the project record. Changing inspection sample
times alone uses [reinspection](agent-quickstart.md#reinspect-or-edit)
(`python -m scene_production.agent inspect`), not source revision.

## 6. Add required audio, then deliver

For an ordinary Scene with local narration, bind frame-timed `events.json` cues
to an original non-reinspection render `result.json` via
`scene_production.captions` ([caption sidecars](scene-captions.md)) and attach
integer-PCM local WAVs via `scene_production.audio` ([existing-WAV
preview](scene-audio.md)). These helpers do not synthesize or retime speech, and
the resulting `preview.mp4` is outside the silent revision audit contract: if
`preview.mp4` reveals a timing mismatch, adjust the silent Scene in Steps 3-5
and re-bind from the new render receipt. Because `preview.mp4` changes container
bytes, prior silent [editorial](editorial-review.md) assessments become stale
(`candidate_requires_fresh_assessment: true`)-watch and listen to the full mix
and record a fresh assessment bound to `preview.mp4` before delivery. Paid TTS
or LLM credentials require explicit user approval; source-citing storyboards
with keyless network `gTTS` use [Explainer](../README.md#explainer-explainer)
instead.

Deliver a playable video verified for recipient access (plus required captions
or transcripts) together with retained project files recording the brief, exact
SHA-256 identities, applied changes, review state and scope, open issues,
editable project location and next action so a receiving agent can resume
without reconstructing chat history. Retain both baseline and candidate runs
when revised.

For cross-directory or cross-machine transfer, package with [portable
projects](portable-projects.md) (`project.json`, setting `"render"` explicitly
when quality differs from `qm` or an explicit FPS was used; see [preserve
requested render
settings](portable-projects.md#preserve-requested-render-settings)). Relative
paths in the project record do not make machine-bound receipts relocatable: a
receiver must review trusted code, select a local interpreter, run preflight and
render a new local baseline while keeping producer evidence separate and
unmodified.

---

## Optional pilot validation

This section is for maintainers or teams evaluating this starter guide itself;
it is not required to deliver an individual video. Use the [pilot protocol and
session log](templates/first-use-pilot.md) and obtain participant consent before
recording sessions or sharing participant materials. Keep clean-install,
configured-runtime and recovery cohorts separate (never substitute a prepared
machine and log it as a clean install). Internal agent rehearsals form a
separate cohort and do not substitute for unfamiliar-developer pilot sessions.
