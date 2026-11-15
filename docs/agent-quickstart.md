# Agent quick start

Use `scene_production.agent` to render a trusted ordinary Manim Scene or reinspect
an already-rendered run (via its `render` receipt) from a single agent. It wraps
`scene_production.render` and `reinspect` with JSONL lifecycle events, exclusive
attempt directories, and hash-checked artifact manifests. Start here before adding
[local task coordination](agent-handoff.md). For an idea-to-delivery journey, use the
[first-use starter](first-use-starter.md). The [documentation index](README.md) also
covers concept JSON drafts and source-citing Explainer storyboards. For a runnable
render, revision, and fixture-review loop, use the
[silent review and revision walkthrough](silent-review-example.md).

## Prepare the environment and inputs

Use Python 3.12 or 3.13 from a trusted checkout or installed package. Install Manim
(`0.22.1`) using either `.[explainer]` ([Quick start](../README.md#quick-start)) or
`.[gui]` ([GUI installation](../README.md#installation)), together with `ffmpeg`,
`ffprobe`, and Manim's system libraries from [README prerequisites](../README.md#requirements).
Add optional `.[review]` (`Pillow>=11`) only if exporting labeled frame sheets. There is
no separate agent extra, and the control scene below uses no narration, network TTS, or
LaTeX.

Run module commands from the trusted checkout root (installed `scene_production.*` CLIs
also work from any directory once on `PATH`, whereas `examples.*` module commands require
the checkout root). Rendering executes Python with the caller's privileges and inherited
environment. Review scene code before executing it. To receive an external project,
follow the trusted isolated launcher procedure in [portable projects](portable-projects.md)
first.

## Agent runtime preflight

Before rendering, run the check with the same Python interpreter you will use
for production:

```sh
python -m app.environment --json
# If the scene uses default Tex/MathTex:
python -m app.environment --json --require-latex
```

Exit 0 means the version/import probes passed, 1 means a runtime blocker, and
2 means invalid CLI usage. The JSON includes `interpreter`, `blockers`,
and Manim installation argv (`recovery.manim_install_argv`) for that interpreter.
It installs nothing. If `interpreter` points to the wrong virtual environment,
activate the intended one and re-run.

The check probes for runnable `manim`, `ffmpeg`, and `ffprobe` (and `latex` plus
`dvisvgm` when `--require-latex` is set; `pdflatex` alone is insufficient). Cairo
and Pango build metadata is advisory: missing headers may prevent installation
but do not prove an existing runtime cannot render. See
[README prerequisites](../README.md#requirements) before attempting installation.

A successful probe does not verify rendering (`render_smoke_test: not_run`) or check
fonts, codecs, external assets, or custom TeX packages; run the control scene below
next.

## Prepare a control scene

Discover the machine-readable interface:

```sh
python -m scene_production.agent describe
```

`describe` emits one JSONL `terminal` event (parsed via
`scene_production.completion.read_completion(stdout, returncode, expected_operation="describe")`;
unlike `render`/`inspect`, `describe` emits no `started` event) containing protocol
rules, option defaults, and `commands.render.invocation` / `commands.inspect.invocation`
templates; see [agent protocol start](scene-agent.md#start-here).

Create a project directory of your choice. Save this as `scene.py`:

```python
from manim import BLUE, Dot, RIGHT, Scene


class Demo(Scene):
    def construct(self):
        dot = Dot(color=BLUE)
        self.add(dot)
        self.wait(1)
        self.play(dot.animate.shift(2 * RIGHT), run_time=1)
        self.wait(1)
```

Save this as `timeline.json` beside it:

```json
{"version":1,"segments":[{"label":"demo","start":0,"end":3,"samples":[0.5,1.5,2.5]}]}
```

This timeline describes expected duration and inspection times in seconds. Editing it
changes the inspection, not the animation. See [scene production](scene-production.md)
for its full format and validation limits. If you plan to follow up with traced
[scene revision](scene-revision.md) (`scene_production.revision`), add
`# scene:start <section>` / `# scene:end <section>` markers, call
`self.next_section(...)` before the first emitted frame (including any opening `wait`,
and at the start of each marked section alongside a matching revision manifest), and
pass `--record-scene-frames` on `render`.

## Render and accept the operation result

Replace the placeholder paths below (absolute paths recommended, or relative to `cwd`).
The output's parent directory must already exist; `attempt-001` itself must not exist.
Use a fresh directory for every invocation. `--quality` accepts `ql` (480p), `qm`
(720p, default), `qh` (1080p), `qp` (1440p), and `qk` (2160p); `--timeout` limits the
Manim render phase in seconds, including output draining and process cleanup (default
`120`), while `--tool-timeout` limits each `ffmpeg`/`ffprobe` inspection call (default
`30`).

```sh
python -m scene_production.agent render \
  --scene-file /absolute/project/scene.py --scene Demo \
  --timeline /absolute/project/timeline.json \
  --output-dir /absolute/existing-parent/attempt-001 \
  --quality qm --timeout 120 --tool-timeout 30
```

For a claimed render (an invocation that passed initial CLI checks and created its
fresh `--output-dir`), stdout normally contains two JSONL lines (`event: "started"`
and then `event: "terminal"`) linked by `run_token`; see the
[agent completion protocol](scene-agent.md#events-and-completion). When streaming, save
`run_token` from `started` and verify that `terminal` carries the same token (or pass
that saved token as `expected_token` to `read_completion`). A synchronous Python caller
can retain completed stdout and pass it with the exit code to `read_completion(...)`,
which correlates `started` and `terminal` automatically:

```python
import json
from pathlib import Path
import subprocess
import sys

from scene_production.completion import read_completion

output_dir = Path("/absolute/existing-parent/attempt-002")
invocation = {
    "args": [
        sys.executable, "-m", "scene_production.agent", "render",
        "--scene-file", "/absolute/project/scene.py", "--scene", "Demo",
        "--timeline", "/absolute/project/timeline.json",
        "--output-dir", str(output_dir),
        "--quality", "qm", "--timeout", "120", "--tool-timeout", "30",
    ],
    "cwd": "/absolute/trusted/ManimSimpleRender",  # Or any dir if package is installed.
    "env": None,
    "shell": False,
}
result = subprocess.run(**invocation, capture_output=True, check=False)
completion = read_completion(
    result.stdout, result.returncode, expected_operation="render"
)
if completion.status != "succeeded":
    failure_record = {
        "returncode": result.returncode,
        "status": completion.status,
        "reason": completion.reason,
        "attempt_dir": str(output_dir) if output_dir.exists() else None,
        "stdout": result.stdout.decode("utf-8", errors="replace"),
        "stderr": result.stderr.decode("utf-8", errors="replace"),
    }
    raise RuntimeError(
        f"No confirmed success: {failure_record['status']} ({failure_record['reason']}); "
        "investigate retained capture/attempt, do not retry"
    )
run_token = completion.run_token  # Captured from this stream's actual started event.
# Retain the token outside the attempt if needed for later correlation.
artifacts = completion.terminal["artifacts"]
receipt = json.loads(Path(artifacts["receipt"]["path"]).read_text(encoding="utf-8"))
print("Video:", artifacts["video"]["path"])
for frame in receipt["frames"]:
    print("Review frame:", frame["path"])
```

`read_completion` accepts `render` success only when exit `0` matches one `started`
event and one `terminal` `succeeded` event with matching `operation`, `run_token`,
`render_cleanup_state="complete"`, and valid `artifacts` manifest shape. It does not
open or rehash files on disk (in the snippet, reading `result.json` only fetches review
frame paths); see [read a captured invocation](scene-agent.md#read-a-captured-invocation)
for full `unknown`-rejection rules.

The [agent completion protocol](scene-agent.md#events-and-completion) defines
`failed` and `unknown` outcomes, including usage failures that emit no `started`
event. Investigate before starting another attempt: never automatically retry a
`failed` or `unknown` attempt (after fixing a clean exit-1 `failed` cause, launch a
new run in a fresh `--output-dir`). Never poll `result.json.status` to decide whether
rendering has finished: a `failed` receipt can be an intermediate state during
inspection.

## Review artifacts and diagnose failures

`completion.terminal["artifacts"]` provides paths and hashes for `receipt`
(`result.json`, which pairs frame paths with inspection metadata), `source`, `timeline`,
`video`, sampled frames (`frame_000`...), and optional `frame_trace` (per-section frame
mapping). Open the video and PNGs with your available viewing tools, or export
[caption-free frame evidence packages](scene-frame-samples.md#caption-free-evidence-packages)
(labeled PNG sheets without captions via `scene_production.visual_frames` and optional
`Pillow>=11` / `.[review]`). Review transitions, timing, object identity and facts;
successful machine checks do not establish visual or semantic acceptance.

Inspection checks decode validity and probe metadata, not target delivery profiles
(codec, pixel format, resolution, FPS); verify required delivery specifications
separately per [scene production](scene-production.md).

On failure, inspect `terminal["error"]`, `terminal["diagnostics"]`, and
`diagnostics.jsonl` first, then check `api.log` (where validation errors land before
`production/scene-*/` exists) and `production/scene-*/result.json` plus `render.log`
(`render` only; `inspect` never creates `render.log`). If no `terminal` event or
attempt directory exists (for example, usage errors or pre-`api.log` startup failures),
inspect captured `stdout`/`stderr` and any `attempt.json` (attempt metadata),
`diagnostics.jsonl`, or `api.log` created before failure. See
[attempt files and privacy](scene-agent.md#attempt-files-and-privacy) for the POSIX
`0700`/`0600` layout (`attempt.json`, `events.jsonl`, `diagnostics.jsonl`, and `api.log`
are all `0600` when created) and [logging and diagnostics](logging-and-diagnostics.md)
for triage. Retain partial output on failure; a video or PNG alone is not a success
result.

## Reinspect or edit

To change inspection times for an unchanged video, save a corrected timeline and
pass `--from-run` with the original render's `result.json` (`artifacts["receipt"]["path"]`,
not `attempt.json` or an earlier reinspection receipt):

```sh
python -m scene_production.agent inspect \
  --from-run /absolute/existing-parent/attempt-001/production/scene-ID/result.json \
  --timeline /absolute/project/corrected-timeline.json \
  --output-dir /absolute/existing-parent/inspection-001 \
  --tool-timeout 30
```

This creates a new attempt and does not run Manim (`--timeout` does not apply;
only `--tool-timeout` bounds `ffmpeg`/`ffprobe` checks). Pass
`expected_operation="inspect"` when validating output with `read_completion`.

Source changes require a fresh `render`; when using [scene revision](scene-revision.md)
(`scene_production.revision`), run `audit` after that fresh candidate render to apply
edit-scope checks, non-target frame comparisons, and retained rollback versions. To
bind explicit findings to delivered video bytes (`record`) or compare revised video
bytes (`compare`), use [editorial review](editorial-review.md)
(`scene_production.editorial`), or run `python -m examples.silent_review.run` from
the checkout root ([silent review walkthrough](silent-review-example.md)).

## Interpret each command's result

Exit codes are specific to each interface; there is no shared approval code:

- `scene_production.agent describe` / `render` / `inspect`: exit `0` is only part of
  the [completion rule](scene-agent.md#events-and-completion). `describe` emits one
  `terminal` event (exits `0` or `2`); `render` and `inspect` require matching
  `started` and `terminal` event evidence as above. Exit `1` reports failure; exit `2`
  is invalid arguments; exit `3`, `130`, or `143` reports an unknown outcome. Investigate
  incomplete or conflicting stdout/exit-code transport evidence before starting another
  run rather than assuming failure is safe to retry.
- `scene_production.editorial record` / `compare`: exit `0` means the requested
  operation completed, even for `changes_requested`, `needs_review`, changed
  candidate bytes, or integrity flags equal to `false`. Read the JSON; handled
  input/file errors and usage errors exit `2`. See
  [editorial command results](editorial-review.md#command-results-and-exit-codes).
- `scene_production.revision audit`: exit `0` (`unchanged_non_target`) means non-target
  native/raw/decoded checks and baseline/candidate inspection-timeline alignment passed
  across segments; exit `2` with a JSON `needs_review` result means non-target checks
  or inspection-timeline alignment found differences. Handled safety/scope guard and
  invalid-evidence failures exit `1`; argument parsing errors also exit `2` without
  producing an audit result. See
  [revision evidence](scene-revision.md#optional-observed-frame-evidence).
- [`examples.silent_review.run`](silent-review-example.md): exit `0` means
  `unchanged_non_target` audit, exit `2` means `needs_review` audit or argument error,
  and exit `1` means an uncaught exception (retaining existing output).

## Add optional capabilities

- Use [caption sidecars](scene-captions.md) (`scene_production.captions` Python API),
  [existing WAV previews](scene-audio.md) (`scene_production.audio` Python API), and
  [frame evidence and caption-linked samples](scene-frame-samples.md)
  (`scene_production.visual_frames` CLI) after a successful run.
- Use [portable projects](portable-projects.md) to transfer selected editable inputs.
- Use [local handoff](agent-handoff.md) (`scene_production.handoff` CLI) when multiple
  workers need durable task dependencies, claims and recovery. Start with
  `python -m scene_production.handoff describe`. Callers supply tasks, verify actual
  user authorization and operate the consumer loop; this interface does not launch
  agents or deliver external messages.

## Request 1080p30 explicitly

Replace `--quality qm` with `--quality qh` and add `--fps 30` in the
[CLI render command above](#render-and-accept-the-operation-result) (or update
`invocation["args"]` in Python) to request `1920x1080` at 30 fps. The direct non-agent
Python API (no JSONL completion events or attempt directories; see
[scene production](scene-production.md)) is
`scene_production.render(..., quality="qh", fps=30)`. Invalid FPS options fail before
creating an attempt directory (under `scene_production.agent`) or production run
directory. `inspect` does not render or accept `--fps`.

`requested_fps` in events and receipts records requested intent only: trusted scene or
config code may supersede it, and setting `--fps` does not guarantee constant frame rate
(CFR), exact duration, or downstream helper eligibility (for example, legacy
caption-linked sampling caps at `1280x720`). Inspect observed probe/trace metadata,
and see [explicit requested frame rate](scene-production.md#explicit-requested-frame-rate),
[optional requested frame rate](scene-agent.md#optional-requested-frame-rate),
[frame sample limits](scene-frame-samples.md#supported-local-inputs-and-limits), and
[WAV audio previews](scene-audio.md) for valid integer rates (`1`-`240`) and helper
budgets.

## Videos assembled after rendering

If your final video adds footage after Manim rendering, reviewing the raw rendered
scene does not cover the final composite. See the
[synthetic composition example](composed-review-example.md) for separate identities,
assembly provenance, protected moving-footage comparisons, and final-video review
records.
