# ManimSimpleRender

Home of the **Explainer Engine**: turn a short storyboard YAML that cites real source lines into a
narrated, animated Manim video. Before anything is rendered, a citation gate checks that the
cited source lines still contain the evidence the storyboard declares for them (as whole words,
with some on the first and the last cited line), and stops the render if any do not. The gate
checks citations, not narration. The repo also contains a PyQt5 GUI for batch-rendering Manim
scenes (see [Desktop GUI](#desktop-gui) below).

![Animated plot: a green token budget starts at a floor of 5, rises with lines cited, and levels off at 24, above a simplified two-line version of the code that computes it](assets/evidence-budget.gif)

*A clip from `examples/evidence-budget.story.yaml`, rendered by the Explainer Engine. Full video
and write-up: <https://frankxue.dev/projects/explainer-engine/>.*

## Quick start

```bash
git clone https://github.com/frankxue831/ManimSimpleRender.git
cd ManimSimpleRender
python3.12 -m venv .venv && source .venv/bin/activate   # Python 3.12 or 3.13
pip install -e ".[explainer]"
explainer verify examples/evidence-budget.story.yaml
explainer render examples/evidence-budget.story.yaml -o out.mp4
```

`verify` runs the citation gate, then checks that the code generator accepts the storyboard; it
only reads files, does not render or call TTS, and needs no network. `render` additionally needs:

- **Manim's system prerequisites** (ffmpeg, LaTeX, cairo/pango), which `pip` does not install;
  see [Requirements](#requirements).
- **Network access for narration.** The only supported voice is `gtts` (manim-voiceover's
  Google Text-to-Speech service, a live Google endpoint). No API key or credential is needed,
  and there is no offline TTS fallback: synthesis is retried with backoff, then the render fails.
  Audio is cached by narration text, one folder per language, under
  `~/.cache/explainer/voiceovers` (override with `EXPLAINER_TTS_CACHE`), so a re-render of
  unchanged narration reuses the cache.

**Reproducibility.** `examples/evidence-budget.story.yaml` is the storyboard behind the video
above. The storyboard for the harness-field explainer on the project page is not public and is
not in this repository.

## Explainer and concept-render tools

These are separate command-line tools in the same repo. They need the Manim system
prerequisites (see [Requirements](#requirements)) for any step that renders video.

### Explainer (`explainer/`)

Install with `pip install -e ".[explainer]"` (adds PyYAML and manim-voiceover). This installs
the `explainer` command (equivalent: `python -m explainer.cli`). It shares the Qt-free render
core with the GUI; see `docs/adr/0001-render-core-boundary.md` for that boundary.

```bash
# Check a storyboard's citations against the source repo it cites, then generate its
# Manim scene code (no render, no TTS)
explainer verify examples/evidence-budget.story.yaml

# Generate, verify, and render a storyboard to an mp4
explainer render examples/evidence-budget.story.yaml -o out.mp4 [--quality ql|qm|qh|qp|qk|1080p]

# Draft a storyboard from real code with an LLM
explainer plan --repo . --focus PATH[:START-END] --topic "what to explain" -o draft.story.yaml \
  [--provider grok|anthropic] [--beats 4] [--revisions 1] [--dry-run]
```

`--focus` is repeatable. `--dry-run` prints the prompt and exits without calling a model.
Run `explainer plan --help` for the remaining options (`--model`, `--effort`, `--max-tokens`,
`--max-repairs`, `--allow-dirty`).

Planner providers and environment variables:

- `--provider grok` (the default) runs the `grok` CLI as a subprocess. It must be on `PATH`,
  or set `EXPLAINER_GROK_BIN` to the binary's path.
- `--provider anthropic` needs `pip install -e ".[planner]"` and an Anthropic API key, read by
  the SDK from `ANTHROPIC_API_KEY` (or an `ant` profile).
- `EXPLAINER_TTS_CACHE` (optional) sets the narration cache directory used by `render`.

### Concept render (`concept_render/`)

Renders a silent concept draft from a JSON input (max 64 KiB) and prints a JSON receipt.
Run it from a source checkout; it needs no API key. See `docs/concept-render.md` for the
input format.

```bash
python -m concept_render --input examples/concepts/team_growth.json --output-dir output/concepts --timeout 60
python -m concept_render.revision --original original.json --candidate candidate.json
```

`--timeout` is in seconds, defaults to 60, and must be greater than 0 and at most 120.

## Desktop GUI

A PyQt5 desktop GUI for discovering and batch-rendering [Manim](https://www.manim.community/)
scenes. Point it at a folder of Manim scripts, pick the scenes you want, choose a resolution
and engine, and it renders them via the `manim` CLI in a background thread with live logs and
per-scene results.

### Features

- **Automatic scene discovery** — AST-based, import-free scanning of a folder's `*.py` files;
  no need to list scene classes by hand (see `app/scenes/discovery.py`).
- **Batch rendering** off the UI thread, with a progress bar, streaming log, cancellation, and
  per-scene pass/fail decoration.
- **Resolution + engine selection** (`-ql`/`-qm`/`-qh`/`-qk`; cairo or OpenGL).
- Remembers your last folders, resolution, and engine (via `QSettings`).
- Dark theme.

### Requirements

- **Python 3.12 or 3.13** (both install numpy 2.x)
- **Manim 0.20.1** and its **system prerequisites**, which `pip` does *not* install:
  **ffmpeg**, a **LaTeX** distribution, and **cairo/pango**. See the
  [Manim installation guide](https://docs.manim.community/en/stable/installation.html) for
  per-OS instructions. (Without these, `pip install` succeeds but renders fail at runtime.)

> Tip: click **Check Environment** in the app to verify that manim, ffmpeg, and LaTeX are
> detected in the interpreter that will run the render, before you start a batch.

### Installation

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

### Usage

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

### Standalone app (advanced)

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
black --check app explainer tests main.py    # formatting (same as CI)
flake8 app explainer tests main.py           # linting (same as CI)
```

CI (GitHub Actions) runs the same checks on every push and pull request. The architecture is
layered — UI → controller → services (config, scenes, environment, render, validation); services
are Qt-free and unit-tested headless. See `AGENTS.md` for the architecture overview.

## License

[Apache License 2.0](LICENSE).
