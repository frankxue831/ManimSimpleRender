# Agent quick start

Use `scene_production.agent` to render a trusted ordinary Manim Scene or reinspect
a recorded video from a single agent. It wraps the existing production APIs with
JSONL events and retained artifacts. Start here before adding local task coordination.
The [documentation index](README.md) also covers concept JSON drafts and source-citing
Explainer storyboards. For a runnable render, record, fix and compare loop, use the
[silent review and revision walkthrough](silent-review-example.md). Its fixture
finding and machine audit do not replace an actual editorial review.

## Prepare the environment and inputs

Use Python 3.12 or 3.13 from a trusted checkout or installed product. Rendering needs
Manim and its system prerequisites, plus `ffmpeg` and `ffprobe` on PATH. Follow the
[installation instructions](../README.md#quick-start); either the existing `.[gui]`
or `.[explainer]` extra supplies Manim. The ordinary Scene below uses no narration,
network TTS or LaTeX text. No separate agent extra is required.

Run module commands from the trusted checkout root. Rendering executes Python with
the caller's privileges and inherited environment. Review scene code before executing
it. To receive an external project, follow the trusted isolated launcher procedure
in [portable projects](portable-projects.md) first.

Discover the machine-readable interface:

```sh
python -m scene_production.agent describe
```

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

This timeline describes expected duration and inspection times. Editing it changes
the inspection, not the animation. See [scene production](scene-production.md) for
its full format and validation limits.

## Render and accept the operation result

Replace absolute paths below. The output's parent directory must already exist;
`attempt-001` itself must not exist. Use a fresh directory for every invocation.

```sh
python -m scene_production.agent render \
  --scene-file /absolute/project/scene.py --scene Demo \
  --timeline /absolute/project/timeline.json \
  --output-dir /absolute/existing-parent/attempt-001 \
  --quality qm --timeout 120 --tool-timeout 30
```

For a claimed render, stdout normally contains `started` and then one `terminal`
event. Save the invocation's token when `started` arrives and correlate the terminal
with it. A synchronous Python caller can retain the completed stream and apply the
same rule after process exit:

```python
import json
from pathlib import Path
import subprocess
import sys

from scene_production.completion import read_completion

invocation = {
    "args": [
        sys.executable, "-m", "scene_production.agent", "render",
        "--scene-file", "/absolute/project/scene.py", "--scene", "Demo",
        "--timeline", "/absolute/project/timeline.json",
        "--output-dir", "/absolute/existing-parent/attempt-002",
        "--quality", "qm", "--timeout", "120", "--tool-timeout", "30",
    ],
    "cwd": "/absolute/trusted/ManimSimpleRender",
    "env": None,
    "shell": False,
}
result = subprocess.run(**invocation, capture_output=True, check=False)
# Keep the stdout/stderr capture and observed returncode for this invocation.
completion = read_completion(
    result.stdout, result.returncode, expected_operation="render"
)
if completion.status != "succeeded":
    raise RuntimeError(
        f"No confirmed success: {completion.status} ({completion.reason}); investigate, do not retry"
    )
run_token = completion.run_token  # Captured from this stream's actual started event.
# Retain the token outside the attempt if needed for later correlation.
artifacts = completion.terminal["artifacts"]
receipt = json.loads(Path(artifacts["receipt"]["path"]).read_text(encoding="utf-8"))
print("Video:", artifacts["video"]["path"])
for frame in receipt["frames"]:
    print("Review frame:", frame["path"])
```

This example accepts success only after exit 0, exactly one `started` followed by
one successful terminal, and matching operation and invocation token. The pure
reader rejects incomplete JSONL, duplicate keys and ambiguous protocol evidence
as unknown. It checks the reported manifest's shape but does not read files or
rehash artifacts. Reading the receipt above is for locating review frames, not
another completion or integrity check. The [agent protocol](scene-agent.md#events-and-completion)
defines failed and unknown outcomes, including usage failures that emit no `started`.
Keep the recorded outcome when handling them; the example's exception only prevents
unconfirmed success from being consumed. Do not automatically retry an unknown attempt.
Never poll `result.json.status` to decide that rendering has finished: a `failed`
receipt can be an intermediate state during inspection.

## Review artifacts and diagnose failures

The terminal's `artifacts` map provides paths and hashes for the checked receipt,
source snapshot, timeline, video and sampled frames. Open the video and PNGs with
your available viewing tools. Review transitions, timing, object identity and facts;
successful machine checks do not establish visual or semantic acceptance. Frame paths
are also paired with inspection metadata in the production receipt.

Inspection does not enforce delivery requirements such as H.264 High profile,
`yuv420p`, 1280x720 or 30 fps. Check any required output specification separately;
see the [production inspection limits](scene-production.md).

The attempt directory contains `attempt.json`, `events.jsonl`, private `api.log`,
and diagnostics when available. Its `production/scene-*/` child retains the ordinary
run, including `result.json` and `render.log`. Read the event's structured error and
diagnostics first, then use [logging and diagnostics](logging-and-diagnostics.md).
Retain partial output on failure; a video or PNG alone is not a success result.

## Reinspect or edit

To change inspection times for an unchanged video, save a corrected timeline and
use the original render receipt from `artifacts["receipt"]["path"]`:

```sh
python -m scene_production.agent inspect \
  --from-run /absolute/existing-parent/attempt-001/production/scene-ID/result.json \
  --timeline /absolute/project/corrected-timeline.json \
  --output-dir /absolute/existing-parent/inspection-001 \
  --tool-timeout 30
```

This creates a new attempt and does not run Manim. Supply the original render's
receipt, not `attempt.json` or an earlier reinspection receipt. Source changes need
a fresh render; [scene revision](scene-revision.md) adds explicit edit scope,
non-target frame comparisons and retained rollback versions for silent productions.

To retain explicit feedback against delivered video bytes, use
[editorial review](editorial-review.md). The [silent review walkthrough](silent-review-example.md)
connects a baseline render, recorded finding, targeted source edit, full candidate
render, non-target audit and a fresh candidate record. The candidate remains
`needs_review`: an author's `--addresses F1` claim does not resolve finding `F1`.

## Interpret each command's result

Exit codes are specific to each interface; there is no shared approval code:

- `scene_production.agent render` / `inspect`: exit 0 is only part of the
  [completion rule](scene-agent.md#events-and-completion). Require matching
  successful event evidence as above. Exit 1 reports failure; exit 2 is a usage
  failure; exit 3, 130 or 143 reports an unknown outcome. Investigate incomplete
  or conflicting transport evidence rather than assuming failure is safe to retry.
- `scene_production.editorial record` / `compare`: exit 0 means the requested
  operation completed, even for `changes_requested`, `needs_review`, changed
  candidate bytes or false integrity flags. Read the JSON; handled input/file
  errors and usage errors exit 2. See [editorial command results](editorial-review.md#command-results-and-exit-codes).
- `scene_production.revision audit`: exit 0 means unchanged non-target machine
  layers; exit 2 with a JSON `needs_review` result means the audit needs review.
  Handled invalid evidence/guard failures exit 1; argument parsing errors also
  exit 2 but do not produce an audit result. See [revision evidence](scene-revision.md#optional-observed-frame-evidence).
- `examples.silent_review.run`: exit 0 means the example completed with an
  unchanged non-target audit. Exit 2 with a result means the completed audit
  needs review; argument parsing errors also exit 2. An uncaught workflow exception
  exits 1 and retains existing output for investigation. Even at exit 0,
  its fixture assessment remains `needs_review`, never approval.

## Add optional capabilities

- Use [caption sidecars](scene-captions.md), [existing WAV previews](scene-audio.md)
  and [caption-linked frame samples](scene-frame-samples.md) after a successful run.
- Use [portable projects](portable-projects.md) to transfer selected editable inputs.
- Use [local handoff](agent-handoff.md) when multiple workers need durable task
  dependencies, claims and recovery. Start with `python -m scene_production.handoff describe`.
  Callers supply tasks, verify actual user authorization and operate the consumer loop;
  this interface does not launch agents or deliver external messages.

## Request 1080p30 explicitly

Append `--quality qh --fps 30` to the render command above to request 1920×1080 at
30fps. The public Python equivalent is `render(..., quality="qh", fps=30)`.
FPS is optional: omission or Python `None` preserves the preset. Python accepts
only integers from 1 to 240, not booleans, floats or strings; CLI accepts integer
tokens in the same range. Invalid FPS is rejected before the attempt is created.

`requested_fps` records intent only. Trusted scene/config changes may supersede
it; verify observed probe/trace data. The option does not guarantee CFR, exact
duration, semantic acceptance or delivery, and does not expand downstream helper
limits. Reinspection does not render or accept a new FPS option.
