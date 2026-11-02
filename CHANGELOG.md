# Changelog

All notable changes to this project are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **`scene-production` command** (also `python -m scene_production`) for rendering a trusted, hand-authored Manim `Scene` and checking the result: full video decoding, native-resolution PNG samples at timeline points, and retained source/timeline/video identities. A timeline is inspection configuration and does not drive animation (#16).
- **Recorded-video reinspection:** re-check a retained video when only the inspection timeline changes, without rerunning Manim; changed video bytes are rejected (#16).
- **Netty EventLoop and TCP message-framing examples** with inspection timelines and font-coverage checks (#16).
- **Scene revision helpers** (`python -m scene_production.revision`): capture a version, locate a stable scene ID, apply an author-written edit with stale and scope guards, audit that non-target sections render the same frames, and restore. The caller picks the target and writes the edit; the tool doesn't interpret feedback or design animation (#17).
- **`--record-scene-frames`** (opt-in) records native section boundaries and pre-encoding frame hashes; normal rendering is unchanged when it's off (#17).

### Known limits

- #16 rendering and fonts were tested on macOS only. The #17 example flow was also run once on Linux (Debian 13, Python 3.12, Manim 0.20.1, Noto Sans CJK SC); Windows, OpenGL and other Manim versions are untested (#16, #17).
- `restore` needs the baseline run directory and the candidate video to still exist (#17).
- Visual review was sampled, not full playback. Machine checks don't establish whether a scene's content is correct (#16, #17).
- Restore is not a crash-safe multi-file transaction (#17).

## [0.2.0] - 2026-10-23

### Breaking: how you install it

- **Plain `pip install .` no longer installs the desktop GUI or manim.** The base
  install now has no dependencies, so pick an extra (#13):
  - `pip install -e ".[verify]"` installs only PyYAML, which is enough for `explainer verify`.
  - `pip install -e ".[explainer]"` adds manim and manim-voiceover so you can run `explainer render`. It
    needs the cairo system libraries (and LaTeX); the README lists the apt and brew packages.
  - `pip install ".[gui]"` installs the PyQt5 desktop app and manim.
  - `pip install -e ".[planner]"` adds the Anthropic SDK for `explainer plan`, which drafts a storyboard from real code. It is not part of `.[explainer]`, so `verify` and `render` stay light.
  - `pip install -r requirements.txt` still installs the same packages as before.

### Changed: the citation gate is stricter

Storyboards that passed on 0.1.0 can fail now. That's deliberate: each case below
used to let a "verified" badge stand on evidence that didn't prove anything.

- A plain evidence token must match whole words: `"2"` no longer matches `24`, and `TOKENS` no
  longer matches `MAX_TOKENS`. `/regex/` tokens and text without spaces between words (CJK) are
  unchanged (#8).
- A cite's first and last lines must both hold evidence. Otherwise it fails with the new reason
  `loose_range`, which names the tighter range to cite instead (#8).
- Tokens that match without any real source text (`""`, `"   "`, `"//"`, `"/a*/"`) are rejected (#8).
- Regex tokens that raise `OverflowError` or `RecursionError` count as missing instead of crashing (#8).

### Changed: errors and exit codes

The exit codes are 0 ok, 2 validation error, 3 citation miss, 4 render failure, 5 planner LLM failure, and 6 when the planner produced nothing usable (5 and 6 come only from `explainer plan`).

- `explainer verify` now also runs code generation, so a storyboard that passes the gate but can't
  become a scene fails at verify. Code-generation errors print one `error:` line and exit 2 in
  both `verify` and `render`, instead of a traceback and exit 1 (#11).
- Malformed plot specs (non-numeric, infinite or NaN axis bounds, YAML dates, reversed axes,
  non-mapping curves or annotations) now fail with an `error:` line naming the beat, exit 2 (#12).
  Empty or falsy values such as `x: []` or `curves: ""`, which used to fall back to defaults, are
  errors now too.
- `explainer render` without manim installed stops before writing anything, exits 4, and prints the
  install command (#13).

### Changed: the planner

- `explainer plan` trims each cite to the lines its evidence covers, so generated storyboards pass
  the stricter gate (#8).
- Evidence now reaches a cite's first and last lines. When a trim still cuts a line of code, the
  CLI prints a `dropped line …` note instead of shortening the cite silently (#10).

### Added

- `examples/citation-gate.story.yaml`, the storyboard for the third explainer video, *What the
  verified badge actually promises* (#9).
- CI checks that every `examples/*.story.yaml` verifies and generates (#9), and a job that runs the
  README quick start as written on Python 3.12 and 3.13 (#13).

### Fixed

- The proxy example scene finds `assets/` again after the move to `examples/scenes/` (#6).
- `CLAUDE.md` imports `AGENTS.md`, so agent sessions load the shared guide (#6).

### Docs

- The README states what the gate checks (the cited lines still contain the declared evidence),
  not that narration can't drift (#6).
- The README has one quick start with prerequisites first, an Examples section with links to
  each video, and the desktop GUI under its own heading (#6, #13).
- The package description, `AGENTS.md` and the CLI docstring describe the repo as Explainer
  Engine plus the GUI (#7).

### Known limits

- The Mac brew and MacTeX install lines are not tested in CI.
- A "verified" badge vouches for the cited lines when the video was made, not for the narration.

## [0.1.0]

First tagged release (`6973ba4`).

[0.2.0]: https://github.com/frankxue831/ManimSimpleRender/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/frankxue831/ManimSimpleRender/releases/tag/v0.1.0
