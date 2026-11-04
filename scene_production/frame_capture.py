"""Private optional full-Manim runner observing native sections and raw RGBA.

Manim remains optional until main(). Hooks forward the original frame object and
repeat count unchanged. No image tolerance or semantic assessment is performed.
"""

from contextlib import contextmanager
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile

from .frame_trace import FrameTrace


class _CaptureState:
    def __init__(self, source_sha256):
        self.source_sha256 = source_sha256
        self.writer = None
        self.writers = []
        self.collector = None

    def owner(self, writer):
        if not any(value is writer for value in self.writers):
            self.writers.append(writer)
        if self.writer is not None and self.writer is not writer:
            raise ValueError("frame capture supports exactly one writer")
        self.writer = writer
        if self.collector is None:
            rate = Fraction(str(writer.renderer.camera.frame_rate))
            self.collector = FrameTrace(rate, self.source_sha256)
        writer._scene_production_frame_count = self.collector.count
        return self.collector

    def abort(self, error):
        # Manim starts a non-daemon queue consumer before calling write_frame.
        # A hook exception bypasses Scene.tear_down; stop that consumer so the
        # child can report its original failure instead of waiting for timeout.
        for writer in self.writers:
            try:
                thread = getattr(writer, "writer_thread", None)
                if thread is not None and thread.is_alive():
                    writer.close_partial_movie_stream()
                else:
                    container = getattr(writer, "video_container", None)
                    if container is not None:
                        container.close()
            except Exception as cleanup_error:
                error.add_note("frame writer cleanup failed: " + str(cleanup_error))

    def finish(self):
        if self.collector is None:
            raise ValueError("no captured Manim frames")
        return self.collector.finish()


@contextmanager
def capture_hooks(writer_class, scene_class, source_sha256):
    state = _CaptureState(source_sha256)
    original_write = writer_class.write_frame
    original_section = scene_class.next_section

    def write(writer, frame_or_renderer, num_frames=1):
        collector = state.owner(writer)
        if not hasattr(frame_or_renderer, "tobytes"):
            raise ValueError("raw capture requires the actual Cairo RGBA array")
        frame = frame_or_renderer
        if str(frame.dtype) != "uint8":
            raise ValueError("raw capture requires uint8 RGBA frames")
        collector.add_frame(frame.tobytes(), frame.shape, num_frames)
        writer._scene_production_frame_count = collector.count
        return original_write(writer, frame_or_renderer, num_frames=num_frames)

    def section(scene, name="unnamed", *args, **kwargs):
        skip = kwargs.get("skip_animations", args[1] if len(args) > 1 else False)
        if skip:
            raise ValueError("traced native sections cannot skip animations")
        collector = state.owner(scene.renderer.file_writer)
        collector.section(name)
        return original_section(scene, name, *args, **kwargs)

    writer_class.write_frame = write
    scene_class.next_section = section
    try:
        yield state
    except BaseException as error:
        state.abort(error)
        raise
    finally:
        writer_class.write_frame = original_write
        scene_class.next_section = original_section


@contextmanager
def _source_import_path(source):
    previous = list(sys.path)
    trusted = str(Path(__file__).resolve().parent.parent)
    project = str(source.parent)
    sys.path[:] = [trusted, project, *[p for p in previous if p not in (trusted, project)]]
    try:
        yield
    finally:
        sys.path[:] = previous


def main():
    target = os.environ.get("SCENE_PRODUCTION_FRAME_TRACE")
    if not target or len(sys.argv) < 2:
        raise ValueError("frame capture requires a source and a scoped trace output")
    source = Path(sys.argv[1]).resolve()
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    # These are the existing installed Manim classes, not another render engine.
    from manim import Scene
    from manim.scene.scene_file_writer import SceneFileWriter

    # Installed Manim and product imports are resolved before trusted scene helpers.
    with _source_import_path(source), capture_hooks(SceneFileWriter, Scene, source_sha) as state:
        sys.argv[0] = "manim"
        try:
            runpy.run_module("manim", run_name="__main__")
        except SystemExit as error:
            if error.code not in (None, 0):
                raise
        data = state.finish()
    path = Path(target)
    fd, name = tempfile.mkstemp(prefix=".frame-trace-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, separators=(",", ":"))
        Path(name).replace(path)
    finally:
        Path(name).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    main()
