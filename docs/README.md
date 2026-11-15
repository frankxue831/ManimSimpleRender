# Documentation

## Start here

Install Python, Manim, and system prerequisites via the [project README](../README.md).
Before rendering an ordinary `Scene`, run `python -m app.environment --json` to verify media
tools and runtime readiness; see [agent quick start](agent-quickstart.md#agent-runtime-preflight).

- For a step-by-step new video project (human author and external agent building a
  hand-authored Python Manim `Scene` with optional local PCM WAV audio), start with the
  [first-use starter](first-use-starter.md), which sequences the `Scene` guides below.
- For automated single-agent `python -m scene_production.agent` (`render` or `inspect`)
  runs, start with the [agent quick start](agent-quickstart.md) before reading the full
  [Agent protocol](scene-agent.md).

## Choose a workflow

In this repository, a `Scene` is a Manim Python `Scene` class (*trusted* means caller-vetted
Python code, because rendering executes Python with your privileges), and a *receipt*
(`result.json`) records run paths, settings, and SHA-256 hashes.

| Goal | Input | Result | Guide |
| --- | --- | --- | --- |
| Select and batch-render scenes in a desktop window | Trusted `Scene` folder | Rendered videos, per-scene results, and batch logs | [Desktop GUI](../README.md#also-in-this-repo-desktop-gui) |
| Explain source code with citations and narration | Source repository and storyboard YAML (or `--repo`, `--focus` lines, and `--topic` for `explainer plan`) | Drafted YAML, citation/codegen checks, or narrated video | [Explainer](../README.md#explainer-explainer) |
| Make a silent schematic draft | Concept JSON | Generated `Scene`, silent video, and receipt | [Concept rendering](concept-render.md) |
| Render and check a hand-authored `Scene` | Trusted `Scene` and inspection timeline | Video, sampled PNGs, receipt, and diagnostics | [Scene production](scene-production.md) |
| Invoke rendering or reinspection from an agent | Trusted `Scene` or prior `result.json`, and inspection timeline | Isolated attempt directory, JSONL events, and hash-checked artifacts | [Agent protocol](scene-agent.md) ([quick start](agent-quickstart.md)) |
| Change a bounded section of a silent `Scene` | Revision manifest, baseline run, and authored edit | Scoped edit, comparison evidence, and rollback version | [Scene revision](scene-revision.md) |
| Transfer an editable project | Explicit file inventory and requirements | Portable ZIP, bundle inspection report, and preflight check | [Portable projects](portable-projects.md) |
| Coordinate local workers with dependencies | Recorded user-authorization evidence and task JSON | Durable claims, outcomes, and pollable local event inbox (no push notifications) | [Local handoff](agent-handoff.md) |

An inspection timeline selects check timestamps while the Python `Scene` controls animation.
[Concept rendering](concept-render.md) is a standalone draft path from a source checkout
(`python -m concept_render`), whereas [Agent protocol](scene-agent.md) wraps
[Scene production](scene-production.md) with isolated attempt directories and JSONL event logs,
[Scene revision](scene-revision.md) operates on silent `Scene` production baselines (before
audio extensions; external caption sidecars remain bound to the baseline receipt), and
[Local handoff](agent-handoff.md) coordinates dependent manual or
[Agent protocol](scene-agent.md) runs.

Installed executables are `manim-simple-render` (Desktop GUI), `explainer`, and
`scene-production`; `agent`, `revision`, `portable`, and `handoff` run via
`python -m scene_production.<module>`. Guides list install prerequisites where needed.
Output isolation varies by interface: `scene-production`, `concept_render`, and
`revision capture` create fresh subdirectories under an output root; `agent` and
`portable unpack` require a nonexistent destination directory; `portable export` requires
a nonexistent output `.zip`; `revision apply`/`restore` update declared project files in
place; and `explainer render`/`plan` (`-o`) and the Desktop GUI's `--media_dir` can
overwrite existing outputs in a reused destination.

## Extend, review, or diagnose a run

These helpers extend, sample, review, or diagnose runs without re-rendering a `Scene`.
Caption sidecars and caption-free frame evidence require an original `status="success"`
`operation="render"` `result.json` (not a `reinspect` receipt or `attempt.json`), audio
previews and caption-linked samples consume a caption sidecar `manifest.json`, editorial
review accepts any delivered `.mp4` (rather than `result.json`), and logging covers active,
completed, and failed runs across the Desktop GUI, `scene-production`/reinspection,
`agent`, `handoff`, and `explainer render`:

- [Caption sidecars](scene-captions.md) (`scene_production.captions` API; draft-SRT CLI `python -m app.render.subtitle_draft`): explicit frame events, external SRT files, and binding checks written into a fresh `captions-*` subdirectory.
- [Audio previews](scene-audio.md) (`scene_production.audio` API): checksum-locked PCM WAV bindings against a caption `manifest.json` and separate narrated MP4 previews written into nonexistent output directories.
- [Frame evidence and caption-linked samples](scene-frame-samples.md) (`python -m scene_production.visual_frames`): expanded beyond initial timeline samples with bounded native PNGs, timeline coverage indexes, and labeled contact-sheet grids from `--from-run` or `--captions` into a nonexistent output directory.
- [Editorial review](editorial-review.md) (`python -m scene_production.editorial`): findings and candidate comparisons tied to a run or revision version, recording JSON and SHA-256 hashes of any `.mp4` into a nonexistent output file without decoding media.
- [Logging and diagnostics](logging-and-diagnostics.md) (reference guide): run and attempt log locations, phase timing records, and failure triage.

Media-checking helpers verify declared container/stream formats and SHA-256 byte identities
(`editorial` verifies assessment JSON and SHA-256 byte hashes without decoding media). Review
visuals, timing, facts, and audio separately before claiming an explanation is ready.

## Examples

- [Dynamic Netty and TCP examples](dynamic-examples.md): hand-authored silent `Scene` scripts and CJK font requirements.
- [Executable revision example](scene-revision.md#executable-revision-example): local capture, locate, apply, audit, and restore walkthrough.
- [Silent review workflow example](silent-review-example.md): baseline render, single-section revision, and retained review candidate.
- [Composed review workflow example](composed-review-example.md): compositing synthetic overlays onto a scene while reviewing raw-scene visuals separately from the final video.
- [Real-Manim handoff regression](agent-handoff.md#reproducible-real-manim-handoff-regression): concurrent local claims, durable discovery, and pending review.

## Templates, reference, and development

- [Project record template](templates/video-project.md): copy-and-fill project checklist for the [first-use starter](first-use-starter.md).
- [First-use pilot log](templates/first-use-pilot.md): evaluation checklist for testing the [first-use starter](first-use-starter.md) in a pilot session.
- [Generated diagram motion](diagram-motion.md): Explainer storyboard cubic-path start, layering, and identity reference.
- [Shared render-core decision](adr/0001-render-core-boundary.md): render-core boundary rationale and implementation status.
- [Contributor guide](../AGENTS.md): repository architecture, commands, and coding conventions.
- [Development checks](../README.md#development): test suite, formatter, linter, and CI workflow.
- [Changelog](../CHANGELOG.md): released and unreleased capabilities.

## Historical records

The [archive](archive/README.md) preserves designs, plans, migrations, experiments, and
reviews. Their results apply to the recorded revisions and environments; use the guides
above for current operation.
