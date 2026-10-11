"""Render a storyboard to an mp4: verify -> codegen -> render (via the app core).

Reuses the hardened render pipeline (``app.render``) to run the generated scene,
so process-group kill, merged streams, and MANIM_NOT_FOUND handling come for
free. The generated scene imports ``explainer.codegen.code_compat`` at render
time, so the manim subprocess is given ``PYTHONPATH`` = repo root.
"""

import glob
import os
import shutil
import tempfile

from app.render.aggregation import JobOutcome
from app.render.command_builder import build_command
from app.render.jobs import RenderJob
from app.render.quality import quality_flag_for_height
from app.render.runner import RenderRunner

from explainer.cite.verifier import verify_storyboard
from explainer.codegen.manim_codegen import generate_scene
from explainer.storyboard.model import load_storyboard

_HERE = os.path.dirname(os.path.abspath(__file__))  # explainer/render
REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))  # repo root
SCENE_NAME = "ExplainerVideo"


def resolution_to_quality(resolution, fallback="-qm"):
    """Map a theme resolution like ``'1080p30'`` to a manim quality flag.

    Returns ``-ql/-qm/-qh/-qp/-qk`` from the shared quality ladder
    (``app.render.quality``); unknown or empty resolutions return ``fallback``
    (pass ``None`` to detect unknowns, as the CLI does).
    """
    height = str(resolution or "").split("p", 1)[0].strip()
    if not height.isdecimal():
        return fallback
    return quality_flag_for_height(int(height), fallback=fallback)


class CitationGateError(Exception):
    """Raised when one or more cited code facts fail verification."""

    def __init__(self, failures):
        self.failures = failures
        super().__init__("citation gate failed:\n  " + "\n  ".join(str(f) for f in failures))


class RenderError(Exception):
    """Raised when the manim render does not produce a video."""

    def __init__(self, outcome, workdir):
        self.outcome = outcome
        self.workdir = workdir
        super().__init__(f"render failed ({outcome}); generated scene kept at {workdir}")


def render_storyboard(storyboard_path, out_path, quality=None, lang="en", on_log=None):
    """Verify, generate, and render ``storyboard_path`` to ``out_path`` (an mp4).

    ``quality`` is a manim flag (``-ql/-qm/-qh/-qp/-qk``); when ``None`` it is derived
    from the storyboard's ``theme.resolution`` (so a ``1080p30`` theme renders at
    ``-qh`` by default). Raises :class:`CitationGateError` if any cite fails, or
    :class:`RenderError` if the render produces no video (the generated scene is
    kept for debugging).
    """
    on_log = on_log or (lambda line: None)
    storyboard = load_storyboard(storyboard_path)
    if quality is None:
        quality = resolution_to_quality(storyboard.theme.resolution)

    failures = verify_storyboard(storyboard)
    if failures:
        raise CitationGateError(failures)

    source = generate_scene(storyboard, scene_name=SCENE_NAME, lang=lang)
    workdir = tempfile.mkdtemp(prefix="explainer_")
    try:
        scene_file = os.path.join(workdir, "explainer_scene.py")
        with open(scene_file, "w", encoding="utf-8") as handle:
            handle.write(source)

        media_dir = os.path.join(workdir, "media")
        job = RenderJob(scene_file, SCENE_NAME)
        command = build_command(job, quality, media_dir)

        env = os.environ.copy()
        previous = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = REPO_ROOT + (os.pathsep + previous if previous else "")
        result = RenderRunner().run_job(
            job,
            command,
            cancel_event=None,
            on_log=on_log,
            env=env,
        )

        if result.outcome != JobOutcome.SUCCESS:
            raise RenderError(result.outcome, workdir)

        produced = glob.glob(os.path.join(media_dir, "videos", "*", "*", SCENE_NAME + ".mp4"))
        if not produced:
            raise RenderError("no_output_mp4", workdir)

        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        shutil.copyfile(produced[0], out_path)
        srt = os.path.splitext(produced[0])[0] + ".srt"
        if os.path.exists(srt):
            shutil.copyfile(srt, os.path.splitext(out_path)[0] + ".srt")
    except RenderError:
        raise  # keep the generated scene + media in workdir for debugging
    except BaseException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    shutil.rmtree(workdir, ignore_errors=True)
    return out_path
