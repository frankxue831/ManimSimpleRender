# Silent concept rendering

Run these commands from the source checkout using its existing Python and Manim environment. The selected Python must already support the application's render modules and Manim, including its existing text/font and rendering requirements. Inspection additionally requires existing `ffprobe` and `ffmpeg` executables on PATH. These commands do not install anything or use the GUI. No installed package entry point is provided.

Render command: `python -m concept_render --input concept.json --output-dir output/concepts --timeout 60`

The timeout defaults to 60 seconds and must be a finite number greater than zero and at most 120. The Python API is `concept_render.render_adapter.render(input_path, output_dir, timeout=60, runner=None)`. Boolean timeouts are rejected. Input validation completes before creating the output run directory or calling the runner.

## Input grammar

Input is a UTF-8 JSON object of at most 64 KiB. Every object must contain exactly its required fields. Unicode display text is accepted; text is embedded as Python literals and rendered with `Text`, never evaluated as code.

- Root fields: `version`, `title`, `font`, `nodes`, `edges`, `steps`. Version must be integer 1. Title is 1-100 characters; font is 1-80 characters.
- `nodes`: 1-8 objects with `id`, `label`, `x`, `y`. IDs are unique ASCII identifiers matching `[A-Za-z_][A-Za-z0-9_]{0,31}`. Labels are 1-60 characters. Coordinates are finite numbers, with x in [-5, 5] and y in [-2.5, 2.5]. Booleans are not numbers.
- `edges`: 0-12 objects with `from` and `to`, referencing distinct known node IDs. Directed pairs cannot repeat.
- `steps`: 1-12 objects with `show`, `highlight`, `caption`, `duration`. Each reference list contains 0-8 unique known IDs. A node can be shown only once. Highlights must reference nodes visible by that step, including newly shown nodes. Captions are 1-120 characters. Each duration is a finite non-boolean number in [1, 10] seconds; total duration must be in [10, 30] seconds.

Minimal input: `{"version":1,"title":"A concept","font":"Arial","nodes":[{"id":"a","label":"Start","x":0,"y":0}],"edges":[],"steps":[{"show":["a"],"highlight":["a"],"caption":"Introduce the concept","duration":10}]}`

## Outputs and failures

Each invocation creates a unique `concept-` subdirectory. Earlier runs are preserved. It saves the original bytes as `input.json`, generated `scene.py`, streamed `render.log`, and `result.json`. Generation preserves a 16:9 logical frame and leaves pixel resolution to Manim's `-ql` setting. Labels have a higher z-index than opaque boxes, including during subsequent box highlights.

On success, stdout contains the receipt JSON: `status`, absolute `video`, absolute `scene`, `input_sha256`, `requested_font`, `steps`, absolute `log`, and `return_code`. Step indices are zero based. Start and end times use cumulative input durations without display rounding; these are planned times, not measurements. The input hash covers the original bytes, including whitespace.

Success requires an unset cancellation event, `JobOutcome.SUCCESS`, return code zero, and exactly one nonempty final `ConceptScene.mp4` under the run's media directory. Files under `partial_movie_files` are excluded. A timer requests cancellation at the deadline; the existing runner supervises termination with a one-second terminate grace period. Process cleanup can finish after the deadline. The timer is cancelled and joined even if execution raises.

Once a run directory exists, failures write a receipt with `status: failed`, `error`, `stage`, and the absolute log path, then raise an error. Invalid input creates no run directory. Filesystem failures that prevent receipt persistence are explicitly reported. CLI failures print a concise stderr diagnostic and exit nonzero.

## Measured video inspection

Inspection command: `python tools/verify_concept_video.py --result /absolute/path/to/concept-run/result.json`

The tool requires a success receipt and a nonempty video. It runs `ffprobe` with a finite timeout to measure duration and rejects missing, malformed, nonpositive or nonfinite results. It checks contiguous step times and rejects any planned end more than 0.2 seconds beyond the measured duration.

For every step, `ffmpeg -nostdin` actually decodes two PNGs: one after the fixed 0.6-second reveal and another near the available end of the step. Sampling times stay inside both the step and the measured video duration. A step without a post-reveal interval is rejected. Each subprocess uses an argument list, `shell=False`, and a 30-second timeout. Missing tools, decoding errors, and missing or empty images fail inspection.

Each inspection creates a fresh `frames-` directory beside the receipt. Only after all frames decode successfully does it save `evidence.json` and print that file's absolute path. Evidence contains the absolute video path, SHA256 of the actual video bytes, measured `duration_seconds`, and `frames` with absolute `path`, requested sample `time`, and zero-based `step`. Sample times are seek requests; encoded frame timestamps are quantized by the video's frame rate. Failed inspections may leave partial PNGs but do not publish an evidence receipt.

## Validate a feedback revision

An agent can propose a revised scene as a separate JSON file. Before accepting or
rendering that candidate, validate the revision:

```bash
python -m concept_render.revision --original original.json --candidate candidate.json
```

Both inputs are read-only UTF-8 JSON files, limited to 64 KiB each; duplicate JSON
fields are rejected. A revision preserves `version`, `title`, `font`, `nodes`,
`edges`, the number of steps, and the set of nodes shown across all steps. Only
step captions, durations, reveals (`show`) and highlights may change, subject to
the existing scene grammar, including visibility and total-duration limits. An
unchanged candidate is rejected.

Success prints JSON with `changes` (one-based `step`, `field`, `before`, `after`),
`duration_before` and `duration_after`, and exits 0. Invalid input exits nonzero
with a diagnostic on stderr and no success JSON. This command writes no input or
result files and does not render. Python callers can use
`concept_render.revision.validate_revision(original, candidate)` for the same
report, or receive `ValueError` for a rejected revision.

This establishes scope and grammar only. A separate reviewer must check whether
the changes satisfy the feedback; an accepted candidate still needs rendering
and visual inspection before claiming a usable animation.

## Scope of evidence

The output is a silent schematic draft. There is no voiceover. Font availability, glyph coverage, readability, layout overlap, arrow clarity, factual correctness and teaching quality require inspection. A requested font in the receipt does not establish which fallback font was used.

Fake-runner acceptance tests exercise supervision and receipts only. They cannot establish real rendering or provide valid video evidence. The host must execute a real render, run inspection, and separately record manual viewing of the extracted images. In particular, inspect later steps of the six-node input to confirm that earlier labels remain visible through subsequent highlights. Candidate code and automated extraction alone do not establish that this visual regression is fixed.

Parent modules use the standard library and the existing headless `app.render` modules. Only the generated scene imports Manim, inside the render subprocess. No worker, explainer, GUI, networking, packaging or dependency changes are introduced. This document records no evidence from any individual render; records of each real render, its measurements and manual image inspection are retained by the host performing that render in the corresponding run outputs, and this document must not be treated as proof of verification.
