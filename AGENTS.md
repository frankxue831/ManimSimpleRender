# AGENTS.md

This file provides guidance to coding agents (Codex, Claude Code, and others) working in this repository. `CLAUDE.md` only imports this file (`@AGENTS.md`), so Claude Code reads the same guide.

## Project Overview

ManimSimpleRender holds two products. The Explainer Engine (`explainer/`) is a CLI that compiles storyboard YAML through a citation gate into deterministic Manim codegen and a narrated video. The desktop GUI (`app/`) is a PyQt5 application that discovers the Manim scenes in a folder of Python files and batch-renders the ones the user selects.

The two verticals share one Qt-free render core (`app/render`), per `docs/adr/0001-render-core-boundary.md`. `explainer/` may import only `app.render.*` (never `app.render.worker`); nothing imports back from it. The boundary is enforced by `tests/test_architecture.py`. `concept_render/`, a smaller source-checkout-only CLI for silent concept drafts (`docs/concept-render.md`), also imports `app.render`; `tests/test_architecture.py` does not scan it.

## Architecture

The application is a layered PyQt5 GUI under the `app/` package. Dependency
direction: UI -> controller -> services (config, scenes, environment, render, validation).
Services never import the UI; only the render worker imports Qt.

- **main.py**: Thin entry point; delegates to `app/bootstrap.py`.
- **app/bootstrap.py**: Creates QApplication, loads + validates config (falling back to built-in defaults if the packaged config is corrupt), wires MainView + RenderController, runs the event loop.
- **app/config/**: `models.py` (typed dataclasses + `ConfigError`), `loader.py` (`load_config`/`load_config_or_default` with validation + built-in defaults), packaged `config.v2.json`.
- **app/scenes/discovery.py**: AST-based discovery of renderable Manim `Scene` subclasses (import-free; `RECOGNIZED_SCENE_BASES` allowlist). `discover_scene_result()` returns one immutable snapshot (scenes + skipped-file diagnostics from the same parse pass); the controller fails closed on discovery issues.
- **app/environment/**: `doctor.py` (`check_environment` + per-tool probes for python/manim/ffmpeg/latex, injectable runners), `models.py` (typed check/report models). Drives the GUI's Check Environment button.
- **app/render/**: `jobs.py` (`RenderJob` + `build_jobs` + `media_output_collisions`), `command_builder.py` (pure manim argv), `runner.py` (Qt-free subprocess), `process_session.py` (bounded, Qt-free supervision of one render subprocess: process-group spawn, terminate->kill escalation), `worker.py` (QThread adapter), `aggregation.py` (`RenderSummary`), `quality.py` (canonical manim quality ladder; see `docs/adr/0001-render-core-boundary.md`).
- **app/ui/**: `main_view.py` (passive window), `sections.py`, `theme.py` + `theme.qss` (external dark theme), `controller.py` (state machine + QSettings).
- **app/validation.py**: Pure pre-render validation.
- **explainer/**: `storyboard/` (YAML IR + loader), `cite/` (citation gate: checks that the cited lines contain the evidence the storyboard declares, as whole words, with evidence on the first and last cited lines; if the storyboard names a commit, also that HEAD matches it and the tree is clean unless `allow_dirty`), `codegen/` (deterministic Manim scene emission, diagram canvas), `plan/` (LLM planner drafting storyboards from real code; providers behind a fake-able protocol), `render/adapter.py` (the only cross-package edge, into `app.render`), `cli.py` (`explainer verify|render|plan`).
- **tests/**: Headless unit tests plus `tests/e2e/` (fake-manim GUI/worker tiers + a real-render smoke that CI runs nightly), `tests/explainer_tests/`, `tests/support/` (shared fixtures), and `tests/test_architecture.py` (executable ADR 0001 boundary).

## Key Components

### UI Layer (app/ui/)
- `MainView` (main_view.py): passive QMainWindow — widgets, setter methods, and intent signals only; holds no render logic.
- `RenderController` (controller.py): mediates view <-> services; owns the worker lifecycle and a small idle/running/cancelling state machine (validation runs inline while idle); persists last folders/resolution/engine via QSettings.
- Dark theme is an external stylesheet (`theme.qss`); the cancel state is a dynamic property (`QPushButton[state="cancel"]`).

### Render Engine (app/render/)
- `RenderWorker` (worker.py): QThread that iterates explicit `(file, scene_class)` jobs and streams status/log/progress + a final `RenderSummary` via signals.
- `RenderRunner` (runner.py): Qt-free subprocess execution — merged stdout/stderr (no deadlock), cooperative cancellation via `threading.Event`, terminate->kill escalation, distinct `MANIM_NOT_FOUND` outcome.
- A render job is one `(file, scene_class)` pair (no file x scene cross-product).

### Configuration System (app/config/)
- Typed config v2 (`app/config/config.v2.json`): `resolutions` as `{label, flag}` objects, `engines` as `{label, renderer_flag}` objects (no parallel arrays). Scenes are discovered from the input folder at runtime, not stored in config.
- Resolutions map to manim quality flags; the valid set -ql/-qm/-qh/-qp/-qk has a single source of truth (`app/render/quality.py`), and the packaged config currently offers four of the five (no 1440p/-qp entry). `renderer_flag` is `""` (cairo/Default) or `"--renderer=opengl"` (OpenGL) in the packaged config; the loader also accepts `"--renderer=cairo"`.

## Common Commands

### Running the Application
```bash
python main.py
```

### Installing Dependencies
```bash
# Using virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### VS Code Setup
The project includes VS Code configuration in `.vscode/settings.json`:
- Python interpreter points to `./.venv/bin/python`
- Automatic virtual environment activation
- Linting and formatting settings

### Manual Manim Rendering
The application generates commands like:
```bash
manim input_file.py SceneName -qh --media_dir output_folder
```

### Tests, Formatting, Lint
```bash
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests
black --check app explainer tests main.py
flake8 app explainer tests main.py
```
Fast CI (`.github/workflows/ci.yml`) runs these on py3.12 + py3.13 without manim installed (services are Qt-free-testable; explainer tests skip their one manim-touching check). The nightly workflow installs the full render toolchain and runs the real-render e2e smoke.

### Explainer CLI
```bash
pip install -e ".[explainer]"   # PyYAML + manim-voiceover; add [planner] for the LLM planner
explainer verify <storyboard>                    # citation gate only
explainer render <storyboard> -o out.mp4         # verify + deterministic codegen + narrated mp4 (--quality ql..qk)
explainer plan --repo R --focus PATH:START-END --topic "..." -o sb.yaml   # LLM-drafted storyboard (needs [planner]; provider grok|anthropic)
```
Example storyboard: `examples/evidence-budget.story.yaml` (cites this repo's own `explainer/plan/evidence.py`, so `explainer verify` works from any checkout).

## Development Notes

- The application uses subprocess to execute Manim commands
- All rendering happens in background threads to prevent UI freezing
- Configuration is loaded from `config.v2.json` at startup
- Media files are organized in `media/` directory with separate folders for images, texts, and videos
- The UI validates folder paths and scene selections before starting renders

## File Structure

- `app/`: Application package — `bootstrap.py`, `config/`, `environment/`, `scenes/`, `render/`, `ui/`, `validation.py`
- `explainer/`: The CLI vertical — `storyboard/`, `cite/`, `codegen/`, `plan/`, `render/`, `cli.py`
- `tests/`: Headless unit tests. Run: `python -m unittest discover -s tests` (the window smoke test needs `QT_QPA_PLATFORM=offscreen`)
- `docs/`: `adr/` (architecture decision records) and `concept-render.md`
- `examples/`: Explainer storyboard examples (e.g., `evidence-budget.story.yaml`)
- `examples/scenes/`: Demo Manim scenes for the GUI (e.g., `proxy_pattern_scene.py`). `test_debugging_workflow*.py` there are Manim scenes, not tests; pytest is pointed at `tests/` only
- `output/`: Default output directory for rendered videos
- `media/`: Manim-generated media files (images, texts, videos) - ignored by git
- `assets/`: Static assets like SVG icons used in animations
- `.venv/`: Python virtual environment - ignored by git
- `.vscode/`: VS Code configuration with Python interpreter settings

## Scene Development

Example scenes should follow Manim patterns like `examples/scenes/proxy_pattern_scene.py` which demonstrates:
- Proper Scene class inheritance
- Helper methods for consistent arrow and label creation
- Fixed coordinate positioning to prevent drift
- Z-index management for proper layering
- Structured animation sequences with clear scenarios
- Standard ASCII characters (avoid Unicode characters that cause encoding issues)

## Common Issues & Solutions

### Character Encoding
- Keep ASCII in Python identifiers, config keys, and command strings.
- On-screen Unicode in rendered `Text(...)` content is permitted (e.g. a check mark); the subprocess log path decodes with `errors='replace'`, so Unicode in Manim output cannot crash the reader.
- Still avoid stray Unicode punctuation in code/comments like `–` (en dash), `‑` (non-breaking hyphen), `─` (box drawing); use ASCII `-`.