# ADR 0001: app/render is the shared render core

Date: 2026-06-10
Status: accepted

## Subsequent implementation status (2026-11-08)

The context and extraction trigger below record the original two-consumer decision.
The implementation now also has `concept_render/` and `scene_production/` consumers
of `app.render`. The new-consumer extraction trigger has therefore been met, but
the core remains in `app/render`; extraction is outstanding and is not performed
by this documentation update. The distribution still ships the GUI, Explainer
and scene production together, with optional dependency extras.

The Qt boundary remains applicable. `tests/test_architecture.py` also guards scene
production's Qt-free imports; it does not scan `concept_render/`. Explainer now
imports shared quality and diagnostic helpers as well as its render adapter's
dependencies, so the original exact import-count description is historical.
See the [documentation index](../README.md) for the current tool inventory.

## Context

The repo has two front-ends sharing one render pipeline:

- app/        the PyQt5 GUI (batch-renders existing .py Manim scenes)
- explainer/  the CLI vertical (storyboard YAML -> citation gate ->
              deterministic Manim codegen -> narrated video)

The only cross-package edge is explainer/render/adapter.py importing five
names from app.render. app/ imports nothing from explainer/. The dependency
is acyclic and one-directional.

    +---------------------+        +----------------------+
    |  app/ui (PyQt5 GUI) |        |  explainer/ (CLI)    |
    |  app/bootstrap      |        |  storyboard, cite,   |
    |  app/ui/controller  |        |  codegen, adapter    |
    +----------+----------+        +----------+-----------+
               |                              |
               v                              v
    +-------------------------------------------------+
    |  app/render  (shared render core, Qt-free*)     |
    |  jobs, command_builder, runner, aggregation,    |
    |  quality                                        |
    |  *worker.py: the one Qt module (GUI-side        |
    |   QThread adapter) -- not part of the core API  |
    +-------------------------------------------------+

## Decision

- app/render is the shared render core. app/ui and explainer/ may depend on
  it; nothing may depend back on app/ui or explainer/.
- The core is Qt-free EXCEPT app/render/worker.py, which is explicitly the
  GUI-side Qt adapter (QThread). explainer/ must never import
  app.render.worker, and no other core module may import it either (it is
  not part of the core API).
- No module under app/render imports app.ui or app.bootstrap (extending the
  layering rule in app/__init__.py, which names app.ui).
- app/render/quality.py is the single source of truth for the manim quality
  ladder (flag <-> pixel height). The config loader's flag allowlist and the
  explainer's resolution mapping derive from it.
- These rules are enforced by tests/test_architecture.py (static imports plus
  constant-string dynamic imports; see its docstring for limits).

## Deferred: extracting a neutral render_core/ package

Renaming the core out of the app package fixes only a naming smell today: it
is four import lines, zero behavior change, and does not decouple install
dependencies while one distribution ships both entry points.

Extraction trigger (pre-decided, do not re-litigate): extract render_core/
when a NEW consumer of the core appears beyond the current two (app/ui,
explainer/), or when the distribution splits (e.g. shipping the explainer
without Qt deps). It is a ~1h mechanical move.

Also out of scope here: unifying the GUI and explainer UX (a product
decision needing its own brainstorm, not an architecture cleanup).

## Consequences

- The boundary is executable: a PR that adds a back-edge or a Qt import to
  the core fails tests/test_architecture.py.
- explainer gains new core capabilities (like the shared quality ladder)
  without growing private copies.
- The extraction decision is pre-made; future sessions implement the trigger
  instead of re-debating the architecture.

### Cleanup evidence and consumer blocking

Render operation outcomes are separate from `render_cleanup_state`: `not_started`
means this invocation did not acquire a render process, `complete` records the
supervisor's bounded quiescence observations, and `unknown` means those observations
were not established. Missing or invalid evidence is unknown. Consumers must not
publish a successful render unless its state is complete.

The desktop batch worker halts on unknown, accounts for remaining jobs as not
completed, and retains an explicit summary warning. The controller blocks both
Start and Rerun after uncertain cleanup or worker completion without a summary.
Logs and output access remain available, and closing the application is allowed.
This is an application-session latch, not persistent host-wide containment;
reopening the application is not evidence that cleanup completed. Investigation
is required before further renders. Explainer retains potentially active workdirs
and concept receipts preserve cleanup state even when rendering fails.

## Cleanup evidence is independent of outcome

`app.render.cleanup.RenderCleanupState` is Qt-free and shared by all render
consumers. `JobResult.render_cleanup_state` is appended with a conservative
`unknown` default; old positional construction remains possible but supplies no
safe execution evidence. Every in-tree consumer rejects uncertainty before
publishing artifacts or launching another render. Ordinary failure can coexist
with complete cleanup. Outcome/return-code/diagnostic text cannot substitute for
session observations. Production propagates typed state on exceptions even when
there is no run directory or receipt persistence fails.

Complete means observed leader reap, final captured POSIX group absence or owned
Windows Job Object emptiness before close, no active reader and no pending resume
step. It does not establish containment of detached scene code. POSIX zombies or
failed probes may conservatively retain unknown. The GUI's blocked state is local
to the application session; reopening the app is not cleanup evidence.


Returned results are validated as a tuple before their cleanup state is latched.
`not_started` is inconsistent with SUCCESS, a non-None render return code, or an
outcome other than FAILED/MANIM_NOT_FOUND. Such contradictions become unknown;
legitimate no-spawn failures with no return code remain not_started. Complete
post-spawn rollback may still accompany FAILED/None. An interrupted reader start
remains unknown regardless of identity publication or a transient false is_alive
observation. Its potentially active output stream is not closed by that snapshot.
