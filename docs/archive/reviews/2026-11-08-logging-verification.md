# Logging verification record (2026-11-08)

> Historical record extracted from `docs/logging-and-diagnostics.md` at `fb80059`.
> The counts and outcomes below were recorded for those revisions; they were not
> rerun during documentation reorganization. See the [current logging guide](../../logging-and-diagnostics.md)
> and [archive index](../README.md).


The initial lightweight run on November 8, 2026 used Python 3.13 on macOS with
Qt offscreen and ran 1184 tests:
1165 passed and 19 were skipped for unavailable real Manim/TTS or platform
requirements. Black and Flake8 passed for the same source/example paths checked
by CI; `git diff --check` passed. The citation-gate example was updated to its
new source line range and its `explainer verify` command passed.

Regression coverage includes sparse GUI output, full-log export and same-file
protection, disk write failures, forced QThread termination without a lingering
flusher, late timer callbacks, private early-failure tracebacks, unchanged
two-event agent stdout, handoff protocol inconsistency, and sanitized failure
reason propagation. The complete test suite also exercises the existing
architecture, portable output, revision and conservative recovery contracts.

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m black --check app explainer scene_production tests main.py
.venv/bin/python -m flake8 app explainer scene_production tests main.py
git diff --check
```

The real-render follow-up used Manim 0.20.1 and ffmpeg/ffprobe 9.0.2 in this
worktree's isolated environment. The existing real-render smoke passed. New
`tests/e2e/test_real_logging_smoke.py` runs Agent and handoff success/failure
through actual CLI subprocesses, checking video/frame hashes, phase records,
private-output isolation and exact stdout/state/journal agreement. It gates on
Manim, ffmpeg and ffprobe so the lightweight CI still skips it, while the
existing nightly e2e discovery includes it automatically.

Additional retained local smoke evidence checked a real scene timeout after
entering `construct`, a successful recorded-video reinspection without a new
render, and success/failure through the GUI worker with real subprocesses. The
worker was driven synchronously for this evidence; QThread scheduling and forced
termination are covered separately by isolated regression tests.

Two independent second-pass code reviews covered sensitive data, exception
semantics, optional versus mandatory persistence, thread shutdown and export
behavior. A known-incomplete log exported to itself or a hardlink previously
lost its warning in the GUI status; this was fixed and regression-tested. No
remaining blocking findings were identified within that review scope.

After this fix and the real smoke additions, the full suite ran **1187 tests:
1176 passed, 11 skipped**, in 36.126 seconds. Skips were nine tests requiring
the uninstalled voiceover package, one real Windows process test and one
environment-dependent Qt scrollbar check. Black, Flake8 and `git diff --check`
passed. No real TTS, OpenGL render, full interactive GUI acceptance or human
semantic review is claimed.

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest \
  tests.e2e.test_real_render_smoke tests.e2e.test_real_logging_smoke -v
```

The PR 26 review follow-up ran **1210 tests: 1199 passed, 11 skipped**, in
35.802 seconds on the same local Python 3.13 environment. The skip categories
above are unchanged. Black, Flake8 and citation-gate example verification passed.
See the separate [review response and evidence checklist](2026-11-08-pr26-review-followup.md)
for reproduced findings, fixes and remaining limitations. The earlier frozen
handoff describes the original PR head, not these follow-up changes.
