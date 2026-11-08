# Portable Handoff Implementation Plan

> Historical record. Original path: `docs/superpowers/plans/2026-11-03-portable-handoff.md`.
> Findings, commands and validation counts describe the recorded revision and environment,
> not current acceptance. Historical command paths may refer to the original layout.
> See the [current guide](../../portable-projects.md) and [archive index](../README.md).

> **For agentic workers:** Execute inline using superpowers:executing-plans; use a fresh receiving agent and final reviewer for acceptance.

**Goal:** Export explicit inputs and preflight a received ordinary Manim project.
**Architecture:** One bounded archive/environment/CLI module in scene_production, reusing static revision validation and existing render/revision APIs.
**Tech Stack:** Python stdlib, existing Manim/FFmpeg environment; no new dependencies.
**Spec:** [Portable handoff design](../designs/2026-11-03-portable-handoff-design.md)

## Global Constraints

- Start at current remote f697ffa; isolated local branch only.
- No project execution during inspect/unpack/preflight; no installation or network.
- Explicit file selection, versioned inventory, permission/licence notes, no fonts copied.
- Same-machine receiving test only; no promise of semantic or cross-platform equality.

## Review Focus

1. ZIP traversal/aliases/symlinks/special entries: refuse before destination creation.
2. Unlisted or hash-mismatched files: fail closed, not partial import.
3. Missing helper/source mapping: actionable static failure, no implicit discovery.
4. Missing package/font: blocking preflight, no auto-install/fallback claim.
5. Secrets/cache paths and existing destination: reject; preserve unrelated files.

### Task 1: archive and static inventory

Files: scene_production/portable.py; tests/test_portable.py.
Interfaces: export_project(spec, output), inspect_bundle(archive), unpack_bundle(archive, destination).
- [x] Write failing round-trip/security/explicit-selection tests; run and observe red.
- [x] Implement bounded validation and new-destination extraction; run tests green.

### Task 2: receiving preflight and documented CLI

Files: scene_production/portable.py, docs/portable-projects.md, example project config.
Interfaces: preflight(root, environment=None), probe_environment(requirements), module CLI.
- [x] Add missing-dependency/font/static-no-execution tests; observe red then green.
- [x] Document exact export/inspect/unpack/preflight/render/revision commands and limits.
- [x] Full suite/lint/build; local logical commit.

### Task 3: receiving experiment and delivery

- [x] Export project/tool kit; fresh-context receiver renders and edits named scene.
- [x] Record actual intervention, rollback/replay and non-target audit results.
- [x] Fresh review, fix required issues; final suite/lint/build after fixes.
- [ ] Render silent dynamic demo with actual artifacts/evidence; decode and sample.
- [ ] Save usable kit and MP4 via Library; report precise limits and local commits.
