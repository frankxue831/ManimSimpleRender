# Documentation

Agents should start with the [agent quick start](agent-quickstart.md). For installation,
the desktop application and Explainer's first example, see the [project README](../README.md).

## Choose a workflow

| Goal | Input | Result | Guide |
| --- | --- | --- | --- |
| Select and batch-render scenes in a desktop window | Trusted Python Scene folder | Videos, per-scene results and batch logs | [Desktop GUI](../README.md#also-in-this-repo-desktop-gui) |
| Explain source code with citations and narration | Source repository and storyboard YAML | Citation/codegen checks or narrated video | [Explainer](../README.md#explainer-explainer) |
| Make a silent schematic draft | Concept JSON | Generated Scene, silent video and receipt | [Concept rendering](concept-render.md) |
| Render and check a hand-authored Scene | Trusted Scene and inspection timeline | Video, sampled PNGs, receipt and diagnostics | [Scene production](scene-production.md) |
| Invoke rendering or reinspection from an agent | The same inputs, plus a new attempt directory | JSONL events and hash-checked artifacts | [Agent protocol](scene-agent.md) |
| Change a bounded section of a silent Scene | Revision manifest, baseline run and authored edit | Scoped edit, comparison evidence and rollback version | [Scene revision](scene-revision.md) |
| Transfer an editable project | Explicit file inventory and requirements | ZIP, inspection and receiving preflight | [Portable projects](portable-projects.md) |
| Coordinate local workers with dependencies | Authorization-source records and task JSON | Durable claims, outcomes and pull inbox | [Local handoff](agent-handoff.md) |

Concept rendering runs from a source checkout. Scene production has both a CLI and
Python API; the agent protocol and handoff have separate module entry points.
Caption and audio helpers expose Python APIs; do not assume every module has a CLI.
The guides specify installation and output-directory rules for each interface.

## Extend or inspect a completed run

- [Caption sidecars](scene-captions.md): explicit frame events, external SRT and binding checks.
- [Audio previews](scene-audio.md): freeze existing WAV inputs and create a separate preview.
- [Caption-linked frame samples](scene-frame-samples.md): bounded, exact-index PNG extraction.
- [Logging and diagnostics](logging-and-diagnostics.md): locate logs and interpret failures and timing.

A production timeline selects inspection times; the Python Scene controls the animation.
Machine checks verify the stated media and byte identities. Review pictures, timing,
facts and audio separately before claiming that an explanation is ready.

## Examples and development

- [Dynamic Netty and TCP examples](dynamic-examples.md), including font requirements.
- [Generated diagram motion](diagram-motion.md): supported path and identity rules.
- [Executable revision example](scene-revision.md#executable-revision-example).
- [Contributor guide](../AGENTS.md) and [development checks](../README.md#development).
- [Shared render-core decision](adr/0001-render-core-boundary.md), with subsequent implementation status.
- [Changelog](../CHANGELOG.md) for released and unreleased capabilities.

## Historical records

The [archive](archive/README.md) preserves designs, plans, migrations, experiments and
reviews. Their results apply to the recorded revisions and environments; use the
guides above for current operation.
