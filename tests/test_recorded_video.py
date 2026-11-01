"""Recorded-video recovery: synthetic media, no real rendering or benchmarks."""

import json
from pathlib import Path
import shutil
import subprocess
import threading
import unittest
from unittest.mock import patch

import tests.test_scene_production as fixtures
from scene_production import digest, render
from scene_production.reinspection import reinspect


class RecordedVideoTests(unittest.TestCase):
    def setUp(self):
        fixtures.PipelineTests.setUp(self)
        self.target = self.root / "rechecked"

    def make(self, failure=None):
        def inspect(command, timeout):
            if command[0] == "ffprobe" and failure:
                raise failure
            return fixtures.PipelineTests.fake_tool(command, timeout)

        with patch("scene_production.tool", side_effect=inspect):
            try:
                result = render(
                    self.source,
                    "Demo",
                    self.timeline,
                    self.output,
                    runner=fixtures.PipelineTests.runner(self),
                )
            except Exception as error:
                if not failure:
                    raise
                self.assertIs(error, failure)
                return Path(error.receipt_path)
        return Path(result["run_dir"]) / "result.json"

    def check(self, path):
        return reinspect(path, self.timeline, self.target)

    def read_failure(self):
        return json.loads(next(self.target.glob("*/result.json")).read_text())

    def test_flat_identity_is_durable_before_probe_and_old_failed_is_rejected(self):
        def failing(command, timeout):
            saved = json.loads(next(self.output.glob("*/result.json")).read_text())
            self.assertEqual(saved["completed_artifact_version"], 1)
            self.assertEqual(saved["status"], "failed")
            self.assertEqual(saved["video_sha256"], digest(saved["video"]))
            raise RuntimeError("probe failed")

        with patch("scene_production.tool", side_effect=failing):
            with self.assertRaises(RuntimeError) as error:
                render(
                    self.source,
                    "Demo",
                    self.timeline,
                    self.output,
                    runner=fixtures.PipelineTests.runner(self),
                )
        path = Path(error.exception.receipt_path)
        original = json.loads(path.read_text())
        self.assertEqual(original["stage"], "probe")
        for marker in (None, True, 2):
            changed = dict(original)
            if marker is None:
                changed.pop("completed_artifact_version")
            else:
                changed["completed_artifact_version"] = marker
            path.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):
                self.check(path)
            self.assertFalse(self.target.exists())

    def test_no_identity_for_cancel_failed_render_missing_video_or_source_mutation(self):
        for mode in ("missing", "mutate", "timeout"):
            self.output = self.root / mode
            event = threading.Event()
            if mode == "timeout":
                event.set()
            with self.assertRaises(RuntimeError):
                render(
                    self.source,
                    "Demo",
                    self.timeline,
                    self.output,
                    cancel_event=event,
                    runner=fixtures.PipelineTests.runner(self, mode),
                )
            saved = json.loads(next(self.output.glob("*/result.json")).read_text())
            self.assertNotIn("completed_artifact_version", saved)

        class Failed:
            def run_job(self, *args):
                raise OSError("could not render")

        with self.assertRaises(OSError) as error:
            render(self.source, "Demo", self.timeline, self.root / "failed", runner=Failed())
        self.assertNotIn(
            "completed_artifact_version", json.loads(Path(error.exception.receipt_path).read_text())
        )

    def test_legacy_success_recovery_isolated_repeated_sequential_no_current_source_execution(self):
        path = self.make()
        saved = json.loads(path.read_text())
        saved.pop("completed_artifact_version")
        path.write_text(json.dumps(saved))
        self.source.write_text("invalid source ! raise AssertionError('must not execute')")
        before = {p: digest(p) for p in path.parent.rglob("*") if p.is_file()}
        self.timeline.write_text(
            '{"version":1,"segments":[{"label":"fixed","start":0,"end":1,"samples":[0.4]}]}'
        )
        with (
            patch("scene_production.tool", side_effect=fixtures.PipelineTests.fake_tool) as calls,
            patch("scene_production.RenderRunner.run_job", side_effect=AssertionError("no render")),
            patch("scene_production.ast.parse", side_effect=AssertionError("no discovery")),
        ):
            result = self.check(path)
            repeated = self.check(path)
        self.assertNotEqual(result["run_dir"], repeated["run_dir"])
        self.assertEqual(result["operation"], "reinspect")
        self.assertIn("不应用源代码修改", result["notice"])
        self.assertNotIn("command", result)
        self.assertEqual(result["origin_receipt_sha256"], digest(path))
        self.assertEqual(result["video_sha256"], saved["video_sha256"])
        self.assertEqual(result["timeline_sha256"], digest(self.timeline))
        self.assertEqual([f["time"] for f in result["frames"]], [0.1, 0.4, 0.9])
        for frame in result["frames"]:
            self.assertEqual(frame["sha256"], digest(frame["path"]))
        commands = [c.args[0] for c in calls.call_args_list]
        self.assertEqual(sum(c[-1] == "-" for c in commands), 2)
        self.assertEqual(sum(c.count("-ss") == 1 for c in commands), 6)
        self.assertEqual(before, {p: digest(p) for p in before})
        with self.assertRaises(ValueError):
            self.check(Path(result["run_dir"]) / "result.json")  # no chain

    def test_missing_corrupt_symlink_root_and_bounded_json_are_rejected_before_output(self):
        path = self.make(RuntimeError("inspection failed"))
        original = path.read_bytes()
        for name in ("source.py", "timeline.json", None):
            target = path.parent / name if name else Path(json.loads(original)["video"])
            raw = target.read_bytes()
            for mode in ("missing", "corrupt", "symlink"):
                target.unlink()
                if mode == "corrupt":
                    target.write_bytes(raw + b"wrong")
                elif mode == "symlink":
                    outside = self.root / "outside"
                    outside.write_bytes(raw)
                    target.symlink_to(outside)
                with self.assertRaises((ValueError, OSError)):
                    self.check(path)
                self.assertFalse(self.target.exists())
                target.unlink(missing_ok=True)
                target.write_bytes(raw)
        invalid = [b"{", b'{"status":1,"status":2}', b'{"value":NaN}', b" " * (2 * 1024 * 1024 + 1)]
        wrong_root = json.loads(original)
        wrong_root["run_dir"] = str(self.root)
        invalid.append(json.dumps(wrong_root).encode())
        for raw in invalid:
            path.write_bytes(raw)
            with self.assertRaises(ValueError):
                self.check(path)
            self.assertFalse(self.target.exists())
        path.write_bytes(original)

    def test_copy_or_origin_changes_and_inspection_video_changes_fail(self):
        path = self.make()
        old_video = Path(json.loads(path.read_text())["video"])
        original = old_video.read_bytes()
        copy = shutil.copyfile
        for mode in ("copy", "origin"):
            self.target = self.root / mode

            def copying(source, target):
                result = copy(source, target)
                if Path(target).suffix == ".mp4":
                    (Path(target) if mode == "copy" else old_video).write_bytes(b"changed")
                return result

            with patch("scene_production.reinspection.shutil.copyfile", side_effect=copying):
                with self.assertRaises(ValueError):
                    self.check(path)
            self.assertEqual(self.read_failure()["stage"], "copy")
            old_video.write_bytes(original)
        self.target = self.root / "decode-change"

        def mutating(command, timeout):
            value = fixtures.PipelineTests.fake_tool(command, timeout)
            if command[-1] == "-":
                Path(command[command.index("-i") + 1]).write_bytes(b"changed")
            return value

        with patch("scene_production.tool", side_effect=mutating):
            with self.assertRaises(ValueError):
                self.check(path)
        self.assertEqual(self.read_failure()["status"], "failed")

    def test_probe_decode_frames_errors_timeouts_preserve_exception_and_failed_receipt(self):
        path = self.make(RuntimeError("old inspection failed"))
        for stage in ("probe", "decode", "frames"):
            for failure in (RuntimeError("bad media"), subprocess.TimeoutExpired("tool", 1)):
                self.target = self.root / (stage + type(failure).__name__)

                def failing(command, timeout):
                    actual = (
                        "probe"
                        if command[0] == "ffprobe"
                        else ("decode" if command[-1] == "-" else "frames")
                    )
                    if actual == stage:
                        raise failure
                    return fixtures.PipelineTests.fake_tool(command, timeout)

                with patch("scene_production.tool", side_effect=failing):
                    with self.assertRaises(type(failure)) as error:
                        self.check(path)
                self.assertIs(error.exception, failure)
                self.assertEqual(self.read_failure()["stage"], stage)
                self.assertEqual(self.read_failure()["status"], "failed")
                self.assertEqual(Path(error.exception.receipt_path).name, "result.json")

    def test_still_wrong_new_timeline_and_persistence_failure_are_explicit(self):
        path = self.make()
        raw = self.timeline.read_bytes()
        self.timeline.write_text('{"version":1,"segments":[{"label":"wrong","start":0,"end":2}]}')
        with patch("scene_production.tool", side_effect=fixtures.PipelineTests.fake_tool):
            with self.assertRaises(ValueError):
                self.check(path)
        self.assertEqual(self.read_failure()["stage"], "probe")
        self.timeline.write_bytes(raw)
        self.target = self.root / "cannot-save"
        original_replace = Path.replace
        writes = 0
        problem = RuntimeError("original probe failure")

        def replacing(path, target):
            nonlocal writes
            writes += 1
            if writes > 1:
                raise PermissionError("cannot publish failed receipt")
            return original_replace(path, target)

        with (
            patch("pathlib.Path.replace", replacing),
            patch("scene_production.tool", side_effect=problem),
        ):
            with self.assertRaises(PermissionError) as error:
                self.check(path)
        self.assertIs(error.exception.__cause__, problem)
        persisted = json.loads(next(self.target.glob("*/result.json")).read_text())
        self.assertEqual(persisted["status"], "failed")

    def test_cli_inspect_failure_receipt_and_legacy_flags(self):
        from scene_production.__main__ import main

        path = self.make()
        argv = [
            "scene_production",
            "inspect",
            "--from-run",
            str(path),
            "--timeline",
            str(self.timeline),
            "--output-dir",
            str(self.target),
        ]
        with (
            patch("sys.argv", argv),
            patch("scene_production.tool", side_effect=RuntimeError("check")),
            patch("builtins.print") as printed,
        ):
            self.assertEqual(main(), 1)
        self.assertTrue(any(str(c.args[0]).startswith("receipt: ") for c in printed.call_args_list))
        argv = [
            "scene_production",
            "--scene-file",
            "scene.py",
            "--scene",
            "Demo",
            "--timeline",
            "timeline.json",
            "--output-dir",
            "out",
        ]
        with (
            patch("sys.argv", argv),
            patch("scene_production.__main__.render", return_value={}) as run,
            patch("builtins.print"),
        ):
            self.assertEqual(main(), 0)
            self.assertEqual(run.call_args.kwargs["scene"], "Demo")

    def test_final_publication_failure_stage_checkpoint_and_cause(self):
        origin = self.make()
        replace = Path.replace
        for operation in ("render", "reinspect"):
            for persistent in (False, True):
                directory = self.root / (operation + str(persistent))
                problem = PermissionError("final publication denied")
                underlying = OSError("underlying disk condition")
                problem.__cause__ = underlying
                attempts = 0

                def replacing(path, target):
                    nonlocal attempts
                    attempts += 1
                    if attempts == 2:
                        raise problem
                    if persistent and attempts > 2:
                        raise PermissionError("failed receipt publication denied")
                    return replace(path, target)

                with (
                    patch("pathlib.Path.replace", replacing),
                    patch("scene_production.tool", side_effect=fixtures.PipelineTests.fake_tool),
                ):
                    with self.assertRaises(PermissionError) as error:
                        if operation == "render":
                            render(
                                self.source,
                                "Demo",
                                self.timeline,
                                directory,
                                runner=fixtures.PipelineTests.runner(self),
                            )
                        else:
                            reinspect(origin, self.timeline, directory)
                canonical = next(directory.glob("*/result.json"))
                saved = json.loads(canonical.read_text())
                self.assertEqual(saved["status"], "failed")
                self.assertEqual(saved["video_sha256"], digest(saved["video"]))
                if operation == "render":
                    self.assertEqual(saved["completed_artifact_version"], 1)
                if persistent:
                    self.assertIs(error.exception.__cause__, problem)
                    self.assertIn("failed receipt publication", str(error.exception))
                else:
                    self.assertIs(error.exception, problem)
                    self.assertIs(error.exception.__cause__, underlying)
                    self.assertEqual(saved["stage"], "publication")
                    self.assertEqual(
                        Path(error.exception.receipt_path).resolve(), canonical.resolve()
                    )
