"""Real Manim/ffmpeg logging and protocol smoke; no TTS, LaTeX or fake media.

Requires the rendering extras plus ffmpeg/ffprobe. All scene code, ledger records
and output belong to temporary test fixtures; synthetic authorization records are
not user consent or permission for any external action.
"""

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from scene_production.handoff.ledger import Ledger

ROOT = Path(__file__).resolve().parents[2]
READY = importlib.util.find_spec("manim") is not None and all(
    shutil.which(name) for name in ("ffmpeg", "ffprobe")
)
PRIVATE_MARKER = "PRIVATE_REAL_SMOKE_OUTPUT"


@unittest.skipUnless(READY, "real logging smoke needs manim + ffmpeg + ffprobe")
class RealLoggingSmokeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="msr-real-logging-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.timeline = self.root / "timeline.json"
        self.timeline.write_text(
            json.dumps({"version": 1, "segments": [{"label": "square", "start": 0, "end": 1}]}),
            encoding="utf-8",
        )

    def scene(self, failed=False):
        path = self.root / ("failure.py" if failed else "success.py")
        body = (
            f"        raise RuntimeError('{PRIVATE_MARKER}')\n"
            if failed
            else "        self.add(Square())\n        self.wait(1)\n"
        )
        path.write_text(
            "from manim import Scene, Square\n"
            "class LoggingSmoke(Scene):\n"
            "    def construct(self):\n"
            f"        print('{PRIVATE_MARKER}', flush=True)\n" + body,
            encoding="utf-8",
        )
        return path

    def cli(self, module, *arguments, code=0):
        process = subprocess.run(
            [sys.executable, "-m", module, *map(str, arguments)],
            cwd=ROOT,
            shell=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(process.returncode, code, process.stdout + process.stderr)
        self.assertEqual(process.stderr, "")
        self.assertNotIn(PRIVATE_MARKER, process.stdout)
        return process

    def records(self, path):
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("\n"), f"incomplete JSONL: {path}")
        self.assertNotIn(PRIVATE_MARKER, text)
        return [json.loads(line) for line in text.splitlines()]

    def timeline_records(self, directory):
        records = self.records(directory / "diagnostics.jsonl")
        self.assertEqual([row["sequence"] for row in records], list(range(1, len(records) + 1)))
        self.assertTrue(all(row["timestamp"].endswith("Z") for row in records))
        elapsed = [row["elapsed_seconds"] for row in records]
        self.assertEqual(elapsed, sorted(elapsed))
        self.assertTrue(all(row.get("duration_seconds", 0) >= 0 for row in records))
        return records

    def assert_artifacts(self, terminal):
        self.assertFalse(terminal["semantic_acceptance"])
        artifacts = terminal["artifacts"]
        self.assertIn("video", artifacts)
        self.assertTrue(any(name.startswith("frame_") for name in artifacts))
        for item in artifacts.values():
            data = Path(item["path"]).read_bytes()
            self.assertGreater(len(data), 0)
            self.assertEqual(hashlib.sha256(data).hexdigest(), item["sha256"])
        self.assertEqual(Path(artifacts["video"]["path"]).read_bytes()[4:8], b"ftyp")
        receipt = json.loads(Path(artifacts["receipt"]["path"]).read_text())
        self.assertAlmostEqual(receipt["video_duration_seconds"], 1, delta=1 / 15)

    def assert_agent(self, attempt, events, failed=False):
        self.assertEqual([row["event"] for row in events], ["started", "terminal"])
        started, terminal = events
        self.assertEqual(terminal["run_token"], started["run_token"])
        self.assertEqual(terminal["status"], "failed" if failed else "succeeded")
        self.assertEqual(terminal["exit_code"], 1 if failed else 0)
        self.assertFalse(terminal["retry_safe"])
        self.assertEqual(events, self.records(attempt / "events.jsonl"))
        self.assertEqual(terminal, json.loads((attempt / "attempt.json").read_text()))
        wrapper = self.timeline_records(attempt)
        self.assertTrue(all(row["run_token"] == started["run_token"] for row in wrapper))
        self.assertTrue(any(row.get("phase") == "operation" for row in wrapper))
        runs = list((attempt / "production").glob("scene-*"))
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertIn(PRIVATE_MARKER, (run / "render.log").read_text())
        production = self.timeline_records(run)
        if failed:
            self.assertEqual(terminal["diagnostics"]["stage"], "render")
            self.assertEqual(terminal["diagnostics"]["failure_reason"], "render_failed")
            self.assertEqual(terminal["error"]["code"], "operation_failed")
            self.assertTrue(
                any(
                    row["event"] == "phase_failed" and row["phase"] == "render"
                    for row in production
                )
            )
            self.assertFalse(any(row["event"] == "production_finished" for row in production))
        else:
            self.assert_artifacts(terminal)
            phases = {row["phase"] for row in production if row["event"] == "phase_finished"}
            self.assertTrue(
                {"prepare", "render", "probe", "decode", "frames", "publication"} <= phases
            )
            self.assertEqual(production[-1]["event"], "production_finished")
        return terminal

    def test_agent_real_success_and_scene_exception_keep_two_event_protocol(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                attempt = self.root / ("failed-attempt" if failed else "successful-attempt")
                result = self.cli(
                    "scene_production.agent",
                    "render",
                    "--scene-file",
                    self.scene(failed),
                    "--scene",
                    "LoggingSmoke",
                    "--timeline",
                    self.timeline,
                    "--output-dir",
                    attempt,
                    "--quality",
                    "ql",
                    "--timeout",
                    "30",
                    "--tool-timeout",
                    "10",
                    code=1 if failed else 0,
                )
                self.assert_agent(
                    attempt, [json.loads(line) for line in result.stdout.splitlines()], failed
                )

    def test_handoff_real_success_and_failure_match_agent_and_ledger(self):
        workflow = self.root / "workflow"
        with Ledger.create(workflow) as ledger:
            ledger.add_authorization(
                {
                    "authorization_id": "synthetic-smoke-fixture",
                    "source_kind": "user_message",
                    "source_ref": "test-only:synthetic-smoke-fixture",
                    "question_text": "Synthetic test fixture; not an actual user question.",
                    "original_text": "Synthetic local smoke fixture; not external authorization.",
                    "allowed_actions": ["render"],
                    "allowed_resources": ["synthetic-scene"],
                    "restrictions": ["Temporary local fixtures only; no external delivery."],
                }
            )
            for failed in (False, True):
                ledger.create_task(
                    {
                        "task_id": "failed" if failed else "successful",
                        "title": "Synthetic real-render logging smoke",
                        "owner": "synthetic-worker",
                        "kind": "render",
                        "action": "render",
                        "resource": "synthetic-scene",
                        "authorization_id": "synthetic-smoke-fixture",
                        "depends_on": [],
                        "payload": {
                            "scene_file": str(self.scene(failed)),
                            "scene": "LoggingSmoke",
                            "timeline": str(self.timeline),
                            "quality": "ql",
                            "timeout": 30,
                            "tool_timeout": 10,
                        },
                    }
                )
        for failed in (False, True):
            with self.subTest(failed=failed):
                task_id = "failed" if failed else "successful"
                process = self.cli(
                    "scene_production.handoff",
                    "--workflow",
                    workflow,
                    "run",
                    "--task-id",
                    task_id,
                    "--worker",
                    "synthetic-worker",
                    "--authorization-revision",
                    "1",
                    code=1 if failed else 0,
                )
                response = json.loads(process.stdout)
                self.assertEqual(response["ok"], not failed)
                result = response["result"]
                self.assertEqual(result["status"], "failed" if failed else "succeeded")
                directory = Path(result["directory"])
                terminal = self.assert_agent(
                    directory / "agent", self.records(directory / "protocol.log"), failed
                )
                self.assertEqual(result["run_token"], terminal["run_token"])
                phases = {
                    row["phase"]
                    for row in self.timeline_records(directory)
                    if row["event"] == "phase_finished"
                }
                self.assertTrue({"launch", "agent_wait", "verification", "ledger_finish"} <= phases)
                if failed:
                    self.assertEqual(result["agent_stage"], "render")
                    self.assertEqual(result["agent_failure_reason"], "render_failed")
                else:
                    self.assertEqual(result["artifacts"], terminal["artifacts"])
                with Ledger(workflow) as ledger:
                    self.assertEqual(ledger.get_task(task_id)["status"], result["status"])
                    attempt = ledger.get_attempt(result["attempt_id"])
                    self.assertEqual(attempt["status"], result["status"])
                    self.assertEqual(attempt["result"], result)


if __name__ == "__main__":
    unittest.main()
