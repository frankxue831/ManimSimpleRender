"""Teaching pauses are explicit frame-grid holds, not simulated wall clocks."""

from types import SimpleNamespace
import unittest

from scene_production.frame_clock import frame_count, mark_section, hold_until_frame


class FakeScene:
    def __init__(self, rate=30, count=0, traced=True):
        self.camera = SimpleNamespace(frame_rate=rate)
        self.renderer = SimpleNamespace(
            time=count / rate, file_writer=SimpleNamespace(), skip_animations=False
        )
        if traced:
            self.renderer.file_writer._scene_production_frame_count = count
        self.sections = []
        self.wait_calls = []

    def next_section(self, name):
        self.sections.append(name)

    def wait(self, duration, frozen_frame):
        self.wait_calls.append((duration, frozen_frame))
        n = int(duration / (1 / self.camera.frame_rate))
        self.renderer.time += n / self.camera.frame_rate
        if hasattr(self.renderer.file_writer, "_scene_production_frame_count"):
            self.renderer.file_writer._scene_production_frame_count += n


class FrameClockTests(unittest.TestCase):
    def test_frozen_holds_cover_quantized_rates_and_assert_output(self):
        for rate in [24, 25, 30, 50, 60, 120, 240]:
            for n in [1, 7, 31, 369, 511, rate * 60 - 1]:
                scene = FakeScene(rate, 17)
                start = mark_section(scene, "intro")
                self.assertEqual(start, 17)
                hold_until_frame(scene, start + n)
                self.assertEqual(frame_count(scene), start + n)
                self.assertTrue(scene.wait_calls[0][1])
                self.assertEqual(scene.sections, ["intro"])

    def test_fallback_counter_and_noop(self):
        scene = FakeScene(30, 369, traced=False)
        self.assertEqual(frame_count(scene), 369)
        hold_until_frame(scene, 369)
        self.assertEqual(scene.wait_calls, [])
        hold_until_frame(scene, 511)
        self.assertEqual(frame_count(scene), 511)

    def test_reject_overrun_non_grid_and_false_success(self):
        scene = FakeScene(30, 20)
        for value in [19, True, 21.1, -1]:
            with self.assertRaises(ValueError):
                hold_until_frame(scene, value)
        with self.assertRaises(ValueError):
            frame_count(FakeScene(29.97))
        scene.wait = lambda duration, frozen_frame: None
        with self.assertRaisesRegex(RuntimeError, "frame"):
            hold_until_frame(scene, 21)

    def test_fractional_second_frame_coordinates_snap_only_float_roundoff(self):
        from scene_production.frame_clock import frame_coordinate

        for rate, count in [(24, 31), (60, 7), (30, 1)]:
            coordinate, nearest, error, limit = frame_coordinate(count / rate, rate)
            self.assertEqual(nearest, count)
            self.assertLessEqual(error, limit)
            coordinate, nearest, error, limit = frame_coordinate((count + 0.001) / rate, rate)
            self.assertGreater(error, limit)
