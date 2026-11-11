"""Headless orchestration tests; media decoding is mocked, not visual acceptance."""

import ast
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from examples.silent_review import run
from scene_production import digest, revision


class SilentReviewExampleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "new-example"

    def protocol(self):
        common = {
            "protocol_version": 1,
            "run_token": "a" * 32,
            "operation": "render",
            "retry_safe": False,
        }
        artifacts = {
            name: {"path": str(self.root / name), "sha256": "b" * 64}
            for name in ("receipt", "source", "timeline", "video", "frame_000")
        }
        # No receipt exists: completion must come from captured transport, not disk.
        events = [
            dict(common, event="started", status="running", attempt_dir=str(self.root / "attempt")),
            dict(
                common,
                event="terminal",
                status="succeeded",
                exit_code=0,
                semantic_acceptance=False,
                verification_scope="retained_artifact_bytes",
                artifacts=artifacts,
            ),
        ]
        return events, artifacts

    def test_render_accepts_observed_process_and_actual_started_token(self):
        events, artifacts = self.protocol()
        stdout = ("\n".join(json.dumps(item) for item in events) + "\n").encode()
        completed = subprocess.CompletedProcess([], 0, stdout, b"retained stderr\n")
        with patch.object(run.subprocess, "run", return_value=completed) as invoke:
            result = run.render_attempt(self.root, self.root, "baseline")
        self.assertEqual(result, artifacts)
        self.assertEqual((self.root / "baseline.stdout.jsonl").read_bytes(), stdout)
        self.assertEqual((self.root / "baseline.stderr.log").read_bytes(), completed.stderr)
        saved = json.loads((self.root / "baseline.completion.json").read_text())
        self.assertEqual(saved["run_token"], events[0]["run_token"])
        self.assertEqual(saved["status"], "succeeded")
        self.assertEqual(invoke.call_count, 1)
        self.assertIn("--record-scene-frames", invoke.call_args.args[0])
        self.assertFalse(invoke.call_args.kwargs["check"])

    def test_bad_transport_or_exit_stops_without_retry_or_receipt_fallback(self):
        events, _ = self.protocol()
        for label, lines, code in (
            ("missing-start", events[1:], 0),
            ("exit-mismatch", events, 3),
            ("malformed", ["not JSON"], 0),
        ):
            with self.subTest(label=label):
                stdout = ("\n".join(json.dumps(item) for item in lines) + "\n").encode()
                completed = subprocess.CompletedProcess([], code, stdout, b"")
                with patch.object(run.subprocess, "run", return_value=completed) as invoke:
                    with self.assertRaisesRegex(RuntimeError, "do not automatically retry"):
                        run.render_attempt(self.root, self.root, label)
                self.assertEqual(invoke.call_count, 1)
                self.assertEqual((self.root / f"{label}.stdout.jsonl").read_bytes(), stdout)
                saved = json.loads((self.root / f"{label}.completion.json").read_text())
                self.assertEqual(saved["status"], "unknown")

    def fake_render(self, project, output, name):
        """Synthetic bytes for orchestration only; never presented as real media."""
        folder = output / name
        folder.mkdir()
        for filename, payload in (
            ("source.py", (project / "scene.py").read_bytes()),
            ("timeline.json", (project / "timeline.json").read_bytes()),
            ("video.mp4", name.encode()),
            ("frame.png", b"mock frame"),
            ("trace.json", b"mock trace; audit is mocked"),
        ):
            (folder / filename).write_bytes(payload)
        receipt = {
            "status": "success",
            "run_dir": str(folder),
            "source": str(project / "scene.py"),
            "source_sha256": digest(folder / "source.py"),
            "timeline_sha256": digest(folder / "timeline.json"),
            "video": str(folder / "video.mp4"),
            "video_sha256": digest(folder / "video.mp4"),
            "command": ["python", "-m", "manim", str(project / "scene.py"), "SilentReview"],
            "frames": [
                {
                    "path": str(folder / "frame.png"),
                    "sha256": digest(folder / "frame.png"),
                    "time": 1.5,
                }
            ],
        }
        run.save(folder / "result.json", receipt)
        return {
            key: {"path": str(folder / filename), "sha256": digest(folder / filename)}
            for key, filename in (
                ("receipt", "result.json"),
                ("source", "source.py"),
                ("timeline", "timeline.json"),
                ("video", "video.mp4"),
                ("frame_000", "frame.png"),
                ("frame_trace", "trace.json"),
            )
        }

    def test_complete_fixture_preserves_scope_and_always_needs_review(self):
        fixture_before = {path.name: path.read_bytes() for path in run.FIXTURES.glob("*.*")}
        with patch.object(run, "render_attempt", side_effect=self.fake_render) as render:
            with patch.object(revision, "audit", return_value={"status": "unchanged_non_target"}):
                result = run.execute(self.output)
        self.assertEqual(render.call_count, 2)
        self.assertEqual(result["candidate_decision"], "needs_review")
        self.assertEqual(result["readiness"], "not_determined")
        self.assertFalse(result["previous_assessment_current"])
        self.assertTrue(result["candidate_assessment_current"])
        self.assertTrue(result["human_review_required"])
        plan = json.loads((self.output / "plan.json").read_text())
        self.assertEqual(plan["scope"], ["lookup"])
        baseline_source = (self.output / "baseline/source.py").read_text()
        candidate_source = (self.output / "candidate/source.py").read_text()
        before = revision.regions(baseline_source)
        after = revision.regions(candidate_source)
        self.assertEqual(
            baseline_source[slice(*before["context"][:2])],
            candidate_source[slice(*after["context"][:2])],
        )
        self.assertNotEqual(baseline_source, candidate_source)
        ast.parse(candidate_source)
        candidate = json.loads((self.output / "review-candidate.json").read_text())
        self.assertIn("FIXTURE ONLY", candidate["assessment"]["scope"])
        self.assertEqual(candidate["previous"]["addressed_findings"], ["F1"])
        self.assertEqual(candidate["assessment"]["findings"][0]["id"], "F1")
        self.assertEqual(candidate["assessment"]["decision"], "needs_review")
        self.assertEqual(
            fixture_before, {path.name: path.read_bytes() for path in run.FIXTURES.glob("*.*")}
        )
        with patch.object(run, "render_attempt") as repeated:
            with self.assertRaises(FileExistsError):
                run.execute(self.output)
        repeated.assert_not_called()

    def test_failed_first_render_stops_before_any_review_or_candidate(self):
        with patch.object(run, "render_attempt", side_effect=RuntimeError("unknown")) as render:
            with self.assertRaisesRegex(RuntimeError, "unknown"):
                run.execute(self.output)
        self.assertEqual(render.call_count, 1)
        self.assertFalse((self.output / "review-baseline.json").exists())
        self.assertFalse((self.output / "candidate").exists())

    def test_missing_parent_is_not_created(self):
        with self.assertRaises(FileNotFoundError):
            run.execute(self.root / "absent" / "new-example")
        self.assertFalse((self.root / "absent").exists())

    def test_retained_bytes_are_checked_separately(self):
        path = self.root / "receipt"
        path.write_bytes(b"original")
        artifacts = {"receipt": {"path": str(path), "sha256": digest(path)}}
        self.assertEqual(run.artifact(artifacts, "receipt"), path)
        path.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "missing or changed"):
            run.artifact(artifacts, "receipt")

    def test_evidence_retains_all_numbered_frames(self):
        keys = ("receipt", "source", "timeline", "frame_trace", "frame_000", "frame_100")
        artifacts = {}
        for key in keys:
            path = self.root / key
            path.write_bytes(key.encode())
            artifacts[key] = {"path": str(path), "sha256": digest(path)}
        self.assertEqual(run.evidence(artifacts), [str(self.root / key) for key in keys])

    def test_audit_needs_review_does_not_gain_editorial_approval(self):
        with patch.object(run, "render_attempt", side_effect=self.fake_render):
            with patch.object(revision, "audit", return_value={"status": "needs_review"}):
                result = run.execute(self.output)
        self.assertEqual(result["audit_status"], "needs_review")
        self.assertEqual(result["candidate_decision"], "needs_review")
        self.assertEqual(result["readiness"], "not_determined")


if __name__ == "__main__":
    unittest.main()
