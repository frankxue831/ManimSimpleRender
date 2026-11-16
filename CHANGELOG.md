# Changelog

All notable changes to this project are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Opt-in aligned revision capture**: `scene_production.revision capture
  --require-aligned` requires native section traces and matching inspection
  windows before creating a version snapshot. Default capture behavior and
  audit verdicts are unchanged; see [existing production preparation](docs/scene-revision.md#preparing-an-existing-production).
- **First-use starter and templates**: Add an ordered
  [first-use starter](docs/first-use-starter.md),
  [project record template](docs/templates/video-project.md), and
  [first-use pilot log](docs/templates/first-use-pilot.md), and update the
  [agent quick start](docs/agent-quickstart.md#agent-runtime-preflight) to place agent runtime
  preflight before the [control-scene render](docs/agent-quickstart.md#prepare-a-control-scene)
  (#42).
- **Agent runtime preflight (`python -m app.environment --json`)**: Add a Qt-free JSON
  preflight command reusing the Desktop GUI's **Check Environment** probes
  (`app/environment/doctor.py`), upgrading present-but-non-runnable `ffmpeg` from `doctor.py`'s
  GUI warning to a preflight failure, requiring `ffprobe`, reporting missing or non-runnable default
  `Tex` tools (`latex`, `dvisvgm`) as warnings unless `--require-latex` is set, and reporting
  interpreter-specific `recovery.manim_install_argv`, advisory `cairo`/`pangocairo` `pkg-config`
  diagnostics, and `render_smoke_test: "not_run"`
  ([`docs/agent-quickstart.md#agent-runtime-preflight`](docs/agent-quickstart.md#agent-runtime-preflight))
  (#39).
- **Scene production (`scene-production` / `python -m scene_production`)**: Add rendering
  and recorded-video reinspection for hand-authored ordinary `Scene` projects (caller-vetted
  Python Manim `Scene` scripts executed with caller privileges), with sequential media
  checks, inspection timelines, retained inputs, sampled PNGs, and `result.json` receipts
  ([`docs/scene-production.md`](docs/scene-production.md)) (#16).
- **Dynamic examples**: Add hand-authored Netty EventLoop (`examples/scenes/netty_eventloop.py`)
  and TCP message-framing (`examples/scenes/tcp_stream.py`) examples with inspection timelines
  and font-coverage checks ([`docs/dynamic-examples.md`](docs/dynamic-examples.md)) (#16).
- **Silent scene revisions (`python -m scene_production.revision`)**: Add scoped single-section
  revisions for silent `Scene` baselines, stable section IDs, frame evidence, guarded edits,
  retained rollback versions, an executable revision example
  (`python -m examples.revisions.run_flow`), and opt-in `--localize-frames` changed-frame
  localization in `audit` (`scene_production/frame_localization.py`)
  ([`docs/scene-revision.md`](docs/scene-revision.md)) (#17, #31, #44).
- **Portable projects (`python -m scene_production.portable`)**: Add selected-file ZIP export,
  bundle inspection, unpacking, receiving preflight, an isolated launcher
  (`python -I scene_production/launcher.py`, excluding the current working directory and
  `PYTHONPATH` on untrusted project directories), and explicit `quality`/`fps` preservation in
  `project.json` `render` settings (earlier unreleased portable consumers without `render`
  support reject projects using that field;
  [`docs/portable-projects.md`](docs/portable-projects.md),
  [`docs/portable-projects.md#preserve-requested-render-settings`](docs/portable-projects.md#preserve-requested-render-settings))
  (#21, #43).
- **Caption sidecars and WAV audio previews (`scene_production.captions`, `scene_production.audio`)**:
  Add Python APIs to bind explicit author frame events (`CaptionEvents` in
  `app/render/caption_events.py`) into derived `draft.srt` sidecars (with standalone CLI
  `python -m app.render.subtitle_draft`) and checksum-lock ("freeze") existing caller-supplied
  PCM WAV recordings into separate narrated MP4 previews
  ([`docs/scene-captions.md`](docs/scene-captions.md), [`docs/scene-audio.md`](docs/scene-audio.md))
  (#24).
- **Frame evidence (`python -m scene_production.visual_frames`) and `.[review]`**: Add
  caption-linked decoded frame sampling (`--captions`, #24) and caption-free bounded
  full-resolution (`native/`) decoded frame evidence with coverage indexes and labeled raster
  contact sheets (`--from-run`, requiring `Pillow>=11` via `.[review]`;
  [`docs/scene-frame-samples.md`](docs/scene-frame-samples.md)) (#34).
- **Editorial review (`python -m scene_production.editorial`)**: Add `record` and `compare`
  commands saving version-bound JSON assessments and candidate comparisons for delivered `.mp4`
  videos ([`docs/editorial-review.md`](docs/editorial-review.md)) (#29).
- **Agent interface (`python -m scene_production.agent`), completion reader, and `--fps`**: Add
  a JSONL attempt wrapper over production `render` and `inspect`
  ([`docs/scene-agent.md`](docs/scene-agent.md)), fail-closed completion reader
  (`scene_production/completion.py`, #30), and integer `1`-`240` `--fps` (`requested_fps`,
  intent-only, `app/render/frame_rate.py`;
  [`docs/scene-production.md#explicit-requested-frame-rate`](docs/scene-production.md#explicit-requested-frame-rate))
  across `scene_production`, `agent`, and `handoff` (#24, #36).
- **Local handoff (`python -m scene_production.handoff`)**: Add opt-in SQLite worker
  coordination over `agent.py` with immutable task dependencies, recorded user-authorization
  evidence, fenced claims, a pollable local event inbox ("pull inbox"), and audited recovery
  ([`docs/agent-handoff.md`](docs/agent-handoff.md)) (#25).
- **Font failure diagnostics and isolated reinspection**: Add fixed-message `scene_font`
  failure hints naming `render.log` in `scene-production` and `scene_production.agent`
  ([`docs/scene-production.md#failed-scene-font-selection`](docs/scene-production.md#failed-scene-font-selection))
  and recorded-video reinspection via `python -I scene_production/launcher.py inspect`
  ([`docs/scene-production.md#reinspect-an-unchanged-recorded-video`](docs/scene-production.md#reinspect-an-unchanged-recorded-video))
  (#22).
- **Logging and output identity**: Add render phase diagnostics (`diagnostics.jsonl`), persisted
  GUI log export (**Save Log**, marked `INCOMPLETE` for mid-run snapshots), packaged-code
  SHA-256 fingerprints (`implementation.json`), and Explainer output identity records
  ([`docs/logging-and-diagnostics.md`](docs/logging-and-diagnostics.md)) (#26).
- **Runnable review and handoff examples**: Add traced silent revision review
  (`python -m examples.silent_review.run`, [`docs/silent-review-example.md`](docs/silent-review-example.md),
  #30), synthetic post-render composition review (`python -m examples.composed_review.run`,
  [`docs/composed-review-example.md`](docs/composed-review-example.md), #38), and real-Manim
  concurrent handoff regression (`python -m unittest tests.e2e.test_real_handoff`,
  [`docs/agent-handoff.md#reproducible-real-manim-handoff-regression`](docs/agent-handoff.md#reproducible-real-manim-handoff-regression),
  #33).
- **Documentation**: Add [agent quick start](docs/agent-quickstart.md), task-oriented navigation
  in the [documentation index](docs/README.md), and the [archive index](docs/archive/README.md)
  grouping historical plans, migrations, evaluations, and reviews under `docs/archive/` (#27).

### Changed

- **Render stack dependencies (`pyproject.toml`)**: The render stack upgrades pinned
  `manim==0.22.1` and `numpy>=2.1,<3` across `.[explainer]` and `.[gui]`, upgrades
  `manim-voiceover[gtts]==0.4.0` in `.[explainer]`, drops the `setuptools<81` constraint,
  updates `explainer/codegen/code_compat.py` and `explainer/codegen/tts_compat.py`, enforces
  `manim==0.22.1` at `scene_production --record-scene-frames` startup
  (`scene_production/frame_capture.py`), and rejects unsupported `frame_trace` schema versions
  (`scene_production/frame_trace.py`;
  [`docs/scene-revision.md#traced-rendering-requirements`](docs/scene-revision.md#traced-rendering-requirements))
  (#28, #32).
- **Editorial review (`python -m scene_production.editorial`)**: Writers (`record`) now emit
  schema version 2 with a required `record_prepared_at` canonical UTC timestamp
  (`YYYY-MM-DDTHH:MM:SS.sssZ`, observed once from the writer's local wall clock;
  [`docs/editorial-review.md#record-preparation-timestamp`](docs/editorial-review.md#record-preparation-timestamp)).
  Updated readers still accept strict version 1 records without migration or backfill, whereas
  older unreleased v1-only readers reject version 2 and must be upgraded before producing new
  records. `compare` output adds `record_prepared_at` (`null` for version 1, saved value for
  version 2) and `timestamp_notice`; strict output-key consumers must allow these new fields.
  Preparation time does not establish review or publication time (#37).
- **Render cleanup evidence (`app.render.cleanup.RenderCleanupState`)**: `JobResult` objects
  (`app.render.runner.JobResult`) now carry `render_cleanup_state` as an explicit supervised
  render-process/session teardown field separate from outcome inference: `not_started`,
  `complete`, or `unknown`
  ([`docs/scene-agent.md#render-session-cleanup-evidence`](docs/scene-agent.md#render-session-cleanup-evidence))
  (#35). Custom runners and `JobResult` producers must provide explicit observed evidence (the
  default or any missing/invalid value is `unknown`); `SUCCESS` requires `complete`, while
  `not_started` in a `JobResult` requires `return_code=None` and outcome `FAILED` or
  `MANIM_NOT_FOUND` (not `SUCCESS` or `CANCELLED`).
- **Upgrade and publication gates (`render_cleanup_state`)**: Upgrading schedulers or readers
  requires operators to stop new dispatches, drain older workers, reconcile outstanding
  attempts via `scene_production.handoff reconcile`, and upgrade custom runners and workers
  together before resuming dispatch
  ([`docs/agent-handoff.md#cleanup-uncertainty-at-the-scheduling-boundary`](docs/agent-handoff.md#cleanup-uncertainty-at-the-scheduling-boundary)).
  Pre-upgrade legacy records lacking `render_cleanup_state` remain readable, but missing cleanup
  evidence cannot release `scene_production.handoff`'s exclusive active render lock
  ("render slot") or authorize another render. An `unknown` result holds its render slot,
  halts GUI batches, and blocks artifact publication across Explainer (raises
  `RenderError("render_cleanup_unverified")`), [`concept_render`](docs/concept-render.md) (the
  standalone silent concept-JSON draft workflow; writes a `status="failed"` receipt with
  `render_cleanup_state="unknown"`), and `scene_production`/`agent` (`agent` exits `3` with
  `status="unknown"` and `error.code="render_cleanup_unverified"`).
- **Cleanup operator recovery (`python -m scene_production.handoff`: `reconcile`, `resolve-unknown`)**:
  Reconciliation retains `unknown` attempts and their logs for operator investigation without
  backfilling `complete` from exit codes, valid media, elapsed time, a disappearing PID, or a
  GUI restart, and neither releases the render slot nor triggers an automatic retry. Audited
  abandonment accepts only supported proofs (an unused pre-spawn launch gate `not_started` /
  `fenced_before_spawn_intent` before `spawn_intent` is committed, or `spawn_intent` with a
  verified `same_host_reboot`) documented in the
  [handoff guide](docs/agent-handoff.md#audited-abandonment-of-an-unknown-local-render); no
  same-boot force-release or recovery shortcut exists for already-spawned `unknown` attempts.
- **Documentation**: [`README.md`](README.md), [`AGENTS.md`](AGENTS.md), and
  [`docs/README.md`](docs/README.md) now reflect current usage, architecture boundaries, and
  development-check instructions.
- **Known limits (`scene_production` and `scene_production.revision`)**: Inspection timelines
  select check timestamps; they do not drive animation, and media checks or sampled frames do
  not establish factual correctness or full-playback acceptance
  ([`docs/scene-production.md`](docs/scene-production.md)).
  `python -m scene_production.revision restore` requires retained baseline and candidate runs
  along with their media and is not a crash-atomic multi-file transaction; traced
  `--record-scene-frames` requires `manim==0.22.1` with the Cairo renderer and is exercised in CI
  on Linux only ([retention requirements](docs/scene-revision.md#retention-and-restore-verification);
  [traced rendering limits](docs/scene-revision.md#traced-rendering-requirements)).

### Fixed

- **Desktop GUI (`app/config/loader.py`)**: Fall back to built-in default configuration when
  reading or decoding packaged `app/config/config.v2.json` fails (#41).
- **Desktop GUI (`app/ui/main_view.py`)**: Respect the primary **Start Render** / **Cancel Render**
  button's disabled state for `Ctrl+Return` (#41).
- **Render core (`app/render/worker.py`)**: Report `not_started` in per-job `render_cleanup_state`
  diagnostics when job preparation fails before process spawn (#41).
- **Explainer (`explainer/codegen/manim_codegen.py`)**: Place plot-only beat captions below
  their plot instead of at the origin (#41).
- **Explainer (`explainer/codegen/diagram_codegen.py`, `explainer/storyboard/model.py`)**:
  Validate actor/path start consistency in generated diagram motion, preserve explicit
  destination visibility, and layer guide paths behind moving actors
  ([`docs/diagram-motion.md`](docs/diagram-motion.md)) (#23).
- **Explainer (`explainer/cite/verifier.py`, `explainer/plan/emit.py`)**: Restrict storyboard
  citation reads to the source root (`meta.source.repo_root`), including resolved symlink
  targets, and use exclusive temporary writability probe files in `explainer plan` so
  existing sibling `.staging` or `.rejected` files next to `-o` stay untouched (#40).
- **Portable transfer (`scene_production/portable.py`)**: Bound decompressed portable ZIP
  output instead of trusting declared entry sizes, and reject compression methods other than
  stored/deflated (#40).
- **Local handoff (`scene_production/handoff/runner.py`)**: Renew valid SQLite task leases
  during bounded error cleanup without bypassing fencing checks (#40).

## [0.2.0] - 2026-10-23

### Breaking: how you install it

- **Plain `pip install .` no longer installs the desktop GUI or manim.** The base
  install now has no dependencies, so pick an extra (#13):
  - `pip install -e ".[verify]"` installs only PyYAML, which is enough for `explainer verify`.
  - `pip install -e ".[explainer]"` adds manim and manim-voiceover so you can run `explainer render`. It
    needs the cairo system libraries (and LaTeX); the README lists the apt and brew packages.
  - `pip install ".[gui]"` installs the PyQt5 desktop app and manim.
  - `pip install -e ".[planner]"` adds the Anthropic SDK for `explainer plan`, which drafts a storyboard from real code. It is not part of `.[explainer]`, so `verify` and `render` stay light.
  - `pip install -r requirements.txt` still installs the same packages as before.

### Changed: the citation gate is stricter

Storyboards that passed on 0.1.0 can fail now. That's deliberate: each case below
used to let a "verified" badge stand on evidence that didn't prove anything.

- A plain evidence token must match whole words: `"2"` no longer matches `24`, and `TOKENS` no
  longer matches `MAX_TOKENS`. `/regex/` tokens and text without spaces between words (CJK) are
  unchanged (#8).
- A cite's first and last lines must both hold evidence. Otherwise it fails with the new reason
  `loose_range`, which names the tighter range to cite instead (#8).
- Tokens that match without any real source text (`""`, `"   "`, `"//"`, `"/a*/"`) are rejected (#8).
- Regex tokens that raise `OverflowError` or `RecursionError` count as missing instead of crashing (#8).

### Changed: errors and exit codes

The exit codes are 0 ok, 2 validation error, 3 citation miss, 4 render failure, 5 planner LLM failure, and 6 when the planner produced nothing usable (5 and 6 come only from `explainer plan`).

- `explainer verify` now also runs code generation, so a storyboard that passes the gate but can't
  become a scene fails at verify. Code-generation errors print one `error:` line and exit 2 in
  both `verify` and `render`, instead of a traceback and exit 1 (#11).
- Malformed plot specs (non-numeric, infinite or NaN axis bounds, YAML dates, reversed axes,
  non-mapping curves or annotations) now fail with an `error:` line naming the beat, exit 2 (#12).
  Empty or falsy values such as `x: []` or `curves: ""`, which used to fall back to defaults, are
  errors now too.
- `explainer render` without manim installed stops before writing anything, exits 4, and prints the
  install command (#13).

### Changed: the planner

- `explainer plan` trims each cite to the lines its evidence covers, so generated storyboards pass
  the stricter gate (#8).
- Evidence now reaches a cite's first and last lines. When a trim still cuts a line of code, the
  CLI prints a `dropped line ...` note instead of shortening the cite silently (#10).

### Added

- `examples/citation-gate.story.yaml`, the storyboard for the third explainer video, *What the
  verified badge actually promises* (#9).
- CI checks that every `examples/*.story.yaml` verifies and generates (#9), and a job that runs the
  README quick start as written on Python 3.12 and 3.13 (#13).

### Fixed

- The proxy example scene finds `assets/` again after the move to `examples/scenes/` (#6).
- `CLAUDE.md` imports `AGENTS.md`, so agent sessions load the shared guide (#6).

### Docs

- The README states what the gate checks (the cited lines still contain the declared evidence),
  not that narration can't drift (#6).
- The README has one quick start with prerequisites first, an Examples section with links to
  each video, and the desktop GUI under its own heading (#6, #13).
- The package description, `AGENTS.md` and the CLI docstring describe the repo as Explainer
  Engine plus the GUI (#7).

### Known limits

- The Mac brew and MacTeX install lines are not tested in CI.
- A "verified" badge vouches for the cited lines when the video was made, not for the narration.

## [0.1.0] - 2026-10-15

First tagged release (`6973ba4`).

[Unreleased]: https://github.com/frankxue831/ManimSimpleRender/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/frankxue831/ManimSimpleRender/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/frankxue831/ManimSimpleRender/releases/tag/v0.1.0
