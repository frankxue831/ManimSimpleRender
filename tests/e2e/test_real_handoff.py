"""Real multiprocess handoff regression, not agent/human visual acceptance.

Set MSR_HANDOFF_EVIDENCE_DIR to a NEW path under an existing parent to retain
public fixture evidence for CI/manual inspection. A default temporary directory
is retained on failure, removed on success. No claimed render is ever retried.
"""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from scene_production import digest
from scene_production.handoff import Ledger
from scene_production.handoff.runner import reconcile_evidence

ROOT = Path(__file__).resolve().parents[2]
READY = importlib.util.find_spec("manim") is not None and all(
    shutil.which(tool) for tool in ("ffmpeg", "ffprobe")
)


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


@unittest.skipUnless(READY, "real handoff needs manim + ffmpeg + ffprobe")
class RealHandoffTests(unittest.TestCase):
    def command(self, name, argv, code=0):
        folder = self.root / "commands" / name
        folder.mkdir()
        start = time.monotonic_ns()
        with (folder / "stdout.log").open("wb") as stdout:
            with (folder / "stderr.log").open("wb") as stderr:
                process = subprocess.run(
                    argv, cwd=ROOT, stdout=stdout, stderr=stderr, timeout=15, check=False
                )
        save(
            folder / "process.json",
            {
                "argv": argv,
                "returncode": process.returncode,
                "start_ns": start,
                "end_ns": time.monotonic_ns(),
            },
        )
        self.assertEqual(process.returncode, code, str(folder))
        self.assertEqual((folder / "stderr.log").read_bytes(), b"", str(folder))
        return read(folder / "stdout.log")

    def cli(self, name, *args):
        return self.command(
            name,
            [
                sys.executable,
                "-m",
                "scene_production.handoff",
                "--workflow",
                str(self.workflow),
                *args,
            ],
        )["result"]

    def prepare(self):
        inputs = self.root / "inputs"
        inputs.mkdir()
        shutil.copy2(ROOT / "tests/support/real_handoff_scene.py", inputs / "scene.py")
        save(
            inputs / "timeline.json",
            {
                "version": 1,
                "segments": [{"label": "motion", "start": 0, "end": 2, "samples": [0.25, 1, 1.75]}],
            },
        )
        with Ledger.create(self.workflow) as ledger:
            ledger.add_authorization(
                {
                    "authorization_id": "illustrative-only",
                    "source_kind": "user_message",
                    "source_ref": "fixture-only:invented-authorization",
                    "question_text": "ILLUSTRATIVE FIXTURE ONLY; not an actual user question.",
                    "original_text": "ILLUSTRATIVE FIXTURE ONLY; not actual user consent.",
                    "allowed_actions": ["draft", "render", "review"],
                    "allowed_resources": ["public-geometric-fixture"],
                    "restrictions": ["Synthetic local regression only; no external delivery."],
                }
            )
            for task_id, owner, kind, deps, payload in (
                ("draft", "author", "manual", [], {}),
                (
                    "render",
                    "producer",
                    "render",
                    ["draft"],
                    {
                        "scene_file": str(inputs / "scene.py"),
                        "scene": "HandoffFixture",
                        "timeline": str(inputs / "timeline.json"),
                        "quality": "ql",
                        "timeout": 30,
                        "tool_timeout": 10,
                    },
                ),
                ("review", "reviewer", "manual", ["render"], {}),
            ):
                task = ledger.create_task(
                    {
                        "task_id": task_id,
                        "title": "Public fixture " + task_id,
                        "owner": owner,
                        "kind": kind,
                        "action": task_id,
                        "resource": "public-geometric-fixture",
                        "authorization_id": "illustrative-only",
                        "depends_on": deps,
                        "payload": payload,
                    }
                )
                if deps:
                    self.assertEqual(task["blocked_reason"], "dependency_blocked")
                    self.assertEqual(ledger.list_attempts(task_id), [])
            draft = ledger.claim("draft", "fixture-author", 1)
            identities = {name: digest(inputs / name) for name in ("scene.py", "timeline.json")}
            ledger.finish(
                draft["attempt_id"],
                draft["fence"],
                "succeeded",
                {
                    "scope": "fixture preparation only",
                    "input_sha256": identities,
                    "semantic_acceptance": False,
                },
            )
        return identities

    def contenders(self):
        processes = []
        deadline = time.monotonic() + 90
        try:
            for name in ("contender-a", "contender-b"):
                folder = self.root / "commands" / name
                folder.mkdir()
                argv = [
                    sys.executable,
                    "-m",
                    "tests.support.handoff_process",
                    "contend",
                    str(self.workflow),
                    str(folder),
                ]
                with (folder / "stdout.log").open("wb") as stdout:
                    with (folder / "stderr.log").open("wb") as stderr:
                        process = subprocess.Popen(
                            argv, cwd=ROOT, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr
                        )
                processes.append((process, folder, argv))
            ready_deadline = time.monotonic() + 15
            while not all((folder / "ready").exists() for _, folder, _ in processes):
                self.assertTrue(all(p.poll() is None for p, _, _ in processes))
                self.assertLess(time.monotonic(), ready_deadline, "fixture barrier timed out")
                time.sleep(0.01)  # Test barrier only, never a receipt/liveness poll.
            for process, _, _ in processes:
                process.stdin.write(b"go\n")
                process.stdin.close()
            for process, folder, argv in processes:
                code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
                save(
                    folder / "process.json", {"argv": argv, "pid": process.pid, "returncode": code}
                )
            return processes
        finally:
            # Never retry or resolve an unknown. SIGINT allows the supported
            # runner to attempt its own nested cleanup; OS/storage stalls remain
            # outside the guarantee. All evidence stays for investigation.
            for process, folder, argv in processes:
                if process.poll() is None:
                    try:
                        process.send_signal(signal.SIGINT)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=35)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    save(
                        folder / "interrupted.json",
                        {
                            "argv": argv,
                            "returncode": process.returncode,
                            "outcome": "uncertain; do not retry",
                            "cleanup_verified": False,
                        },
                    )
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()

    def exercise(self):
        identities = self.prepare()
        ack = self.command(
            "ack-and-exit",
            [
                sys.executable,
                "-m",
                "tests.support.handoff_process",
                "ack",
                str(self.workflow),
                str(self.root),
            ],
        )
        self.assertEqual({event["type"] for event in ack["acked"]}, {"created", "ready"})
        self.assertEqual(ack["attempts"], [])
        self.assertEqual(self.cli("empty-inbox", "events", "--owner", "producer"), [])
        candidates = self.cli("fresh-durable-scan", "tasks", "--owner", "producer", "--eligible")
        self.assertEqual([task["task_id"] for task in candidates], ["render"])
        self.assertEqual(self.cli("still-no-attempt", "attempts", "--task-id", "render"), [])
        handoff = self.cli("before-claim", "handoff", "--id", "render")
        self.assertEqual(
            handoff["authorization"]["source_ref"], "fixture-only:invented-authorization"
        )
        processes = self.contenders()
        self.assertEqual(sorted(p.returncode for p, _, _ in processes), [0, 1])
        invocations = [read(folder / "invocation.json") for _, folder, _ in processes]
        self.assertEqual(len({row["pid"] for row in invocations}), 2)
        overlap = min(row["end_ns"] for row in invocations) - max(
            row["start_ns"] for row in invocations
        )
        self.assertGreater(overlap, 0, "callers must really overlap, not just run sequentially")
        for process, folder, _ in processes:
            self.assertEqual(read(folder / "invocation.json")["pid"], process.pid)
            self.assertEqual((folder / "stderr.log").read_bytes(), b"")
            response = read(folder / "stdout.log")
            if process.returncode == 1:
                self.assertEqual(
                    response, {"protocol_version": 1, "ok": False, "error": "request_rejected"}
                )
            else:
                self.assertTrue(response["ok"])
                winner = response["result"]
        self.assertEqual(winner["status"], "succeeded")
        self.assertTrue(winner["exit_observed"])
        self.assertEqual(winner["returncode"], 0)
        self.assertFalse(winner["semantic_acceptance"])
        directory = Path(winner["directory"])
        self.assertEqual(list((self.workflow / "outputs").iterdir()), [directory])
        protocol = [
            json.loads(line) for line in (directory / "protocol.log").read_text().splitlines()
        ]
        self.assertEqual([row["event"] for row in protocol], ["started", "terminal"])
        started, terminal = protocol
        self.assertEqual(started["run_token"], terminal["run_token"])
        self.assertEqual(winner["run_token"], started["run_token"])
        self.assertEqual(terminal["status"], "succeeded")
        self.assertEqual(terminal["exit_code"], 0)
        self.assertFalse(terminal["semantic_acceptance"])
        self.assertEqual(read(directory / "agent/attempt.json"), terminal)
        self.assertEqual(
            (directory / "protocol.log").read_bytes(),
            (directory / "agent/events.jsonl").read_bytes(),
        )
        observed = read(directory / "exit.json")
        self.assertTrue(observed["exit_observed"])
        self.assertFalse(observed["interrupted"])
        self.assertEqual(observed["returncode"], terminal["exit_code"])
        self.assertEqual(reconcile_evidence(directory)["status"], "succeeded")
        self.assertEqual(winner["artifacts"], terminal["artifacts"])
        for entry in winner["artifacts"].values():
            path = Path(entry["path"])
            self.assertTrue(path.is_relative_to(directory))
            self.assertGreater(path.stat().st_size, 0)
            self.assertEqual(digest(path), entry["sha256"])
        self.assertEqual(winner["artifacts"]["source"]["sha256"], identities["scene.py"])
        self.assertEqual(winner["artifacts"]["timeline"]["sha256"], identities["timeline.json"])
        receipt = read(Path(winner["artifacts"]["receipt"]["path"]))
        self.assertAlmostEqual(receipt["video_duration_seconds"], 2, delta=1e-6)
        self.assertEqual([frame["time"] for frame in receipt["frames"]], [0.2, 0.25, 1, 1.75, 1.8])
        streams = receipt["probe"]["streams"]
        self.assertEqual([stream["codec_type"] for stream in streams], ["video"])
        self.assertEqual(streams[0]["nb_frames"], "30")
        self.assertEqual(streams[0]["avg_frame_rate"], "15/1")
        runs = list((directory / "agent/production").glob("scene-*"))
        self.assertEqual(len(runs), 1)
        log = (runs[0] / "render.log").read_text()
        self.assertEqual(log.count("Manim Community"), 1)
        self.assertEqual(log.count("Rendered HandoffFixture"), 1)
        process_record = read(directory / "process.json")
        self.assertEqual(process_record["attempt_id"], winner["attempt_id"])
        self.assertEqual(process_record["fence"], winner["fence"])
        self.assertGreater(process_record["pid"], 0)
        with Ledger(self.workflow) as ledger:
            attempts = ledger.list_attempts("render")
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0]["result"], winner)
            self.assertEqual(attempts[0]["fence"], 1)
            self.assertEqual(ledger.get_task("render")["status"], "succeeded")
            review = ledger.get_handoff("review")
            self.assertEqual(review["predecessors"][0]["attempts"][0]["result"], winner)
            # Retain only fixture task/result fields, never the machine-derived
            # execution_guard inside the raw predecessor attempt record.
            save(
                self.root / "review-handoff.json",
                {
                    "task": review["task"],
                    "upstream_result": winner,
                    "decision": "needs_review",
                    "semantic_acceptance": False,
                },
            )
            self.assertEqual(ledger.get_task("review")["status"], "pending")
            self.assertTrue(ledger.get_task("review")["eligible"])
            self.assertEqual(ledger.list_attempts("review"), [])
            ready = [
                event for event in ledger.pending_events("reviewer") if event["type"] == "ready"
            ]
            self.assertEqual([event["task_id"] for event in ready], ["review"])
        result = {
            "scope": "deterministic local multiprocess API/fixture regression",
            "status": "succeeded",
            "decision": "needs_review",
            "semantic_acceptance": False,
            "human_acceptance": False,
            "independent_agent_review": False,
            "visual_review_performed": False,
            "interruption": "normal consumer exit after ACK, before claim; not an OS crash",
            "durable_scan_recovered": True,
            "render_attempts": len(attempts),
            "render_retries": 0,
            "observed_contender_exit_codes": sorted(p.returncode for p, _, _ in processes),
            "observed_agent_exit_code": observed["returncode"],
            "contender_overlap_ns": overlap,
            "run_token": winner["run_token"],
            "attempt_id": winner["attempt_id"],
            "artifacts": winner["artifacts"],
        }
        save(self.root / "summary.json", result)

    def test_ack_exit_concurrent_real_render_and_pending_review(self):
        explicit = os.environ.get("MSR_HANDOFF_EVIDENCE_DIR")
        self.root = Path(explicit).resolve() if explicit else Path(tempfile.mkdtemp()).resolve()
        if explicit:
            self.root.mkdir()  # Must not reuse output or silently retry an earlier render.
        self.workflow = self.root / "workflow"
        (self.root / "commands").mkdir()
        try:
            self.exercise()
        except BaseException as exc:
            save(
                self.root / "failure.json",
                {
                    "exception_type": type(exc).__name__,
                    "detail": str(exc),
                    "instruction": "Retain evidence and investigate; do not automatically retry.",
                },
            )
            print("Handoff fixture evidence retained:", self.root, file=sys.stderr)
            raise
        else:
            if not explicit:
                shutil.rmtree(self.root)


if __name__ == "__main__":
    unittest.main()
