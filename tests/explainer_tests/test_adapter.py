"""Tests for explainer.render.adapter -- the verify -> codegen -> render orchestrator.

These run WITHOUT a real manim (the runner is faked), so they cover the seams the
review flagged as untested: resolution->quality mapping, the citation gate firing
BEFORE any render side effect, theme-derived quality, and child-only PYTHONPATH.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from app.render.aggregation import JobOutcome

from explainer.cite.verifier import CiteFailure
from explainer.render.adapter import (
    CitationGateError,
    REPO_ROOT,
    RenderError,
    render_storyboard,
    resolution_to_quality,
)

EXAMPLE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "examples",
    "evidence-budget.story.yaml",
)


class ResolutionToQualityTests(unittest.TestCase):
    def test_known_resolutions(self):
        self.assertEqual(resolution_to_quality("480p15"), "-ql")
        self.assertEqual(resolution_to_quality("720p30"), "-qm")
        self.assertEqual(resolution_to_quality("1080p30"), "-qh")
        self.assertEqual(resolution_to_quality("1440p60"), "-qp")
        self.assertEqual(resolution_to_quality("2160p60"), "-qk")

    def test_unknown_defaults_to_medium(self):
        self.assertEqual(resolution_to_quality("weird"), "-qm")
        self.assertEqual(resolution_to_quality(""), "-qm")
        self.assertEqual(resolution_to_quality(None), "-qm")
        self.assertEqual(resolution_to_quality("\u00b2p30"), "-qm")  # isdigit-but-not-int


class _FakeResult:
    def __init__(self, outcome):
        self.outcome = outcome
        self.return_code = 0 if outcome == JobOutcome.SUCCESS else 1
        self.diagnostic = ""
        self.render_cleanup_state = "complete"


class _FakeRunner:
    """Records what the adapter handed the runner, returns SUCCESS -- no manim."""

    last = {}

    def run_job(self, job, command, cancel_event=None, on_log=None, env=None):
        _FakeRunner.last = {
            "command": list(command),
            "parent_pythonpath": os.environ.get("PYTHONPATH"),
            "env": dict(env or {}),
        }
        if on_log is not None:
            on_log("fake child output")
        return _FakeResult(JobOutcome.SUCCESS)


class _ExplodingRunner:
    def __init__(self, *a, **k):
        raise AssertionError("runner must NOT be constructed when the citation gate fails")


class _ProducingRunner(_FakeRunner):
    """Write real test artifacts so publication and receipt hashing are exercised."""

    video = b"first test video"
    subtitles = None

    def run_job(self, job, command, cancel_event=None, on_log=None, env=None):
        result = super().run_job(job, command, cancel_event, on_log, env)
        media = Path(command[command.index("--media_dir") + 1])
        target = media / "videos" / "explainer_scene" / "1080p60" / "ExplainerVideo.mp4"
        target.parent.mkdir(parents=True)
        target.write_bytes(self.video)
        if self.subtitles is not None:
            target.with_suffix(".srt").write_bytes(self.subtitles)
        return result


class _RaisingRunner:
    def run_job(self, job, command, cancel_event=None, on_log=None, env=None):
        raise RuntimeError("boom mid-render")


class AdapterOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = str(self.root / "output.mp4")
        self.logs = []

    def test_uncertain_results_and_exceptions_do_not_publish(self):
        for state in (None, "invalid", "unknown", "not_started", "complete"):
            for raises, outcome, code in (
                (False, JobOutcome.SUCCESS, 0),
                (False, JobOutcome.SUCCESS, None),
                (False, JobOutcome.FAILED, 1),
                (False, JobOutcome.FAILED, None),
                (False, JobOutcome.MANIM_NOT_FOUND, None),
                (True, JobOutcome.FAILED, None),
            ):
                with self.subTest(state=state, raises=raises, outcome=outcome, code=code):
                    result = _FakeResult(outcome)
                    result.return_code = code
                    result.render_cleanup_state = state
                    error = RuntimeError("runner failed")
                    error.render_cleanup_state = state
                    from explainer.render import adapter
                    import shutil

                    workdir = self.root / (str(state) + str(raises))
                    workdir.mkdir()
                    with (
                        patch.object(adapter, "verify_storyboard", return_value=[]),
                        patch.object(adapter, "manim_available", return_value=True),
                        patch.object(adapter.tempfile, "mkdtemp", return_value=str(workdir)),
                        patch.object(adapter, "_diagnostic_directory", return_value=None),
                        patch.object(adapter, "RenderRunner") as runner,
                        patch.object(adapter.shutil, "copyfile") as publish,
                        patch("subprocess.Popen", side_effect=AssertionError("no child")),
                        patch("subprocess.run", side_effect=AssertionError("no command")),
                        patch("os.kill", side_effect=AssertionError("no signal")),
                        patch("os.killpg", side_effect=AssertionError("no signal"), create=True),
                    ):
                        runner.return_value.run_job.side_effect = error if raises else None
                        runner.return_value.run_job.return_value = result
                        with self.assertRaises(Exception) as caught:
                            render_storyboard(EXAMPLE, self.output, on_log=self.logs.append)
                        expected = (
                            state
                            if state == "complete"
                            or (
                                state == "not_started"
                                and (
                                    raises
                                    or (
                                        outcome in (JobOutcome.FAILED, JobOutcome.MANIM_NOT_FOUND)
                                        and code is None
                                    )
                                )
                            )
                            else "unknown"
                        )
                        self.assertEqual(caught.exception.render_cleanup_state, expected)
                        publish.assert_not_called()
                        if expected == "unknown":
                            self.assertTrue(workdir.is_dir())
                    shutil.rmtree(workdir, ignore_errors=True)

    def test_unknown_cleanup_keeps_diagnostic_and_result_event_without_publication(self):
        from explainer.render import adapter

        for outcome in (JobOutcome.SUCCESS, JobOutcome.FAILED):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as output_root:
                diagnostic = "owned group probe failed: fixture detail"

                class UncertainRunner(_ProducingRunner):
                    def run_job(self, *args, **kwargs):
                        result = super().run_job(*args, **kwargs)
                        result.render_cleanup_state = "unknown"
                        result.outcome = outcome
                        result.return_code = 0 if outcome == JobOutcome.SUCCESS else -9
                        result.diagnostic = diagnostic
                        return result

                output = Path(output_root) / "output.mp4"
                output.write_bytes(b"previous published video")
                with (
                    patch.object(adapter, "verify_storyboard", return_value=[]),
                    patch.object(adapter, "manim_available", return_value=True),
                    patch.object(adapter, "RenderRunner", UncertainRunner),
                    patch.object(adapter.shutil, "copyfile") as publish,
                    patch("subprocess.Popen", side_effect=AssertionError("no child")),
                    patch("subprocess.run", side_effect=AssertionError("no command")),
                    patch("os.kill", side_effect=AssertionError("no signal")),
                    patch("os.killpg", side_effect=AssertionError("no signal"), create=True),
                ):
                    with self.assertRaises(RenderError) as caught:
                        render_storyboard(EXAMPLE, str(output), on_log=self.logs.append)
                error = caught.exception
                self.addCleanup(shutil.rmtree, error.workdir, True)
                self.assertEqual(error.render_cleanup_state, "unknown")
                self.assertTrue(Path(error.workdir).is_dir())
                self.assertTrue(list(Path(error.workdir).rglob("ExplainerVideo.mp4")))
                publish.assert_not_called()
                self.assertEqual(output.read_bytes(), b"previous published video")
                directory = Path(error.diagnostics_dir)
                self.assertIn(diagnostic, (directory / "render.log").read_text())
                self.assertIn(diagnostic, self.logs)
                self.assertFalse((directory / "result.json").exists())
                events = [
                    json.loads(line)
                    for line in (directory / "diagnostics.jsonl").read_text().splitlines()
                ]
                result_event = next(e for e in events if e["event"] == "render_result")
                self.assertEqual(result_event["outcome"], outcome.value)
                self.assertEqual(result_event["render_cleanup_state"], "unknown")
                self.assertEqual(
                    result_event["return_code"], 0 if outcome == JobOutcome.SUCCESS else -9
                )
                self.assertNotIn(diagnostic, (directory / "diagnostics.jsonl").read_text())
                self.assertLess(
                    events.index(result_event),
                    next(i for i, e in enumerate(events) if e["event"] == "operation_failed"),
                )

    def diagnostic_directory(self):
        directories = list((self.root / ".explainer-logs").glob("run-*"))
        self.assertEqual(len(directories), 1)
        return directories[0]

    def diagnostic_events(self):
        return [
            json.loads(line)
            for line in (self.diagnostic_directory() / "diagnostics.jsonl").read_text().splitlines()
        ]

    @patch("explainer.render.adapter.RenderRunner", _ExplodingRunner)
    @patch("explainer.render.adapter.verify_storyboard")
    def test_citation_gate_fires_before_any_render(self, mock_verify):
        mock_verify.return_value = [CiteFailure("b", "evidence.py:1", ("24",))]
        prev = os.environ.get("PYTHONPATH")
        with self.assertRaises(CitationGateError):
            render_storyboard(EXAMPLE, self.output, on_log=self.logs.append)
        # gate fired before the runner was built AND before PYTHONPATH was touched
        self.assertEqual(os.environ.get("PYTHONPATH"), prev)
        self.assertEqual(list(self.root.iterdir()), [])

    @patch("explainer.render.adapter.manim_available", return_value=True)
    @patch("explainer.render.adapter.shutil.copyfile")
    @patch("explainer.render.adapter.glob.glob", return_value=["/tmp/x/ExplainerVideo.mp4"])
    @patch("explainer.render.adapter.RenderRunner", _FakeRunner)
    @patch("explainer.render.adapter.verify_storyboard", return_value=[])
    def test_quality_derives_from_theme_and_pythonpath_is_child_only(self, *_mocks):
        prev = os.environ.get("PYTHONPATH")
        os.environ.pop("PYTHONPATH", None)
        try:
            render_storyboard(EXAMPLE, self.output, on_log=self.logs.append)  # quality omitted
            self.assertIn("-qh", _FakeRunner.last["command"])  # theme 1080p30 -> -qh
            self.assertIn(REPO_ROOT, _FakeRunner.last["env"]["PYTHONPATH"])  # repo on child path
            self.assertNotIn("PYTHONPATH", os.environ)  # restored to "unset"
        finally:
            if prev is not None:
                os.environ["PYTHONPATH"] = prev
            else:
                os.environ.pop("PYTHONPATH", None)

    @patch("explainer.render.adapter.manim_available", return_value=True)
    @patch("explainer.render.adapter.shutil.copyfile")
    @patch("explainer.render.adapter.glob.glob", return_value=["/tmp/x/ExplainerVideo.mp4"])
    @patch("explainer.render.adapter.RenderRunner", _FakeRunner)
    @patch("explainer.render.adapter.verify_storyboard", return_value=[])
    def test_render_uses_child_only_pythonpath(self, *_mocks):
        with patch.dict(
            os.environ,
            {"PYTHONPATH": "parent-only", "ADAPTER_SENTINEL": "preserve-me"},
            clear=False,
        ):
            render_storyboard(EXAMPLE, self.output, on_log=self.logs.append)

            self.assertEqual(os.environ["PYTHONPATH"], "parent-only")
            self.assertEqual(_FakeRunner.last["parent_pythonpath"], "parent-only")
            self.assertEqual(
                _FakeRunner.last["env"]["PYTHONPATH"],
                REPO_ROOT + os.pathsep + "parent-only",
            )
            self.assertEqual(_FakeRunner.last["env"]["ADAPTER_SENTINEL"], "preserve-me")

    @patch("explainer.render.adapter.manim_available", return_value=True)
    @patch("explainer.render.adapter.shutil.rmtree", wraps=shutil.rmtree)
    @patch("explainer.render.adapter.RenderRunner", _RaisingRunner)
    @patch("explainer.render.adapter.verify_storyboard", return_value=[])
    def test_workdir_cleaned_on_unexpected_error(self, _mock_verify, _mock_available, mock_rmtree):
        # ARCH-08: an unexpected error mid-render must clean the temp workdir
        # (RenderError still keeps it for debugging; this is a non-RenderError path).
        with self.assertRaises(RuntimeError):
            render_storyboard(EXAMPLE, self.output, on_log=self.logs.append)
        self.assertTrue(mock_rmtree.called)
        directory = self.diagnostic_directory()
        self.assertIn("boom mid-render", (directory / "render.log").read_text())
        self.assertNotIn("boom mid-render", (directory / "diagnostics.jsonl").read_text())
        events = self.diagnostic_events()
        self.assertTrue(
            any(event["event"] == "phase_failed" and event["phase"] == "render" for event in events)
        )

    @patch("explainer.render.adapter.tempfile.mkdtemp")
    @patch("explainer.render.adapter.manim_available", return_value=False)
    @patch("explainer.render.adapter.RenderRunner", _ExplodingRunner)
    @patch("explainer.render.adapter.verify_storyboard", return_value=[])
    def test_missing_manim_is_reported_before_any_render(
        self, _mock_verify, _mock_available, mock_mkdtemp
    ):
        # The light ``.[verify]`` install has no manim. Say so, with the install
        # command, instead of spawning ``python -m manim`` to fail on import and
        # leaving a "generated scene kept at ..." workdir behind.
        with self.assertRaises(RenderError) as ctx:
            render_storyboard(EXAMPLE, self.output, on_log=self.logs.append)
        self.assertEqual(ctx.exception.outcome, "manim_not_installed")
        self.assertIsNone(ctx.exception.workdir)
        self.assertIn('pip install -e ".[explainer]"', str(ctx.exception))
        self.assertFalse(mock_mkdtemp.called)
        self.assertEqual(list(self.root.iterdir()), [])

    @patch("explainer.render.adapter.manim_available", return_value=True)
    @patch("explainer.render.adapter.shutil.copyfile")
    @patch("explainer.render.adapter.glob.glob", return_value=["/tmp/x/ExplainerVideo.mp4"])
    @patch("explainer.render.adapter.RenderRunner", _FakeRunner)
    @patch("explainer.render.adapter.verify_storyboard", return_value=[])
    def test_success_retains_private_logs_after_temporary_media_cleanup(self, *_mocks):
        with patch.dict(os.environ, {"PRIVATE_TEST_KEY": "private-value-never-recorded"}):
            result = render_storyboard(EXAMPLE, self.output, on_log=self.logs.append)
        self.assertEqual(result, self.output)
        directory = self.diagnostic_directory()
        self.assertIn("fake child output", (directory / "render.log").read_text())
        events = self.diagnostic_events()
        self.assertEqual(
            [event["phase"] for event in events if event["event"] == "phase_started"],
            ["prepare", "render", "artifact", "publication"],
        )
        self.assertTrue(any(event["event"] == "operation_finished" for event in events))
        self.assertNotIn("private-value-never-recorded", json.dumps(events))
        self.assertNotIn("fake child output", json.dumps(events))
        self.assertIn("fake child output", self.logs)
        scene = Path(_FakeRunner.last["command"][_FakeRunner.last["command"].index("-qh") - 2])
        self.assertFalse(scene.exists())
        if os.name == "posix":
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual((directory / "render.log").stat().st_mode & 0o777, 0o600)

    @patch("explainer.render.adapter.manim_available", return_value=True)
    @patch("explainer.render.adapter.shutil.copyfile")
    @patch("explainer.render.adapter.glob.glob", return_value=["/tmp/x/ExplainerVideo.mp4"])
    @patch("explainer.render.adapter.RenderRunner", _FakeRunner)
    @patch("explainer.render.adapter.verify_storyboard", return_value=[])
    def test_log_directory_failure_warns_and_render_still_completes(self, *_mocks):
        # A file occupying the optional log directory must not break rendering.
        (self.root / ".explainer-logs").write_text("occupied")
        result = render_storyboard(EXAMPLE, self.output, on_log=self.logs.append)
        self.assertEqual(result, self.output)
        self.assertIn("Warning: persistent render diagnostics unavailable.", self.logs)
        self.assertIn("fake child output", self.logs)

    @patch("explainer.render.adapter.manim_available", return_value=True)
    @patch("explainer.render.adapter.RenderRunner", _ProducingRunner)
    @patch("explainer.render.adapter.verify_storyboard", return_value=[])
    def test_receipts_distinguish_outputs_and_survive_target_overwrite(self, *_mocks):
        calls = [
            (self.output, b"first video"),
            (str(self.root / "second.mp4"), b"second video"),
            (self.output, b"replacement video"),
        ]
        for output, video in calls:
            with patch.object(_ProducingRunner, "video", video):
                self.assertEqual(
                    render_storyboard(EXAMPLE, output, on_log=self.logs.append), output
                )
        directories = list((self.root / ".explainer-logs").glob("run-*"))
        self.assertEqual(len(directories), 3)
        receipts = {}
        for directory in directories:
            receipt_path = directory / "result.json"
            receipt = json.loads(receipt_path.read_text())
            self.assertEqual(receipt["status"], "succeeded")
            self.assertEqual(receipt["run_id"], directory.name)
            self.assertEqual(receipt["operation"], "explainer_render")
            self.assertIn("observed_at", receipt)
            implementation_path = directory / "implementation.json"
            implementation = json.loads(implementation_path.read_text())
            self.assertEqual(implementation["operation"], "explainer_render")
            self.assertEqual(implementation["run_id"], receipt["run_id"])
            self.assertEqual(implementation["implementation"]["source"]["status"], "observed")
            video = receipt["artifacts"]["video"]
            receipts[video["sha256"]] = video
            events = (directory / "diagnostics.jsonl").read_text()
            self.assertNotIn(str(self.root), events)
            self.assertTrue(
                any(
                    event["event"] == "artifact_binding" and event["available"]
                    for event in map(json.loads, events.splitlines())
                )
            )
            if os.name == "posix":
                self.assertEqual(receipt_path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(implementation_path.stat().st_mode & 0o777, 0o600)
        for output, data in calls:
            self.assertEqual(
                receipts[hashlib.sha256(data).hexdigest()],
                {
                    "path": output,
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                },
            )
        self.assertEqual(Path(self.output).read_bytes(), calls[-1][1])

    @patch("explainer.render.adapter.manim_available", return_value=True)
    @patch("explainer.render.adapter.RenderRunner", _ProducingRunner)
    @patch("explainer.render.adapter.verify_storyboard", return_value=[])
    def test_receipt_binds_only_subtitles_copied_by_this_run(self, *_mocks):
        subtitles = b"1\n00:00:00,000 --> 00:00:01,000\nCaption\n"
        with patch.object(_ProducingRunner, "subtitles", subtitles):
            render_storyboard(EXAMPLE, self.output, on_log=self.logs.append)
        receipt = json.loads((self.diagnostic_directory() / "result.json").read_text())
        self.assertEqual(
            receipt["artifacts"]["subtitles"],
            {
                "path": str(Path(self.output).with_suffix(".srt")),
                "size_bytes": len(subtitles),
                "sha256": hashlib.sha256(subtitles).hexdigest(),
            },
        )
        # An old SRT at the destination must not become an artifact of a later
        # render that produced no subtitles.
        previous = self.diagnostic_directory()
        render_storyboard(EXAMPLE, self.output, on_log=self.logs.append)
        latest = next(path for path in previous.parent.iterdir() if path != previous)
        self.assertNotIn("subtitles", json.loads((latest / "result.json").read_text())["artifacts"])

    @patch("explainer.render.adapter.manim_available", return_value=True)
    @patch("explainer.render.adapter.RenderRunner", _ProducingRunner)
    @patch("explainer.render.adapter.verify_storyboard", return_value=[])
    def test_replaced_publication_is_not_bound_to_this_run(self, *_mocks):
        original_copy = shutil.copyfile
        for suffix in (".mp4", ".srt"):
            with self.subTest(replaced=suffix):
                before = set((self.root / ".explainer-logs").glob("run-*"))

                def replaced_copy(source, destination):
                    result = original_copy(source, destination)
                    if Path(destination).suffix == suffix:
                        Path(destination).write_bytes(b"OTHER_RENDER_OUTPUT")
                    return result

                with (
                    patch("explainer.render.adapter.shutil.copyfile", side_effect=replaced_copy),
                    patch.object(_ProducingRunner, "subtitles", b"this run's subtitles"),
                ):
                    result = render_storyboard(EXAMPLE, self.output, on_log=self.logs.append)
                self.assertEqual(result, self.output)
                directory = (set((self.root / ".explainer-logs").glob("run-*")) - before).pop()
                self.assertFalse((directory / "result.json").exists())
                events = [
                    json.loads(line)
                    for line in (directory / "diagnostics.jsonl").read_text().splitlines()
                ]
                self.assertEqual(
                    [
                        event["available"]
                        for event in events
                        if event["event"] == "artifact_binding"
                    ],
                    [False],
                )
                self.assertTrue(any(event["event"] == "operation_finished" for event in events))
                self.assertIn("Warning: persistent render artifact binding unavailable.", self.logs)

    @patch("explainer.render.adapter.manim_available", return_value=True)
    @patch("explainer.render.adapter.RenderRunner", _ProducingRunner)
    @patch("explainer.render.adapter.verify_storyboard", return_value=[])
    def test_source_snapshot_failure_does_not_block_publication(self, *_mocks):
        from explainer.render.adapter import _artifact_binding

        def unavailable_source(path):
            if Path(path).name == "ExplainerVideo.mp4":
                raise PermissionError("private source details")
            return _artifact_binding(path)

        with patch("explainer.render.adapter._artifact_binding", side_effect=unavailable_source):
            self.assertEqual(
                render_storyboard(EXAMPLE, self.output, on_log=self.logs.append), self.output
            )
        self.assertEqual(Path(self.output).read_bytes(), _ProducingRunner.video)
        self.assertFalse((self.diagnostic_directory() / "result.json").exists())
        self.assertIn("Warning: persistent render artifact binding unavailable.", self.logs)
        self.assertNotIn("private source details", "\n".join(self.logs))

    @patch("explainer.render.adapter.manim_available", return_value=True)
    @patch("explainer.render.adapter.RenderRunner", _ProducingRunner)
    @patch("explainer.render.adapter.verify_storyboard", return_value=[])
    def test_receipt_write_failure_keeps_success_and_removes_partial_file(self, *_mocks):
        with patch(
            "explainer.render.adapter.os.replace", side_effect=OSError("private disk details")
        ):
            result = render_storyboard(EXAMPLE, self.output, on_log=self.logs.append)
        self.assertEqual(result, self.output)
        self.assertEqual(Path(self.output).read_bytes(), _ProducingRunner.video)
        directory = self.diagnostic_directory()
        self.assertFalse((directory / "result.json").exists())
        self.assertEqual(list(directory.glob(".result-*.tmp")), [])
        self.assertIn("Warning: persistent render artifact binding unavailable.", self.logs)
        self.assertNotIn("private disk details", "\n".join(self.logs))
        events = self.diagnostic_events()
        self.assertIn(
            False, [event["available"] for event in events if event["event"] == "artifact_binding"]
        )
        self.assertTrue(any(event["event"] == "operation_finished" for event in events))

    @patch("explainer.render.adapter.write_implementation_manifest", return_value=False)
    @patch("explainer.render.adapter.manim_available", return_value=True)
    @patch("explainer.render.adapter.RenderRunner", _ProducingRunner)
    @patch("explainer.render.adapter.verify_storyboard", return_value=[])
    def test_implementation_manifest_failure_warns_without_changing_result(self, *_mocks):
        self.assertEqual(
            render_storyboard(EXAMPLE, self.output, on_log=self.logs.append), self.output
        )
        self.assertIn("Warning: render implementation manifest unavailable.", self.logs)
        self.assertTrue((self.diagnostic_directory() / "result.json").exists())
        events = self.diagnostic_events()
        self.assertEqual(
            [event["available"] for event in events if event["event"] == "implementation_manifest"],
            [False],
        )

    @patch("explainer.render.adapter.manim_available", return_value=True)
    @patch("explainer.render.adapter.verify_storyboard", return_value=[])
    def test_failed_render_keeps_scene_logs_and_sanitized_outcome(self, *_mocks):
        class FailingRunner:
            def run_job(self, job, command, cancel_event=None, on_log=None, env=None):
                on_log("private scene failure detail")
                result = _FakeResult(JobOutcome.FAILED)
                result.diagnostic = "private supervision detail"
                return result

        with patch("explainer.render.adapter.RenderRunner", FailingRunner):
            with self.assertRaises(RenderError) as caught:
                render_storyboard(EXAMPLE, self.output, on_log=self.logs.append)
        workdir = Path(caught.exception.workdir)
        self.addCleanup(shutil.rmtree, workdir)
        self.assertTrue((workdir / "explainer_scene.py").is_file())
        directory = self.diagnostic_directory()
        self.assertEqual(caught.exception.diagnostics_dir, str(directory))
        self.assertIn("private supervision detail", (directory / "render.log").read_text())
        self.assertNotIn("private", (directory / "diagnostics.jsonl").read_text())
        outcome = [event for event in self.diagnostic_events() if event["event"] == "render_result"]
        self.assertEqual(outcome[0]["outcome"], "failed")
        self.assertEqual(outcome[0]["return_code"], 1)
        self.assertFalse(
            any(event["event"] == "operation_finished" for event in self.diagnostic_events())
        )
        self.assertFalse((directory / "result.json").exists())

    @patch("explainer.render.adapter.manim_available", return_value=True)
    @patch("explainer.render.adapter.shutil.copyfile")
    @patch("explainer.render.adapter.glob.glob", return_value=["/tmp/x/ExplainerVideo.mp4"])
    @patch("explainer.render.adapter.RenderRunner", _FakeRunner)
    @patch("explainer.render.adapter.verify_storyboard", return_value=[])
    def test_raw_log_write_failure_warns_without_changing_render_outcome(self, *_mocks):
        from explainer.render.adapter import _RenderLog

        original = _RenderLog.__init__

        def broken_log(instance, directory, on_log):
            original(instance, directory, on_log)
            instance._file.close()
            from unittest.mock import Mock

            instance._file = Mock()
            instance._file.write.side_effect = OSError("private disk details")

        with patch.object(_RenderLog, "__init__", broken_log):
            result = render_storyboard(EXAMPLE, self.output, on_log=self.logs.append)
        self.assertEqual(result, self.output)
        self.assertIn("Warning: persistent render output log is incomplete.", self.logs)
        self.assertIn("fake child output", self.logs)
        self.assertNotIn("private disk details", "\n".join(self.logs))
        logging = [
            event for event in self.diagnostic_events() if event["event"] == "logging_finished"
        ]
        self.assertFalse(logging[0]["output_log_complete"])


if __name__ == "__main__":
    unittest.main()
