"""Caption-free evidence: ordinals, bounded I/O, identity and partial failures."""

from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from scene_production import digest
from scene_production import evidence_export as evidence
from scene_production.evidence_io import Budget, Storage, capture, identity, strict_json
from scene_production import visual_frames

try:
    import PIL
except ImportError:
    PIL = None


class SelectionTests(unittest.TestCase):
    def test_default_endpoints_and_complement(self):
        for n in (1, 2, 11, 12, 45):
            values = evidence.selection(None, n)
            self.assertEqual(values[0], 0)
            self.assertEqual(values[-1], n - 1)
            self.assertEqual(len(values), min(n, 12))
            expected = list(range(n)) if n <= 12 else [0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44]
            self.assertEqual(values, expected)
        self.assertEqual(
            evidence.coverage_for([0, 14, 15, 30, 44], 45)["omitted_intervals"],
            [[1, 14], [16, 30], [31, 44]],
        )
        self.assertTrue(evidence.coverage_for([0], 1)["export_coverage_complete"])
        self.assertEqual(evidence.selection([44, 0, 15], 45), [0, 15, 44])

    def test_invalid_selection(self):
        for indices in ([], [True], [1.5], [-1], [45], [0, 0], "0", list(range(121))):
            with self.subTest(indices=indices), self.assertRaises(ValueError):
                evidence.selection(indices, 45)
        for args in (([1], 45, True), (None, 121, True), (None, 45, 1)):
            with self.assertRaises(ValueError):
                evidence.selection(*args)

    def test_full_export_never_review(self):
        selected = evidence.selection(None, 45, True)
        coverage = evidence.coverage_for(selected, 45)
        self.assertEqual(selected, list(range(45)))
        self.assertTrue(coverage["export_coverage_complete"])
        self.assertFalse(coverage["review_performed_by_exporter"])

    def test_new_flags_rejected_in_caption_mode(self):
        for flags in (["--all-frames"], ["--operation-timeout", "30"]):
            with self.assertRaises(SystemExit) as error:
                visual_frames.main(["--captions", "absent", "--output-dir", "absent", *flags])
            self.assertEqual(error.exception.code, 2)

    def test_import_and_help_do_not_load_pillow_manim_or_qt(self):
        script = """
import sys
from scene_production import visual_frames
assert not any(x == 'PIL' or x.startswith(('PIL.', 'manim', 'PyQt')) for x in sys.modules)
try: visual_frames.main(['--help'])
except SystemExit as exc: assert exc.code == 0
assert not any(x == 'PIL' or x.startswith(('PIL.', 'manim', 'PyQt')) for x in sys.modules)
"""
        subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, timeout=10)

    def test_missing_pillow_precedes_inputs_or_media(self):
        with patch.dict(sys.modules, {"PIL": None}), self.assertRaisesRegex(RuntimeError, "review"):
            evidence.export_run("absent", "absent")


class BoundedIOTests(unittest.TestCase):
    def test_no_newline_stdout_and_stderr_are_capped_during_read(self):
        for fd in (1, 2):
            script = f"import os,time\nwhile True: os.write({fd}, b'x'*65536)"
            start = time.monotonic()
            with self.assertRaisesRegex(ValueError, "capture"):
                capture([sys.executable, "-c", script], Budget(5), 5, limit=1024)
            self.assertLess(time.monotonic() - start, 4)

    def test_deadline_cancel_and_nonzero(self):
        with self.assertRaises(TimeoutError):
            capture([sys.executable, "-c", "import time; time.sleep(10)"], Budget(3), 0.05)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(InterruptedError):
            capture([sys.executable, "-c", "pass"], Budget(3, cancel), 1)
        with self.assertRaisesRegex(RuntimeError, "failed"):
            capture([sys.executable, "-c", "raise SystemExit(7)"], Budget(3), 1)

    def test_running_cancel_and_kill_escalation_are_observed(self):
        class Log:
            def event(self, event, **values):
                self.values = values

        log = Log()
        cancel = threading.Event()
        timer = threading.Timer(0.1, cancel.set)
        timer.start()
        try:
            with self.assertRaises(InterruptedError):
                capture(
                    [sys.executable, "-c", "import time; time.sleep(10)"],
                    Budget(5, cancel),
                    4,
                    diagnostics=log,
                )
        finally:
            timer.join()
        self.assertTrue(log.values["exit_observed"])
        if sys.platform != "win32":
            with self.assertRaises(TimeoutError):
                capture(
                    [
                        sys.executable,
                        "-c",
                        "import signal,time; "
                        "signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(10)",
                    ],
                    Budget(5),
                    0.2,
                    diagnostics=log,
                )
            self.assertTrue(log.values["exit_observed"])
            self.assertEqual(log.values["returncode"], -9)

    def test_capture_exact_boundary_and_direct_child_exit(self):
        class Log:
            def event(self, event, **values):
                self.values = values

        log = Log()
        got = capture(
            [sys.executable, "-c", "print('1234',end='')"], Budget(3), 1, limit=4, diagnostics=log
        )
        self.assertEqual(got, b"1234")
        self.assertTrue(log.values["exit_observed"])
        self.assertEqual(log.values["returncode"], 0)

    def test_hash_size_deadline_and_strict_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "small"
            file.write_bytes(b"abcd")
            self.assertEqual(identity(file, 4, Budget(2))["size_bytes"], 4)
            with self.assertRaises(ValueError):
                identity(file, 3, Budget(2))
            with self.assertRaises(TimeoutError):
                identity(file, 4, Budget(-1))
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}'):
            with self.assertRaises(ValueError):
                strict_json(raw)

    def test_storage_cap_is_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Storage(root, 4, Budget(3))
            store.write("one", lambda f: f.write(b"1234"), 4)
            with self.assertRaisesRegex(ValueError, "storage"):
                store.write("two", lambda f: f.write(b"5"), 4)
            self.assertEqual((root / "two").stat().st_size, 0)


@unittest.skipUnless(
    PIL and shutil.which("ffmpeg") and shutil.which("ffprobe"), "Pillow or media tools missing"
)
class RealEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name).resolve()
        cls.video = cls.root / "fixture.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=s=64x48:r=15:d=3",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(cls.video),
            ],
            check=True,
            capture_output=True,
            timeout=20,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(dir=self.root))
        self.run = self.folder / "run"
        self.run.mkdir()
        self.receipt = self.run / "result.json"
        (self.run / "source.py").write_text("raise RuntimeError('NEVER EXECUTE')\n")
        (self.run / "timeline.json").write_text(
            json.dumps({"version": 1, "segments": [{"label": "Fixture", "start": 0, "end": 3}]})
        )
        shutil.copyfile(self.video, self.run / "video.mp4")
        self.record = {
            "status": "success",
            "run_dir": str(self.run),
            "source": "historical.py",
            "source_sha256": digest(self.run / "source.py"),
            "timeline_sha256": digest(self.run / "timeline.json"),
            "video": str(self.run / "video.mp4"),
            "video_sha256": digest(self.run / "video.mp4"),
        }
        self.save_receipt()
        self.output = self.folder / "out"

    def save_receipt(self):
        self.receipt.write_text(json.dumps(self.record))

    def export(self, **kw):
        return evidence.export_run(self.receipt, self.output, **kw)

    def test_real_package_independent_rgb_and_mapping(self):
        result = self.export(frame_indices=[44, 0, 14, 15, 30])
        index = json.loads((self.output / "frames.json").read_text())
        self.assertEqual(result["operation"], "run_frame_evidence")
        self.assertEqual(index["schema_version"], 1)
        self.assertEqual(result["coverage"]["omitted_intervals"], [[1, 14], [16, 30], [31, 44]])
        independent = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-i",
                str(self.video),
                "-pix_fmt",
                "rgb24",
                "-fps_mode",
                "passthrough",
                "-f",
                "rawvideo",
                "-",
            ],
            capture_output=True,
            check=True,
            timeout=20,
        ).stdout
        from PIL import Image

        for cell, frame in enumerate(index["frames"]):
            i = frame["frame_index"]
            expected = independent[i * 64 * 48 * 3 : (i + 1) * 64 * 48 * 3]
            with Image.open(frame["native"]["path"]) as image:
                self.assertEqual(image.size, (64, 48))
                self.assertEqual(image.tobytes(), expected)
            self.assertEqual(frame["decoded_rgb24_sha256"], hashlib.sha256(expected).hexdigest())
            self.assertEqual(frame["sheet"]["cell"], cell)
            self.assertEqual(frame["sheet"]["tile_dimensions"], [64, 48])
        for key in (
            "visual_acceptance",
            "semantic_acceptance",
            "human_review_recorded",
            "continuous_playback_performed",
            "audio_reviewed",
            "render_completion_verified_by_exporter",
            "cfr_verified",
        ):
            self.assertFalse(result[key])
        self.assertEqual(json.loads((self.output / "manifest.json").read_text()), result)

    def test_all_frames_four_sheets_and_grid(self):
        result = self.export(all_frames=True)
        index = json.loads((self.output / "frames.json").read_text())
        self.assertEqual([s["displayed_count"] for s in index["sheets"]], [12, 12, 12, 9])
        self.assertTrue(result["coverage"]["export_coverage_complete"])
        self.assertEqual(index["frames"][8]["grid_time_seconds"], "8/15")
        self.assertEqual(
            [(f["sheet"]["page"], f["sheet"]["cell"]) for f in index["frames"]],
            [(i // 12 + 1, i % 12) for i in range(45)],
        )

    def test_changed_inputs_never_publish(self):
        for name in ("source.py", "timeline.json", "video.mp4", "result.json", "trace.json"):
            with self.subTest(name=name):
                self.setUp()
                trace = self.run / "trace.json"
                trace.write_bytes(b"{}")
                self.record["frame_trace"] = {"path": str(trace), "sha256": digest(trace)}
                self.save_receipt()
                original = evidence.build_sheets

                def mutate(*args, **kwargs):
                    sheets = original(*args, **kwargs)
                    with (self.run / name).open("ab") as handle:
                        handle.write(b" ")
                    return sheets

                with patch.object(evidence, "build_sheets", side_effect=mutate):
                    with self.assertRaises(ValueError):
                        self.export(frame_indices=[0])
                self.assertFalse((self.output / "manifest.json").exists())

    def test_artifact_corruption_missing_extra_symlink_and_index(self):
        for mode in ("corrupt", "missing", "extra", "symlink", "sheet", "index"):
            with self.subTest(mode=mode):
                self.setUp()
                original = evidence.validate_inventory

                def alter(root, entries, frames, budget):
                    native = root / frames[0]["native"]["relative_path"]
                    if mode == "corrupt":
                        native.write_bytes(b"not PNG")
                    elif mode == "missing":
                        native.unlink()
                    elif mode == "extra":
                        (root / "unexpected").write_bytes(b"extra")
                    elif mode == "symlink":
                        native.unlink()
                        native.symlink_to(self.video)
                    elif mode == "sheet":
                        (root / "sheets/sheet-001.png").write_bytes(b"corrupt")
                    elif mode == "index":
                        (root / "frames.json").write_text("{}")
                    return original(root, entries, frames, budget)

                with patch.object(evidence, "validate_inventory", side_effect=alter):
                    with self.assertRaises(ValueError):
                        self.export(frame_indices=[0])
                self.assertFalse((self.output / "manifest.json").exists())

    def test_origin_rejections(self):
        original = deepcopy(self.record)
        for changes in (
            {"status": "failed"},
            {"status": "failed", "completed_artifact_version": 1},
            {"operation": "reinspect"},
            {"operation": "audio_preview"},
            {"run_dir": str(self.folder)},
            {"completed_artifact_version": True},
            {"source_sha256": "bad"},
            {"video": str(self.video)},
        ):
            self.record = dict(original, **changes)
            self.save_receipt()
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.export()
            self.assertFalse(self.output.exists())

    def test_paths_literal_percent_unicode_space_and_dash(self):
        self.output = self.folder / "-sample %03d 帧"
        mirror = self.folder / "-sample 001 帧"
        mirror.mkdir()
        sentinel = mirror / "frame-001.png"
        sentinel.write_bytes(b"owned elsewhere")
        self.export(frame_indices=[0, 44])
        self.assertEqual(sentinel.read_bytes(), b"owned elsewhere")
        with self.assertRaises(ValueError):
            self.export()
        self.output = self.run / "new"
        with self.assertRaises(ValueError):
            self.export()
        self.output = self.folder / "broken"
        self.output.symlink_to(self.folder / "missing")
        with self.assertRaises(ValueError):
            self.export()

    def test_native_failure_prevents_sheets_and_publication(self):
        for failure in (
            TimeoutError("timed out"),
            RuntimeError("decoder failed"),
            OSError("disk full"),
            InterruptedError("cancelled"),
        ):
            with self.subTest(failure=type(failure).__name__):
                self.setUp()
                with (
                    patch.object(evidence, "extract", side_effect=failure),
                    patch.object(evidence, "build_sheets") as sheets,
                ):
                    with self.assertRaises(type(failure)):
                        self.export()
                    sheets.assert_not_called()
                self.assertFalse((self.output / "manifest.json").exists())

    def test_final_diagnostic_write_and_close_failures_do_not_hide_incompleteness(self):
        original = evidence.DiagnosticTimeline
        for failure in (None, "final_write", "final_flush", "close"):
            with self.subTest(failure=failure):
                self.setUp()
                sinks = []

                class FailingSink:
                    def __init__(self, wrapped):
                        self.wrapped = wrapped
                        self.closed = False
                        self.final_event = False
                        self.close_count = 0

                    def write(self, value):
                        if self.closed:
                            raise AssertionError("diagnostic write after the final cutoff")
                        self.final_event = json.loads(value)["event"] == "publication_ready"
                        if self.final_event and failure == "final_write":
                            raise OSError("injected final diagnostic write failure")
                        return self.wrapped.write(value)

                    def flush(self):
                        if self.final_event and failure == "final_flush":
                            raise OSError("injected final diagnostic flush failure")
                        return self.wrapped.flush()

                    def close(self):
                        self.close_count += 1
                        self.wrapped.close()
                        self.closed = True
                        if failure == "close":
                            raise OSError("injected diagnostic close failure")

                def timeline(*args, **kwargs):
                    value = original(*args, **kwargs)
                    sink = FailingSink(value._file)
                    value._file = sink
                    sinks.append(sink)
                    return value

                with patch.object(evidence, "DiagnosticTimeline", side_effect=timeline):
                    result = self.export(frame_indices=[0])
                saved = json.loads((self.output / "manifest.json").read_text())
                self.assertEqual(saved, result)
                self.assertEqual(saved["status"], "success")
                self.assertEqual(saved["diagnostics_incomplete"], failure is not None)
                self.assertEqual(saved["diagnostics_cutoff"], "before_manifest_publication")
                self.assertEqual(len(sinks), 1)
                self.assertTrue(sinks[0].final_event)
                self.assertTrue(sinks[0].closed)
                self.assertEqual(sinks[0].close_count, 1)

    def test_diagnostics_closed_before_final_manifest_write_and_failed_publication(self):
        original = evidence.Storage.json
        logs = []
        timeline = evidence.DiagnosticTimeline

        def remember(*args, **kwargs):
            value = timeline(*args, **kwargs)
            logs.append(value)
            return value

        def fail_final_write(store, relative, value):
            if relative == ".manifest-complete.tmp":
                self.assertTrue(logs[0]._closed)
                self.assertEqual(value["diagnostics_cutoff"], "before_manifest_publication")
                raise OSError("injected final manifest write failure")
            return original(store, relative, value)

        with (
            patch.object(evidence, "DiagnosticTimeline", side_effect=remember),
            patch.object(evidence.Storage, "json", new=fail_final_write),
        ):
            with self.assertRaisesRegex(OSError, "final manifest write"):
                self.export(frame_indices=[0])
        self.assertFalse((self.output / "manifest.json").exists())
        events = [
            json.loads(line)
            for line in (self.output / "private/diagnostics.jsonl").read_text().splitlines()
        ]
        self.assertEqual(events[-1]["event"], "publication_ready")

    def test_publication_failure_no_final_manifest(self):
        with patch.object(evidence.os, "link", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.export(frame_indices=[0])
        self.assertFalse((self.output / "manifest.json").exists())
        self.assertEqual(
            json.loads((self.output / ".manifest-complete.tmp").read_text())["status"], "success"
        )

    def test_pixel_budget_before_extraction(self):
        with (
            patch.dict(evidence.LIMITS, selected_pixels=64 * 48 * 11),
            patch.object(evidence, "extract") as extract,
        ):
            with self.assertRaisesRegex(ValueError, "pixel budget"):
                self.export()
            extract.assert_not_called()

    def test_count_sentinel_stops_before_end(self):
        with (
            patch.dict(evidence.LIMITS, decoded_frames=10),
            patch.object(evidence, "probe_video", wraps=evidence.probe_video) as probe,
        ):
            with self.assertRaisesRegex(ValueError, "declared"):
                self.export()
            probe.assert_called_once()
        stream = {"width": 64, "height": 48}
        with patch.dict(evidence.LIMITS, decoded_frames=10):
            with self.assertRaisesRegex(ValueError, "sentinel"):
                evidence.count_frames(str(self.video), stream, Budget(10), 5, None)

    def test_fractional_and_vfr_metadata_do_not_claim_cfr(self):
        original = evidence.probe_video

        def variable(*args):
            stream, rate, declared = original(*args)
            stream["duration"] = "3.2"
            stream["avg_frame_rate"] = "30000/1001"
            return stream, Fraction(30000, 1001), declared

        with patch.object(evidence, "probe_video", side_effect=variable):
            result = self.export(frame_indices=[8])
        index = json.loads((self.output / "frames.json").read_text())
        self.assertEqual(index["frames"][0]["grid_time_seconds"], "1001/3750")
        self.assertFalse(result["cfr_verified"])

    def test_non_mp4_and_truncated_media_fail(self):
        for payload in (b"#EXTM3U\nfile:///etc/passwd\n", self.video.read_bytes()[:1024]):
            self.setUp()
            (self.run / "video.mp4").write_bytes(payload)
            self.record["video_sha256"] = digest(self.run / "video.mp4")
            self.save_receipt()
            with self.assertRaises((ValueError, RuntimeError)):
                self.export()
            self.assertFalse((self.output / "manifest.json").exists())

    def test_tool_command_has_no_image2_user_path_and_disables_references(self):
        command = evidence.media_command("/literal %03d/source.mp4", 13)
        self.assertNotIn("-vsync", command)
        self.assertIn("-nostdin", command)
        self.assertEqual(command[command.index("-enable_drefs") + 1], "0")
        self.assertEqual(command[command.index("-use_absolute_path") + 1], "0")
        self.assertEqual(command[command.index("-fps_mode") + 1], "passthrough")

    def test_embedded_bitmap_font_fallback(self):
        from PIL import ImageFont

        with patch.object(ImageFont, "load_default", side_effect=ImportError("no FreeType")):
            self.export(frame_indices=[0, 8, 44])
        self.assertTrue((self.output / "sheets/sheet-001.png").is_file())

    def test_declared_count_mismatch(self):
        original = evidence.probe_video

        def wrong(*args):
            stream, rate, declared = original(*args)
            return stream, rate, declared + 1

        with patch.object(evidence, "probe_video", side_effect=wrong):
            with self.assertRaisesRegex(ValueError, "disagree"):
                self.export()
        self.assertFalse((self.output / "manifest.json").exists())

    def test_size_preflight_precedes_source_hash(self):
        with (
            patch.dict(evidence.LIMITS, source_bytes=2),
            patch.object(evidence, "identity", wraps=evidence.identity) as observe,
        ):
            with self.assertRaisesRegex(ValueError, "byte limit"):
                self.export()
            self.assertEqual(observe.call_count, 1)  # Receipt only, no large input hashing.

    def test_actual_vfr_keeps_ordinals_without_cfr_claim(self):
        video = self.run / "video.mp4"
        video.unlink()
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=s=64x48:r=15:d=3",
                "-vf",
                "select=not(eq(mod(n\\,3)\\,1))",
                "-fps_mode",
                "vfr",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(video),
            ],
            check=True,
            capture_output=True,
            timeout=20,
        )
        self.record["video_sha256"] = digest(video)
        self.save_receipt()
        observed = json.loads(
            subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "frame=best_effort_timestamp_time",
                    "-of",
                    "json",
                    str(video),
                ],
                check=True,
                capture_output=True,
                timeout=20,
            ).stdout
        )
        pts = [Fraction(x["best_effort_timestamp_time"]) for x in observed["frames"]]
        self.assertGreater(len(set(b - a for a, b in zip(pts, pts[1:]))), 1)
        result = self.export(frame_indices=[0, 8, 29])
        self.assertEqual(result["decoded_frame_count"], 30)
        self.assertFalse(result["cfr_verified"])
        self.assertEqual(result["coverage"]["selected_indices"], [0, 8, 29])

    def test_120_distinct_noncontiguous_indices(self):
        video = self.run / "video.mp4"
        video.unlink()
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=s=64x48:r=60:d=4",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(video),
            ],
            check=True,
            capture_output=True,
            timeout=20,
        )
        self.record["video_sha256"] = digest(video)
        self.save_receipt()
        selected = list(range(0, 240, 2))
        result = self.export(frame_indices=selected)
        self.assertEqual(result["coverage"]["selected_indices"], selected)
        self.assertEqual(len(list((self.output / "sheets").glob("*.png"))), 10)

    def test_partial_png_write_keeps_no_manifest_or_sheet(self):
        from PIL import Image

        def failed(image, target, **kwargs):
            target.write(b"partial")
            raise OSError("simulated disk full")

        with patch.object(Image.Image, "save", new=failed):
            with self.assertRaises(OSError):
                self.export(frame_indices=[0])
        self.assertEqual((self.output / "native/frame-001.png").read_bytes(), b"partial")
        self.assertFalse((self.output / "manifest.json").exists())
        self.assertFalse(list((self.output / "sheets").iterdir()))


class MetadataPolicyTests(unittest.TestCase):
    def stream(self):
        return {
            "width": 1920,
            "height": 1080,
            "codec_name": "h264",
            "pix_fmt": "yuv420p",
            "sample_aspect_ratio": "1:1",
            "avg_frame_rate": "30000/1001",
            "r_frame_rate": "30/1",
            "duration": "3.2",
            "nb_frames": "90",
        }

    def probe(self, stream):
        with patch.object(
            evidence, "capture", return_value=json.dumps({"streams": [stream]}).encode()
        ):
            return evidence.probe_video("/not-read", Budget(2), 1, None)

    def test_metadata_and_rate_limits_and_unknown_sar(self):
        for change in (
            {"codec_name": "hevc"},
            {"pix_fmt": "yuv420p10le"},
            {"color_transfer": "smpte2084"},
            {"color_primaries": "bt2020"},
            {"sample_aspect_ratio": "4:3"},
            {"tags": {"rotate": "90"}},
            {"side_data_list": [{"rotation": 90}]},
            {"width": 3841},
            {"height": 2161},
            {"duration": "120.001"},
            {"duration": "NaN"},
            {"avg_frame_rate": "0/0"},
            {"avg_frame_rate": "61/1"},
            {"nb_frames": "7201"},
            {"nb_frames": "junk"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.probe(dict(self.stream(), **change))
        s = self.stream()
        del s["sample_aspect_ratio"]
        actual, rate, declared = self.probe(s)
        self.assertNotIn("sample_aspect_ratio", actual)
        self.assertEqual(rate, Fraction(30000, 1001))
        self.assertEqual(declared, 90)
        for changes in (
            {"width": 3840, "height": 2160},
            {"duration": "120"},
            {"avg_frame_rate": "60/1"},
            {"nb_frames": "7200"},
        ):
            self.probe(dict(self.stream(), **changes))

    def test_count_limit_applied_to_command_before_decode(self):
        calls = []

        def fake(command, *args, **kwargs):
            count = int(command[command.index("-frames:v") + 1])
            calls.append(count)
            return f"frame={count}\nprogress=end\n".encode()

        with patch.object(evidence, "capture", side_effect=fake):
            with self.assertRaisesRegex(ValueError, "sentinel"):
                evidence.count_frames(
                    "/not-read", {"width": 3840, "height": 2160}, Budget(2), 1, None
                )
        self.assertLess(calls[0], 7201)
        self.assertLessEqual(
            (2 * calls[0] + 2 * evidence.LOOKAHEAD + 600) * 3840 * 2160,
            evidence.LIMITS["pixel_work"],
        )


if __name__ == "__main__":
    unittest.main()
