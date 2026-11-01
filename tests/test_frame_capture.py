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
        self.renderer = SimpleNamespace(camera=SimpleNamespace(frame_rate=30))
        self.calls = []

    def write_frame(self, frame, num_frames=1):
        self.calls.append((frame, num_frames))


class FakeScene:
    def __init__(self, writer):
        self.renderer = writer.renderer
        self.renderer.file_writer = writer
        self.calls = []

    @property
    def camera(self):
        return self.renderer.camera

    def next_section(self, name="unnamed", *args, **kwargs):
        self.calls.append(name)


class CaptureHooksTests(unittest.TestCase):
    def test_observation_counter_and_original_writes_preserved(self):
        original_write = Writer.write_frame
        original_section = FakeScene.next_section
        w = Writer()
        s = FakeScene(w)
        pixels = Pixels()
        with capture_hooks(Writer, FakeScene, "a" * 64) as state:
            s.next_section("intro")
            w.write_frame(pixels, 2)
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
