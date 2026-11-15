# Video project record (copy and fill)

Manual project index; ManimSimpleRender (MSR) tools do not parse or validate it.
Keep the filled record local and private by default. Copy it into a project
directory outside the MSR checkout, use paths relative to this record for
project files, and retain authoritative receipts (original run directories,
`result.json`, `attempt.json`, `events.jsonl`, `snapshot.json` and editorial
review JSONs) unchanged; relative paths here do not make machine-bound receipts
relocatable.

Never record credentials or full conversation transcripts; if running a pilot or
sharing, review consent and remove private prompts, personal/participant details
and machine-specific absolute paths (keep local-only interpreter/environment logs
in a separate unshared file if the record may be shared).

Guides (relative links apply inside `docs/templates/`): [first-use
starter](../first-use-starter.md), [documentation
index](../README.md#choose-a-workflow), [agent quick
start](../agent-quickstart.md), [scene production](../scene-production.md),
[scene revision](../scene-revision.md), [editorial
review](../editorial-review.md), [scene captions](../scene-captions.md), [scene
audio](../scene-audio.md), [composed review](../composed-review-example.md),
[portable projects](../portable-projects.md), [local
handoff](../agent-handoff.md), [Explainer](../../README.md#explainer-explainer),
[concept rendering](../concept-render.md) and [pilot
protocol](first-use-pilot.md).

Marking a version `accepted` requires reviewer `approved`, answered acceptance
questions and recipient playback verification; never mark `accepted` solely
because a render, revision audit or `editorial` `compare` check succeeded. After
edits, verify hashes and review validity; do not carry a prior version's
approval forward.

## Brief and decisions

- Project/topic:
- Audience and prerequisite knowledge:
- Intended learning outcome:
- Acceptance questions (observable, set before production):
- Duration range, target dimensions, delivery codec/pixel format (e.g. H.264, `yuv420p`), FPS (integer dividing WAV sample rate if planning `local PCM WAV`, e.g. `48000 Hz` for `24`/`25`/`30`/`60` fps; see [scene audio](../scene-audio.md)), language:
- Supported workflow (`scene_production` / `explainer` / `concept_render`; mark `[scene_production]` items `n/a` when unused):
- Audio/narration requirement (`silent` / `local PCM WAV` [required or planned] / Explainer `gTTS` / external TTS):
- Constraints and excluded scope:
- User decision/approval provenance (message/issue reference or date, not a credential or transcript):
- Primary factual/code sources explained in the video (files, commits, docs, URLs):
- Assumptions and unresolved questions:

## Reproduction and current state

- MSR commit; external coding agent/model/version; OS and architecture:
- Python environment/dependencies, setup commands/exits and recovery notes:
- Setup classification (`clean install` = fresh environment / `configured` = existing runtime / `recovery` = repairing failed setup):
- `[scene_production]` Runtime preflight (`python -m app.environment --json`) and control-scene ([`Demo`](../agent-quickstart.md#prepare-a-control-scene)) render evidence (separate from `scene_production.portable preflight`):
- Source, assets, `[scene_production]` inspection timeline (JSON check windows, not animation) and render settings (`quality` `ql`..`qk` per [scene production](../scene-production.md), explicit `fps`, fonts, `--record-scene-frames` flag):
- Current project version (`v1` / `v2` / revision `version-ID`) and state (`draft` / `changes requested` / `needs review` / `accepted` / `blocked` / `abandoned`):
- Baseline or initial `v1` video (`[scene_production]` path, full SHA-256, original render `result.json`; note `--record-scene-frames`):
- `[scene_production]` Current silent candidate video (if revised; otherwise `n/a`; path, full SHA-256, original render `result.json`, source/timeline SHA-256s):
- Current narrated preview or composite video (if applicable; otherwise `n/a`; path, full SHA-256, `preview_audio` receipt or [composed review](../composed-review-example.md) assembly manifest):
- `[scene_production]` Attempt/completion/receipt references (attempt directory, original render vs reinspection `result.json`, `run_token`, exit code, completion status including any unresolved `unknown` attempts):
- `[scene_production]` Revision manifest, baseline snapshot (`snapshot.json`), `locate` / `verify` / `audit` outputs (if applicable; exit code and status):
- `[scene_production]` Editorial review and `compare` references (reviewer, reviewed video full SHA-256, review JSON path/SHA-256, `decision` [`changes_requested` / `needs_review` / `approved`], scope, `assessment_current`, remaining findings, `--addresses` claims):
- `[scene_production]` Caption sidecar (`events.json` / SRT), `bind_audio` (`manifest.json`) and `preview_audio` (`manifest.json`) receipt references if applicable:
- `[scene_production]` Revision manifest descriptive `captions` (and caption sidecar `events.json`, if used) visually checked against current video (identified by SHA-256); known mismatches:

## Feedback and delivery

- Requested change and agreed scope (if revised; otherwise `n/a`):
- `[scene_production]` Changed target section/content and protected (unchanged non-target) sections/intervals (if revised; otherwise `n/a`):
- Technical checks and results (MSR decode/probe/audit checks separate from delivery-profile check):
- Sampled frames inspected (including before/after state transitions):
- Continuous playback/listening coverage and reviewer:
- Answers to acceptance questions; user/participant (if running a pilot) and reviewer decisions:
- Unresolved findings, untested claims and limits:
- Delivered artifact (path, full SHA-256, captions/transcript path + SHA-256) and recipient-access verification (opened and played outside authoring command):
- `[scene_production]` Transfer validation (`n/a` [no transfer needed] / `record-only` [retained local files without portable ZIP] / `same-machine unpack & preflight` / `separate-machine preflight` / `separate-machine render`; `project.json` with `manifest` and optional `render`, ZIP path/SHA-256, `inspect`/`preflight` results, separate local baseline receipt):
- Editable project location relative to this record:
- Next action and responsible role (`user` / `authoring agent` / `reviewer` / `recipient`; include `scene_production.handoff` [workflow directory and `task_id`](../agent-handoff.md) if used):
