"""Capture hooks observe the exact frames handed to the existing writer."""

from types import SimpleNamespace
import unittest
from scene_production.frame_capture import capture_hooks


class Pixels:
    dtype = "uint8"
    shape = (1, 2, 4)

    def tobytes(self):
        return b"12345678"


class Writer:
    def __init__(self):
        self.video_encoder = SimpleNamespace(frame_rate=30)
        self.calls = []

    def write_frame(self, frame, *, repeat=1):
        self.calls.append((frame, repeat))

    def abort_encode_jobs(self):
        pass


class FakeScene:
    def __init__(self, writer):
        self.renderer = SimpleNamespace(camera=SimpleNamespace(frame_rate=30), file_writer=writer)
        self.calls = []

    @property
    def camera(self):
        return self.renderer.camera

    def next_section(self, name="unnamed", *args, **kwargs):
        self.calls.append(name)


class CaptureHooksTests(unittest.TestCase):
    def test_source_import_path_keeps_product_first_and_restores_state(self):
        from pathlib import Path
        import sys
        from scene_production.frame_capture import _source_import_path

        original = list(sys.path)
        with _source_import_path(Path("/example/source/scene.py")):
            self.assertEqual(Path(sys.path[0]), Path(__file__).resolve().parents[1])
            self.assertEqual(sys.path[1], "/example/source")
        self.assertEqual(sys.path, original)

    def test_observation_counter_and_original_writes_preserved(self):
        original_write = Writer.write_frame
        original_section = FakeScene.next_section
        w = Writer()
        s = FakeScene(w)
        pixels = Pixels()
        with capture_hooks(Writer, FakeScene, "a" * 64) as state:
            s.next_section("intro")
            w.write_frame(pixels, repeat=2)
            s.next_section("next")
            w.write_frame(pixels)
            self.assertEqual(w._scene_production_frame_count, 3)
            out = state.finish()
            self.assertEqual(out["frame_count"], 3)
            self.assertEqual(w.calls, [(pixels, 2), (pixels, 1)])
        self.assertIs(Writer.write_frame, original_write)
        self.assertIs(FakeScene.next_section, original_section)

    def test_renderer_object_is_not_claimed_as_an_observed_cairo_array(self):
        w = Writer()
        s = FakeScene(w)

        class Renderer:
            def get_frame(self):
                return Pixels()

        with capture_hooks(Writer, FakeScene, "a" * 64):
            s.next_section("intro")
            with self.assertRaisesRegex(ValueError, "Cairo"):
                w.write_frame(Renderer())

    def test_skip_and_second_writer_refused_and_hooks_restored(self):
        original = Writer.write_frame
        with self.assertRaises(ValueError):
            with capture_hooks(Writer, FakeScene, "a" * 64):
                w = Writer()
                s = FakeScene(w)
                s.next_section("intro", skip_animations=True)
        self.assertIs(Writer.write_frame, original)
        with capture_hooks(Writer, FakeScene, "a" * 64):
            w = Writer()
            FakeScene(w).next_section("intro")
            w.write_frame(Pixels())
            with self.assertRaisesRegex(ValueError, "writer"):
                Writer().write_frame(Pixels())

    def test_validation_failure_closes_active_encoder_before_propagating(self):
        from queue import Queue
        from threading import Thread

        class ActiveWriter(Writer):
            def __init__(self):
                super().__init__()
                self.queue = Queue()
                self.writer_thread = Thread(target=self.queue.get, daemon=True)
                self.writer_thread.start()
                self.closed = 0

            def abort_encode_jobs(self):
                self.queue.put(None)
                self.writer_thread.join(timeout=1)
                self.closed += 1

        writer = ActiveWriter()
        try:
            with self.assertRaisesRegex(ValueError, "next_section"):
                with capture_hooks(ActiveWriter, FakeScene, "a" * 64):
                    writer.write_frame(Pixels())
            self.assertFalse(writer.writer_thread.is_alive())
            self.assertEqual(writer.closed, 1)
        finally:
            writer.abort_encode_jobs()

    def test_cleanup_failure_does_not_replace_original_capture_error(self):
        class BrokenClose(Writer):
            def __init__(self):
                super().__init__()
                self.writer_thread = SimpleNamespace(is_alive=lambda: True)

            def abort_encode_jobs(self):
                raise RuntimeError("cleanup detail")

        with self.assertRaisesRegex(ValueError, "next_section") as caught:
            with capture_hooks(BrokenClose, FakeScene, "a" * 64):
                BrokenClose().write_frame(Pixels())
        self.assertTrue(any("cleanup detail" in note for note in caught.exception.__notes__))

    def test_rejected_second_writer_encode_jobs_are_both_aborted(self):
        closed = []
        a, b = Writer(), Writer()
        a.abort_encode_jobs = lambda: closed.append("a")
        b.abort_encode_jobs = lambda: closed.append("b")
        with self.assertRaisesRegex(ValueError, "one writer"):
            with capture_hooks(Writer, FakeScene, "a" * 64):
                FakeScene(a).next_section("intro")
                a.write_frame(Pixels())
                b.write_frame(Pixels())
        self.assertEqual(closed, ["a", "b"])
