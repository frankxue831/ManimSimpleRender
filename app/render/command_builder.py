"""Pure construction of the manim CLI argv (no subprocess, no Qt).

The argv prefix that launches manim is resolved by :func:`manim_base_command`:

- Normal (source / pip install): ``[sys.executable, '-m', 'manim']`` -- the SAME
  interpreter the pre-flight probe checks (``app.validation.manim_available``),
  so probe and spawn agree whether manim is module-only or a console script.
- Frozen app (PyInstaller/py2app, ``sys.frozen``): ``sys.executable`` is the
  bundled GUI binary, so ``-m manim`` would relaunch the GUI. Use an external
  ``manim`` on PATH instead (the render toolchain stays outside the bundle).

The full argv is the base + ``[<file>, <scene>, <resolution_flag>, '--media_dir',
<output_dir>]`` with the engine renderer flag appended ONLY when non-empty (the
``""`` sentinel for the default cairo engine suppresses the trailing token).
"""

import shlex
import shutil
import subprocess
import sys


def display_command(argv):
    """Render an argv list as a copy/paste-safe shell command string (BL-01).

    The render path hands ``argv`` straight to ``Popen`` (``shell=False``), so it
    is already safe; this is for *display only* -- the Copy Command feature and the
    worker's ``"$ ..."`` log echo. A naive ``" ".join(argv)`` re-splits on spaces
    and shell metacharacters when pasted into a shell (a path like
    ``/My Documents/scene.py`` breaks, and a tampered ``-ql; rm -rf ~`` flag would
    become executable), so quote per-platform.
    """
    if sys.platform == "win32":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def manim_base_command(frozen=None, which=shutil.which):
    """Return the argv prefix that invokes manim (see module docstring).

    ``frozen``/``which`` are injectable for testing; by default ``frozen`` is
    detected from ``sys.frozen``.
    """
    if frozen is None:
        frozen = getattr(sys, "frozen", False)
    if frozen:
        path = which("manim")
        return [path] if path else ["manim"]  # bare name -> runner maps MANIM_NOT_FOUND
    return [sys.executable, "-m", "manim"]


def build_command(job, resolution_flag, output_dir, engine_renderer_flag="", base_command=None):
    """Return the manim argv list for ``job`` at the given resolution/engine.

    ``base_command`` overrides the resolved manim prefix (defaults to
    :func:`manim_base_command`), so callers/tests can pin the invocation.
    """
    command = list(base_command if base_command is not None else manim_base_command())
    command += [job.file_path, job.scene_class, resolution_flag, "--media_dir", output_dir]
    if engine_renderer_flag:
        command.append(engine_renderer_flag)
    return command
