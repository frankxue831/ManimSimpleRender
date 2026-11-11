"""Real public FPS fixture; cadence assertions describe only this authored scene."""

from copy import deepcopy
from fractions import Fraction
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scene_production import render, revision
from scene_production.completion import read_completion
from scene_production.frame_trace import validate_trace
from scene_production.handoff.ledger import Ledger
from scene_production.handoff import runner as handoff_runner
from scene_production.reinspection import reinspect


@unittest.skipUnless(
    importlib.util.find_spec("manim") and shutil.which("ffmpeg") and shutil.which("ffprobe"),
    "requires existing Manim 0.22.1, ffmpeg and ffprobe",
)
class ExplicitFpsProcessTests(unittest.TestCase):
    def test_public_api_agent_native_trace_and_revision(self):
        import manim

        self.assertEqual(manim.__version__, "0.22.1")
        evidence = os.environ.get("MSR_FPS_EVIDENCE_DIR")
        if evidence:
            Path(evidence).mkdir(parents=True, exist_ok=True)
            root = Path(tempfile.mkdtemp(prefix="fps-", dir=evidence))
        else:
            temporary = tempfile.TemporaryDirectory()
            self.addCleanup(temporary.cleanup)
            root = Path(temporary.name)
        project = root / "project"
        shutil.copytree(Path(__file__).parent / "fixtures" / "explicit_fps", project)

        def save(name, value):
            (root / name).write_text(json.dumps(value, indent=2) + "\n")

        def api(name, fps):
            return render(
                project / "scene.py",
                "ExplicitFps",
                project / "timeline.json",
                root / name,
                quality="qh",
                fps=fps,
                record_scene_frames=True,
                timeout=120,
            )

        baseline = api("api-baseline", 30)
        command = [
            sys.executable,
            "-m",
            "scene_production.agent",
            "render",
            "--scene-file",
            str(project / "scene.py"),
            "--scene",
            "ExplicitFps",
            "--timeline",
            str(project / "timeline.json"),
            "--output-dir",
            str(root / "agent"),
            "--quality",
            "qh",
            "--fps",
            "30",
            "--record-scene-frames",
        ]
        save("agent-command.json", command)
        completed = subprocess.run(command, capture_output=True, timeout=150, check=False)
        (root / "agent.stdout.jsonl").write_bytes(completed.stdout)
        (root / "agent.stderr.log").write_bytes(completed.stderr)
        completion = read_completion(
            completed.stdout, completed.returncode, expected_operation="render"
        )
        save("completion.json", {"exit_code": completed.returncode, "status": completion.status})
        self.assertEqual(completion.status, "succeeded")
        candidate_path = Path(completion.terminal["artifacts"]["receipt"]["path"])
        candidate = json.loads(candidate_path.read_text())
        # Synthetic authorization exercises bookkeeping only, never authenticates consent.
        payload = dict(
            scene_file=str(project / "scene.py"),
            scene="ExplicitFps",
            timeline=str(project / "timeline.json"),
            quality="qh",
            fps=30,
            record_scene_frames=True,
        )
        with Ledger.create(root / "handoff") as ledger:
            ledger.add_authorization(
                dict(
                    authorization_id="fixture-auth",
                    source_kind="user_message",
                    question_text="SYNTHETIC TEST ONLY: render the public fixture?",
                    source_ref="test:explicit-fps-fixture-not-real-user-consent",
                    original_text="SYNTHETIC TEST ONLY: render public fixture",
                    allowed_actions=["render"],
                    allowed_resources=["public-fixture"],
                    restrictions=[],
                )
            )
            ledger.create_task(
                dict(
                    task_id="fixture-task",
                    title="Render public FPS fixture",
                    owner="fixture-owner",
                    action="render",
                    resource="public-fixture",
                    authorization_id="fixture-auth",
                    kind="render",
                    payload=payload,
                )
            )
            claimed = []
            arguments = []
            original_claim = ledger.claim_local_render
            original_arguments = handoff_runner._arguments

            def observe_claim(*args, **kwargs):
                result = original_claim(*args, **kwargs)
                claimed.append(deepcopy(result))
                return result

            def observe_arguments(*args, **kwargs):
                result = original_arguments(*args, **kwargs)
                arguments.append(result[:])
                return result

            with (
                patch.object(ledger, "claim_local_render", side_effect=observe_claim),
                patch.object(handoff_runner, "_arguments", side_effect=observe_arguments),
            ):
                handoff = handoff_runner.run_task(ledger, "fixture-task", "fixture-worker", 1)
            save("handoff-result.json", handoff)
            save("handoff-arguments.json", arguments)
            self.assertEqual(handoff["status"], "succeeded")
            self.assertEqual(len(claimed), 1)
            self.assertEqual(claimed[0]["payload"], payload)
            self.assertEqual(claimed[0]["execution_guard"]["state"], "not_started")
            self.assertEqual(claimed[0]["fence"], 1)
            final_args = arguments[-1]
            self.assertEqual(
                final_args[:4], [sys.executable, "-m", "scene_production.agent", "render"]
            )
            self.assertEqual(final_args.count("--fps"), 1)
            self.assertEqual(final_args[final_args.index("--fps") + 1], "30")
            attempt_id = claimed[0]["attempt_id"]
        with Ledger(root / "handoff") as reopened:
            task = reopened.get_task("fixture-task")
            attempt = reopened.get_attempt(attempt_id)
            self.assertEqual(task["payload"], payload)
            self.assertEqual(attempt["payload"], payload)
            self.assertEqual((task["status"], attempt["status"]), ("succeeded", "succeeded"))
            self.assertEqual((attempt["fence"], attempt["authorization_revision"]), (1, 1))
            self.assertEqual(attempt["authorization_id"], "fixture-auth")
            self.assertEqual(attempt["worker_id"], "fixture-worker")
            self.assertEqual(attempt["execution_guard"]["state"], "spawn_intent")
            save("handoff-attempt.json", attempt)
            self.assertEqual(reopened.get_authorization("fixture-auth")["revision"], 1)
        handoff_receipt = json.loads(Path(handoff["artifacts"]["receipt"]["path"]).read_text())
        self.assertEqual(handoff_receipt["status"], "success")
        for name, receipt in (
            ("api", baseline),
            ("agent", candidate),
            ("handoff", handoff_receipt),
        ):
            self.assertEqual(receipt["requested_fps"], 30)
            self.assertEqual(receipt["command"].count("--fps"), 1)
            self.assertEqual(receipt["command"][receipt["command"].index("--fps") + 1], "30")
            streams = receipt["probe"]["streams"]
            self.assertFalse(any(s["codec_type"] == "audio" for s in streams))
            stream = next(s for s in streams if s["codec_type"] == "video")
            self.assertEqual((stream["width"], stream["height"]), (1920, 1080))
            self.assertEqual(Fraction(stream["r_frame_rate"]), 30)
            self.assertEqual(Fraction(stream["avg_frame_rate"]), 30)
            self.assertEqual(int(stream["nb_frames"]), 12)
            self.assertEqual(Fraction(stream["duration"]), Fraction(2, 5))
            trace = json.loads(Path(receipt["frame_trace"]["path"]).read_text())
            self.assertEqual(trace["frame_rate"], "30")
            self.assertEqual(trace["frame_count"], 12)
            self.assertEqual(
                trace["sections"],
                [
                    {"id": "context", "start_frame": 0, "end_frame": 6},
                    {"id": "ending", "start_frame": 6, "end_frame": 12},
                ],
            )
            self.assertEqual(len(set(trace["frames"][:6])), 1)
            self.assertEqual(len(set(trace["frames"][6:])), 1)
            self.assertNotEqual(trace["frames"][5], trace["frames"][6])
            # Decode all frames without resampling. framemd5 records exact rational PTS.
            decoded = revision.frame_sequence(receipt["video"], Fraction(0), Fraction(2, 5), 30)
            save(name + "-decoded-frames.json", decoded)
            pts = [Fraction(frame[0]) for frame in decoded]
            durations = [Fraction(frame[1]) for frame in decoded]
            self.assertEqual(len(pts), 12)
            self.assertEqual(pts[0], 0)
            self.assertTrue(all(b - a == Fraction(1, 30) for a, b in zip(pts, pts[1:])))
            self.assertEqual(durations, [Fraction(1, 30)] * 12)
            self.assertEqual(pts[-1] + durations[-1], Fraction(2, 5))
            for field, value in (("frame_rate", "60"), ("frame_count", 13)):
                tampered = deepcopy(trace)
                tampered[field] = value
                with self.assertRaises(ValueError):
                    validate_trace(tampered, receipt["source_sha256"], stream)

        snapshot = revision.capture(
            project / "manifest.json", Path(baseline["run_dir"]) / "result.json", root / "versions"
        )
        save("snapshot-path.json", snapshot)
        audit = revision.audit(snapshot, candidate_path, ["ending"])
        save("audit.json", audit)
        self.assertEqual(audit["status"], "unchanged_non_target")
        self.assertTrue(audit["sections"])
        self.assertTrue(
            all(
                section["raw_rgba_equal"] and section["decoded_equal"]
                for section in audit["sections"]
            )
        )
        with patch(
            "scene_production.render", side_effect=AssertionError("reinspection must not render")
        ):
            inspected = reinspect(candidate_path, project / "timeline.json", root / "reinspection")
        self.assertEqual(inspected["video_sha256"], candidate["video_sha256"])
        self.assertEqual(inspected["frame_trace"]["sha256"], candidate["frame_trace"]["sha256"])
        # A copied receipt with false observed-rate evidence cannot validate its trace.
        tampered_root = root / "tampered-receipt"
        original_root = Path(candidate["run_dir"])
        shutil.copytree(original_root, tampered_root)
        tampered_receipt = deepcopy(candidate)
        tampered_receipt["run_dir"] = str(tampered_root)
        for owner, key in ((tampered_receipt, "video"), (tampered_receipt["frame_trace"], "path")):
            owner[key] = str(tampered_root / Path(owner[key]).relative_to(original_root))
        observed = next(
            s for s in tampered_receipt["probe"]["streams"] if s["codec_type"] == "video"
        )
        observed.update(avg_frame_rate="60/1", r_frame_rate="60/1")
        (tampered_root / "result.json").write_text(json.dumps(tampered_receipt))
        with self.assertRaisesRegex(ValueError, "trace frame rate mismatch"):
            revision.recorded(tampered_root / "result.json")
        changed = api("changed-rate", 60)
        with self.assertRaisesRegex(ValueError, "frame rate changed"):
            revision.audit(snapshot, Path(changed["run_dir"]) / "result.json", ["ending"])
        save(
            "result.json",
            {
                "status": "passed",
                "fixture_only": True,
                "fps": 30,
                "frames": 12,
                "audit_status": audit["status"],
            },
        )
