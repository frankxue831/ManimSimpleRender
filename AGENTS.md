# AGENTS.md

This file guides coding agents (Codex, Claude Code, and others) working in this repository.
[`CLAUDE.md`](CLAUDE.md) imports it via `@AGENTS.md`, so Claude Code loads the same guide.

## Project Overview

ManimSimpleRender provides a desktop batch renderer and command-line workflows sharing one
Qt-free Manim subprocess core (`app/render/`):

- `app/`: PyQt5 desktop GUI that AST-discovers Manim `Scene` classes in a folder and
  batch-renders selected scenes.
- `explainer/`: CLI workflow compiling storyboard YAML through a citation gate into
  deterministic Manim codegen and a narrated MP4 (plus an optional LLM storyboard planner).
- `concept_render/`: Standalone source-checkout workflow (`python -m concept_render`)
  generating silent draft scenes from concept JSON.
- `scene_production/`: Tooling for hand-authored ordinary `Scene` projects (`scene-production`
  and `python -m scene_production`), including reinspection, frame evidence, silent
  revision, portable packaging, editorial review, captions/audio helpers, and the
  `scene_production/agent.py` single-agent JSONL wrapper.
- `scene_production/handoff/`: Opt-in local SQLite worker-coordination layer over
  `scene_production/agent.py`.

Throughout these workflows, an *ordinary* `Scene` is a hand-authored Python Manim `Scene`
class, *trusted* means caller-vetted Python executed with caller privileges, and a
*receipt* (`result.json`) records run paths, settings, and SHA-256 hashes.

[`docs/adr/0001-render-core-boundary.md`](docs/adr/0001-render-core-boundary.md) defines the
shared render-core boundary, which [`tests/test_architecture.py`](tests/test_architecture.py)
enforces statically for `app/`, `explainer/`, and `scene_production/`: `app/` never imports
`explainer/`; when importing from `app`, `explainer/` imports only `app.render.*` (never
`app.render.worker`); and `scene_production/` stays Qt-free, never imports `explainer/`,
and imports from `app` only `app.render.*` (never `app.render.worker`) and
`app.scenes.discovery`. By ADR policy, no other top-level package imports `explainer/`,
though `tests/test_architecture.py` does not scan `concept_render/`
([`docs/concept-render.md`](docs/concept-render.md)), which also imports `app.render`.

For tool selection, see the [documentation index](docs/README.md). For an end-to-end
ordinary `Scene` video project, use the [first-use starter](docs/first-use-starter.md).
Agents invoking ordinary `Scene` renders via `scene_production.agent` should start with
the [agent quick start](docs/agent-quickstart.md), and then read the
[protocol reference](docs/scene-agent.md) for completion and recovery rules.

## Architecture

The repository separates the desktop GUI (`app/`), storyboard Explainer CLI (`explainer/`),
source-checkout concept draft workflow (`concept_render/`), ordinary `Scene` workflows and
sidecars (`scene_production/`), and headless tests (`tests/`). Within `app/`, dependencies
flow `UI -> controller -> services` (`config`, `scenes`, `environment`, `render`,
`validation`); services never import the UI, and only `app/render/worker.py` imports Qt.

- `main.py`: Thin entry point delegating to `app/bootstrap.py`.
- `app/bootstrap.py`: Creates `QApplication`, loads and validates config (falling back
  to built-in defaults if the packaged config is corrupt), wires `MainView` and
  `RenderController`, and runs the event loop.
- `app/config/`: `models.py` (typed dataclasses and `ConfigError`), `loader.py`
  (`load_config` and `load_config_or_default` with validation and built-in defaults), and
  packaged `config.v2.json`.
- `app/scenes/discovery.py`: AST-based discovery of renderable Manim `Scene` subclasses
  (import-free; `RECOGNIZED_SCENE_BASES` allowlist). `discover_scene_result()` returns one
  immutable snapshot (scenes and skipped-file diagnostics from the same parse pass); the
  controller fails closed on discovery issues.
- `app/environment/`: `doctor.py` (`check_environment` and per-tool probes for Python,
  Manim, FFmpeg, and LaTeX with injectable runners) and `models.py` (typed check/report
  models). `doctor.py` drives the GUI's **Check Environment** button, and
  `python -m app.environment --json` adapts `check_environment` for ordinary `Scene` agents
  with strict media-tool checks and separate advisory build/LaTeX diagnostics.
- `app/render/`: Qt-free render core plus `worker.py` (the GUI `QThread` adapter). Core
  modules handle jobs and output-collision checks (`jobs.py`: `RenderJob`, `build_jobs`,
  `media_output_collisions`), Manim CLI construction (`command_builder.py`), subprocess
  supervision and cancellation (`runner.py`, `process_session.py` with `terminate -> kill`
  escalation), aggregation (`aggregation.py`: `RenderSummary`), canonical quality flags
  (`quality.py`), cleanup verification (`cleanup.py`: `RenderCleanupState`), phase timing
  (`diagnostics.py`: `DiagnosticTimeline`, see
  [logging and diagnostics](docs/logging-and-diagnostics.md)), packaged-code SHA-256
  diagnostic manifests (`implementation.py`), FPS validation/parsing (`frame_rate.py`),
  author-event staging (`caption_events.py`: `CaptionEvents`), and standalone SRT drafting
  (`subtitle_draft.py`).
- `app/ui/`: `main_view.py` (passive window), `sections.py` (form/layout section widgets),
  `theme.py` and `theme.qss` (external dark theme), and `controller.py` (state machine and
  `QSettings`).
- `app/validation.py`: Pure pre-render validation.
- `explainer/`: `storyboard/` (YAML IR and loader), `cite/` (citation gate checking
  cited lines, whole-word evidence matches, first/last line bounds, and optional clean
  commit match unless `allow_dirty`), `codegen/` (deterministic Manim scene emission and
  diagram canvas), `plan/` (LLM planner drafting storyboards from real code via a
  protocol-backed client, injectable for tests), `render/adapter.py` (render integration
  via `app.render`), and `cli.py` (`explainer verify|render|plan`).
- `concept_render/`: Source-checkout-only concept JSON model, Manim codegen, and silent
  render/revision adapter (`python -m concept_render`; excluded from packaged setup and
  `tests/test_architecture.py`).
- `scene_production/`: Production CLI (`scene-production` / `python -m scene_production`),
  reinspection (`reinspection.py`), frame capture (`frame_capture.py`,
  `evidence_export.py`, `evidence_io.py`, `evidence_sheets.py`), isolated launcher
  (`python -I scene_production/launcher.py`), completion reader (`completion.py`),
  submodules with `python -m scene_production.<module>` CLIs (`agent`, `revision`,
  `portable`, `visual_frames`, `editorial`, and opt-in `handoff/`), and Python-only APIs
  (`captions.py`, `audio.py`). See [scene production](docs/scene-production.md),
  [portable projects](docs/portable-projects.md), and
  [editorial review](docs/editorial-review.md).
- `tests/`: Headless unit tests, multi-tier automation (`tests/e2e/`), explainer
  tests (`tests/explainer_tests/`), shared fixtures (`tests/support/`), the architecture
  guard ([`tests/test_architecture.py`](tests/test_architecture.py)), and the ASCII guard
  ([`tests/test_ascii_guard.py`](tests/test_ascii_guard.py), which enforces pure ASCII across
  `app/**/*.py`).

## Key Components

### UI Layer (app/ui/)

- `MainView` (`main_view.py`): Passive `QMainWindow` with widgets, setter methods, and
  intent signals only; holds no render logic.
- `RenderController` (`controller.py`): Mediates between views and services and owns the
  worker lifecycle. It maintains an `idle`/`running`/`cancelling`/`cleanup_unknown` state
  machine (where `cleanup_unknown` latches the session if cleanup verification fails, while
  validation runs inline when `idle`) and persists last folders, resolution, and engine via
  `QSettings`.
- Dark theme styling uses an external stylesheet (`theme.qss`), and the cancel state uses
  dynamic property `QPushButton[state="cancel"]`.

### Render Engine (app/render/)

- `RenderWorker` (`worker.py`): `QThread` that iterates explicit `(file, scene_class)`
  jobs and streams status, logs, progress, and a final `RenderSummary` via signals.
- `RenderRunner` (`runner.py`): Qt-free subprocess execution with merged stdout/stderr (no
  deadlock), cooperative cancellation via `threading.Event`, `terminate -> kill`
  escalation, and distinct `MANIM_NOT_FOUND` outcomes.
- A render job is one `(file, scene_class)` pair (no file-by-scene cross-product).

### Configuration System (app/config/)

- Typed config v2 (`app/config/config.v2.json`): `resolutions` as `{label, flag}` objects
  and `engines` as `{label, renderer_flag}` objects (typed objects rather than separate
  label/flag lists). The app discovers scenes from the input folder at runtime instead of
  storing them in config.
- Resolutions map to Manim quality flags; the valid set (`-ql`, `-qm`, `-qh`, `-qp`,
  `-qk`) has a single source of truth (`app/render/quality.py`), and
  `app/config/config.v2.json` offers four of the five (no `1440p`/`-qp` entry).
  `renderer_flag` is `""` (`Default`/Cairo) or `"--renderer=opengl"` in the packaged
  config; the loader also accepts `"--renderer=cairo"`.

### Scene Production Sidecars and Handoff (scene_production/)

Keep all `scene_production/` modules Qt-free and preserve public production, revision, and
portable behavior:

- [Caption sidecars](docs/scene-captions.md) (`scene_production/captions.py`): Binds
  explicit author frame events (`events.json`) to a completed original `scene_production`
  `render` receipt (`result.json`, not `reinspect` or `attempt.json`) and generates a
  derived `draft.srt` sidecar (it rejects external SRT input).
- [Audio previews](docs/scene-audio.md) (`scene_production/audio.py`): Checksum-locks
  ("freezes") existing caller-supplied PCM WAV inputs against a caption sidecar
  `manifest.json` and creates separate narrated preview MP4s; silent-only
  [scene revision](docs/scene-revision.md) checks exclude these previews.
- [Frame evidence and caption-linked samples](docs/scene-frame-samples.md)
  (`scene_production/visual_frames.py`): Supports caption-linked sampling (up to 12 decoded
  frames) and bounded, caption-free native-frame evidence with labeled raster contact sheets
  (see the linked guide for schemas, frame/size caps, and why export is not visual
  approval).
- [Agent protocol](docs/scene-agent.md) (`scene_production/agent.py`): Wraps
  `scene_production.render` and `reinspect` APIs with persisted attempt state and JSONL
  events. A receipt (`result.json`) can transiently hold `status=failed` during inspection;
  consumers must rely on API completion or terminal JSONL events and never poll
  `result.json`.
- [Local handoff](docs/agent-handoff.md) (`scene_production/handoff/`): Opt-in local
  SQLite coordination over `scene_production/agent.py` with immutable task dependencies,
  recorded user-authorization evidence, time-limited fenced claims, and a durable pollable
  local pull inbox. Interrupted/unverified (`unknown`) render attempts stay blocked for
  operator investigation; audited abandonment releases the exclusive render slot only with
  supported pre-spawn or same-host reboot proofs, never marks prior outputs as succeeded,
  and requires callers to scan durable task state alongside events.

None of these sidecar, agent, or handoff layers acts as a queue, authenticated approval
system, external messenger, automatic retry service, or semantic review. In frame-extraction
modules (`frame_capture.py`, `visual_frames.py`, `evidence_export.py`), never embed
user-selected ancestor paths in an FFmpeg `-f image2` output pattern; use a fixed relative
name (such as `frame-%03d.png`) inside a freshly created exclusive output `cwd` so ancestor
`%` characters cannot expand or collide.

## Common Commands

### Installing Dependencies

Requires Python `>=3.12,<3.14` (CI tests 3.12 and 3.13) plus system prerequisites
(`cairo`/`pango` dev headers, `ffmpeg`/`ffprobe`, `LaTeX`, and Linux Qt offscreen libs);
see [README Quick start](README.md#quick-start) for OS media/LaTeX packages and
[`.github/workflows/ci.yml`](.github/workflows/ci.yml) for Linux Qt offscreen packages.

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt      # -e .[gui]: desktop GUI + Manim
pip install -r requirements-dev.txt  # -e .[gui,verify,dev]: adds PyYAML, black, and flake8
```

Neither `requirements.txt` nor `requirements-dev.txt` installs `.[explainer]` or
`.[planner]`, and `requirements.txt` omits `PyYAML` (required even to import `explainer`).
Optional `pyproject.toml` extras: `.[verify]` (`PyYAML` only, enough for
`explainer verify`), `.[explainer]` (`PyYAML`, `manim`, and `manim-voiceover`;
`explainer render` also requires network access for `gTTS`), `.[planner]` (`anthropic` SDK
for `--provider anthropic`, plus `ANTHROPIC_API_KEY`; default `--provider grok` uses the
local `grok` CLI; both require `PyYAML` via `.[verify]` or `.[explainer]`), and `.[review]`
(`Pillow` for frame export checks).

### Running Workflows

```bash
python main.py                   # Desktop GUI (or installed `manim-simple-render`)
explainer --help                 # Citation-gated Explainer CLI (`verify|render|plan`; see README.md)
scene-production --help          # Hand-authored Scene production (see docs/scene-production.md)
python -m concept_render --help  # Source-checkout silent drafts (see docs/concept-render.md)
```

For Explainer subcommands, storyboard syntax, and example target
`examples/evidence-budget.story.yaml`, see the [README Explainer section](README.md#explainer-explainer).
For GUI and scene renders, `app/render/command_builder.py` builds commands shaped like
`python -m manim input_file.py SceneName -qh --media_dir output_folder` (frozen GUI builds
invoke external `manim`, and `scene_production` appends `--disable_caching --silent`).

### Tests, Formatting, and CI

```bash
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests
# When editing example scripts, also include the CI-selected examples/ paths from .github/workflows/ci.yml.
black --check app explainer scene_production tests main.py
flake8 app explainer scene_production tests main.py
```

- Per-PR / `master`-push [`.github/workflows/ci.yml`](.github/workflows/ci.yml) (`test` and
  `e2e-pure` on Python 3.12 and 3.13): Runs headless tests without Manim (skipping render
  checks, plus Qt checks in `e2e-pure`) and lints core packages plus selected `examples/`
  paths listed in `.github/workflows/ci.yml` (`concept_render/` is excluded from CI lint
  targets).
- Per-PR / `master`-push `readme-quickstart` (Python 3.12 and 3.13): Installs `.[verify]`
  then `.[explainer]`, verifies example storyboards, and runs real-Manim compatibility,
  production-sidecar/handoff, voiceover-smoke (local audio fixtures rather than network
  `gTTS`), and review workflow examples, retaining public fixture artifacts.
- Other CI jobs: `.github/workflows/ci.yml` also runs `test-windows-render-supervision` and
  `package`; [`.github/workflows/nightly.yml`](.github/workflows/nightly.yml) installs
  `.[gui]` with Manim and Qt system libraries (no TeX or `manim-voiceover`) and runs all
  `tests/e2e` tiers.

Passing automated checks does not establish visual or editorial acceptance.

## File Structure

See [Architecture](#architecture) for package and submodule layouts (`app/`, `explainer/`,
`concept_render/`, `scene_production/`, and `tests/`, where `pyproject.toml` pins `pytest`
to `tests/` only; see [Tests, Formatting, and CI](#tests-formatting-and-ci)).

- `docs/`: Current guides indexed in [`docs/README.md`](docs/README.md); `docs/adr/` holds
  architecture decisions, and `docs/archive/` holds historical plans, experiments, and
  reviews.
- `examples/`: Explainer storyboard examples (e.g., `evidence-budget.story.yaml`), workflow
  examples (`silent_review/`, `composed_review/`, `revisions/`), and demo Manim scenes in
  `examples/scenes/` (`proxy_pattern_scene.py`; `test_debugging_workflow*.py` there are
  Manim scenes, not tests).
- `output/`: Conventional Git-ignored directory for rendered outputs (and GUI **Copy Command**
  fallback; CLIs require explicit output flags).
- `media/`: Manim-generated media files (`images/`, `texts/`, `videos/`), ignored by Git.
- `assets/`: Static assets such as SVG icons used in animations.
- `.venv/`: Python virtual environment, ignored by Git.
- `.vscode/`: VS Code settings (`.vscode/settings.json` points interpreter to
  `./.venv/bin/python` and configures environment activation, `flake8`, and `black`).

## Scene Development

Example scenes follow Manim patterns like `examples/scenes/proxy_pattern_scene.py`, which
demonstrates:

- Subclasses of Manim `Scene` (`ProxyScenarioBase(Scene)`, `ProxyScenario1Scene`, and
  `ProxyScenario2Scene`)
- Helper methods for consistent arrow and label creation
- Fixed coordinate positioning to prevent drift
- Explicit `z_index` management for object layering
- Structured animation sequences with clear scenarios
- ASCII identifiers and comments where applicable, with on-screen Unicode allowed in
  `Text(...)` (`proxy_pattern_scene.py` also contains legacy Unicode arrows in comments; see
  [Character Encoding](#character-encoding))

## Character Encoding

- Keep ASCII in Python identifiers, config keys, and command strings. Keep `app/**/*.py`
  strictly pure ASCII as enforced by [`tests/test_ascii_guard.py`](tests/test_ascii_guard.py).
- Rendered `Text(...)` content in scene scripts outside `app/` (such as `examples/scenes/`)
  may use on-screen Unicode (e.g., a check mark); the subprocess log reader decodes output
  with `errors="replace"`, so Unicode in Manim output cannot crash it.
- Still avoid stray Unicode punctuation in code/comments like `–` (en dash), `‑` (non-breaking hyphen), `─` (box drawing); use ASCII `-`.
