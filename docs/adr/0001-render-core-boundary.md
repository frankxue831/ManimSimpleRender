# ADR 0001: app/render is the shared render core

Date: 2026-06-10
Status: accepted

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
