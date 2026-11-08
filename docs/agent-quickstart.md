# Agent quick start

Use `scene_production.agent` to render a trusted ordinary Manim Scene or reinspect
a recorded video from a single agent. It wraps the existing production APIs with
JSONL events and retained artifacts. Start here before adding local task coordination.
The [documentation index](README.md) also covers concept JSON drafts and source-citing
Explainer storyboards.

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
result = subprocess.run(**invocation, capture_output=True, text=True, check=False)
# Malformed or truncated JSONL must stop processing; never guess a result or retry.
events = [json.loads(line) for line in result.stdout.splitlines()]
started = [event for event in events if event.get("event") == "started"]
terminal = [event for event in events if event.get("event") == "terminal"]
if len(started) != 1 or len(terminal) != 1:
    raise RuntimeError("No confirmed success; inspect this invocation before further work")
run_token = started[0]["run_token"]  # Retain outside the attempt if needed later.
if (
    result.returncode != 0
    or terminal[0].get("status") != "succeeded"
    or any(event.get("run_token") != run_token for event in events)
):
    raise RuntimeError("No confirmed success; inspect terminal status and diagnostics")
artifacts = terminal[0]["artifacts"]
receipt = json.loads(Path(artifacts["receipt"]["path"]).read_text(encoding="utf-8"))
print("Video:", artifacts["video"]["path"])
for frame in receipt["frames"]:
    print("Review frame:", frame["path"])
```

This example accepts success only after exit 0, exactly one successful terminal and
a matching invocation token. The [agent protocol](scene-agent.md#events-and-completion)
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
  --from-run /absolute/attempt-001/production/scene-ID/result.json \
  --timeline /absolute/project/corrected-timeline.json \
  --output-dir /absolute/existing-parent/inspection-001 \
  --tool-timeout 30
```

This creates a new attempt and does not run Manim. Supply the original render's
receipt, not `attempt.json` or an earlier reinspection receipt. Source changes need
a fresh render; [scene revision](scene-revision.md) adds explicit edit scope,
non-target frame comparisons and retained rollback versions for silent productions.

## Add optional capabilities

- Use [caption sidecars](scene-captions.md), [existing WAV previews](scene-audio.md)
  and [caption-linked frame samples](scene-frame-samples.md) after a successful run.
- Use [portable projects](portable-projects.md) to transfer selected editable inputs.
- Use [local handoff](agent-handoff.md) when multiple workers need durable task
  dependencies, claims and recovery. Start with `python -m scene_production.handoff describe`.
  Callers supply tasks, verify actual user authorization and operate the consumer loop;
  this interface does not launch agents or deliver external messages.
