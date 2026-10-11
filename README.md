# ManimSimpleRender

A PyQt5 desktop GUI for discovering and batch-rendering [Manim](https://www.manim.community/)
scenes. Point it at a folder of Manim scripts, pick the scenes you want, choose a resolution
and engine, and it renders them via the `manim` CLI in a background thread with live logs and
per-scene results.

## Features

- **Automatic scene discovery** — AST-based, import-free scanning of a folder's `*.py` files;
  no need to list scene classes by hand (see `app/scenes/discovery.py`).
- **Batch rendering** off the UI thread, with a progress bar, streaming log, cancellation, and
  per-scene pass/fail decoration.
- **Resolution + engine selection** (`-ql`/`-qm`/`-qh`/`-qk`; cairo or OpenGL).
- Remembers your last folders, resolution, and engine (via `QSettings`).
- Dark theme.

## Requirements

- **Python 3.12 or 3.13** (both install numpy 2.x)
- **Manim 0.20.1** and its **system prerequisites**, which `pip` does *not* install:
  **ffmpeg**, a **LaTeX** distribution, and **cairo/pango**. See the
  [Manim installation guide](https://docs.manim.community/en/stable/installation.html) for
  per-OS instructions. (Without these, `pip install` succeeds but renders fail at runtime.)

> Tip: click **Check Environment** in the app to verify that manim, ffmpeg, and LaTeX are
> detected in the interpreter that will run the render, before you start a batch.

## Installation

```bash
# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate

# Install runtime dependencies
pip install -r requirements.txt
```

Alternatively, install the package itself to get the `manim-simple-render` launch command:

```bash
pip install .
```

## Usage

```bash
python main.py            # from a source checkout
# or, if installed as a package:
manim-simple-render
```

Then in the window:

1. Choose an **input folder** containing your Manim `.py` scripts (scenes are discovered
   automatically) and an **output folder** (created if it doesn't exist).
2. Select one or more **scenes**, a **resolution**, and a **render engine**.
3. Click **Start Render**. Progress, logs, and per-scene results appear live; **Cancel** stops
   an in-flight batch.

> Note: rendering executes the selected Python files via Manim. Only point the app at scripts
> you trust.

> Engine tip: prefer the **Default** (cairo) engine. The **OpenGL** engine uses a different
> object model and is not reliable for every scene/platform (e.g. some scenes that render fine
> under cairo raise errors under OpenGL on macOS). If a render fails only under OpenGL, switch
> back to Default — a failed render is shown red per-scene with the error in the log.

## Standalone app (advanced)

You can package the **GUI** as a clickable desktop app with
[PyInstaller](https://pyinstaller.org/) so end users don't need a Python environment.
By design this bundles the GUI only — the render toolchain (**manim, ffmpeg, LaTeX**)
stays **external** and must be installed and on `PATH` (use **Check Environment** in the
app to confirm). The app detects the frozen build and invokes the external `manim`
instead of `python -m manim` automatically.

```bash
pip install pyinstaller
pyinstaller --noconfirm --windowed --name ManimSimpleRender \
  --add-data "app/config/config.v2.json:app/config" \
  --add-data "app/ui/theme.qss:app/ui" \
  main.py
# On Windows, use ';' instead of ':' in --add-data.
```

The bundle appears under `dist/`. This is a per-OS maintainer/release step (build and
test on each target platform); the packaged data files (`config.v2.json`, `theme.qss`)
resolve inside the bundle automatically. It is intentionally not produced by CI.

## Development

```bash
pip install -r requirements-dev.txt          # adds black + flake8
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests   # run the test suite headless
black --check app tests main.py              # formatting
flake8 app tests main.py                     # linting
```

CI (GitHub Actions) runs the same checks on every push and pull request. The architecture is
layered — UI → controller → services (config, scenes, environment, render, validation); services
are Qt-free and unit-tested headless. See `CLAUDE.md` for the architecture overview.

The repository also hosts `explainer/`, a separate CLI vertical (`explainer verify|render|plan`)
that shares the Qt-free render core with the GUI — see
`docs/adr/0001-render-core-boundary.md` for the boundary and its extraction trigger. Install its
extras with `pip install -e ".[explainer]"` (plus `[planner]` for the LLM planner).

Try the citation gate on the shipped example, which cites this repository's own source:

```bash
explainer verify examples/evidence-budget.story.yaml
```

## License

[Apache License 2.0](LICENSE).
