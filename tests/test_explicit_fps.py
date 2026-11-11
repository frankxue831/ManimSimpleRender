"""Bounded requested FPS, default compatibility and pre-launch validation."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.render.cleanup import RenderCleanupState
from app.render.command_builder import build_command
from app.render.jobs import RenderJob
from scene_production import agent, render
from scene_production import __main__ as production_cli
from scene_production.handoff import Ledger, LedgerError
from scene_production.handoff.runner import _arguments, run_task
from tests import test_production_agent, test_scene_production

INVALID = (
    True,
    False,
    0,
    -1,
    241,
    30.0,
    29.97,
    float("nan"),
    float("inf"),
    -float("inf"),
    "30",
    [],
    {},
)
BAD_TOKENS = ("0", "-1", "241", "30.0", "29.97", "3e1", "nan", "inf", "True", "secret")


class BuilderFPSTests(unittest.TestCase):
    def test_all_legacy_shapes_and_bounds(self):
        job = RenderJob("/source with spaces/scene.py", "Demo")
        for flag in ("-ql", "-qm", "-qh", "-qp", "-qk"):
            for engine in ("", "--renderer=cairo", "--renderer=opengl"):
                for base in (None, ["/tool with spaces/manim"]):
                    prefix = base or [sys.executable, "-m", "manim"]
                    expected = prefix + [job.file_path, "Demo", flag, "--media_dir", "/out dir"]
                    expected += [engine] if engine else []
                    with self.subTest(flag=flag, engine=engine, base=base):
                        self.assertEqual(
                            build_command(job, flag, "/out dir", engine, base), expected
                        )
                        self.assertEqual(
                            build_command(job, flag, "/out dir", engine, base, fps=None), expected
                        )
                        for fps in (1, 30, 240):
                            actual = build_command(job, flag, "/out dir", engine, base, fps=fps)
                            self.assertEqual(actual, expected + ["--fps", str(fps)])
                            self.assertEqual(actual.count("--fps"), 1)

    def test_strict_types_and_keyword_only(self):
        job = RenderJob("scene.py", "Demo")
        for value in INVALID:
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_command(job, "-qh", "out", fps=value)
        with self.assertRaises(TypeError):
            build_command(job, "-qh", "out", "", None, 30)


class ProductionFPSTests(unittest.TestCase):
    setUp = test_scene_production.PipelineTests.setUp
    invoke = test_scene_production.PipelineTests.invoke
    runner = test_scene_production.PipelineTests.runner
    receipt = test_scene_production.PipelineTests.receipt
    fake_tool = staticmethod(test_scene_production.PipelineTests.fake_tool)

    def test_invalid_before_source_read_output_timer_or_runner(self):
        runner = Mock()
        for value in INVALID:
            with self.subTest(value=value), patch("scene_production.threading.Timer") as timer:
                with self.assertRaises(ValueError) as caught:
                    render(
                        "missing.py", "Demo", "missing.json", self.output, runner=runner, fps=value
                    )
                self.assertEqual(
                    caught.exception.render_cleanup_state, RenderCleanupState.NOT_STARTED
                )
                self.assertFalse(self.output.exists())
                timer.assert_not_called()
        runner.run_job.assert_not_called()

    def test_requested_intent_does_not_replace_observed_metadata(self):
        for fps in (None, 30):
            self.output = self.root / str(fps)
            runner = Mock(wraps=self.runner())
            with patch("scene_production.tool", side_effect=self.fake_tool):
                result = self.invoke(runner=runner, quality="qh", fps=fps)
            command = runner.run_job.call_args.args[1]
            self.assertEqual(command[-2:], ["--disable_caching", "--silent"])
            self.assertIn("-qh", command)
            if fps is None:
                self.assertNotIn("requested_fps", result)
                self.assertNotIn("--fps", command)
            else:
                self.assertEqual(result["requested_fps"], 30)
                self.assertEqual(command[-4:-2], ["--fps", "30"])
            self.assertEqual(result["command"], command)
            self.assertEqual(result["probe"]["streams"][0]["width"], 16)
            self.assertEqual(result["probe"]["streams"][0]["height"], 16)

    def test_observed_source_override_is_not_rewritten(self):
        def observed_override(command, timeout, **kwargs):
            data = self.fake_tool(command, timeout, **kwargs)
            return data.replace(b"30/1", b"60/1") if command[0] == "ffprobe" else data

        with patch("scene_production.tool", side_effect=observed_override):
            result = self.invoke(runner=self.runner(), fps=30)
        self.assertEqual(result["requested_fps"], 30)
        self.assertEqual(result["probe"]["streams"][0]["avg_frame_rate"], "60/1")

    def test_failure_retains_prepared_command_only_when_opted_in(self):
        for fps in (None, 30):
            self.output = self.root / str(fps)
            runner = Mock(run_job=Mock(side_effect=RuntimeError("failed spawn")))
            with self.assertRaises(RuntimeError):
                self.invoke(runner=runner, fps=fps)
            receipt = self.receipt()
            if fps is None:
                self.assertNotIn("command", receipt)
                self.assertNotIn("requested_fps", receipt)
            else:
                self.assertEqual(receipt["requested_fps"], 30)
                self.assertEqual(receipt["command"], runner.run_job.call_args.args[1])
            self.assertEqual(receipt["render_cleanup_state"], "unknown")

    def test_production_cli_validates_before_dispatch_and_forwards(self):
        args = [
            "scene-production",
            "--scene-file",
            str(self.source),
            "--scene",
            "Demo",
            "--timeline",
            str(self.timeline),
            "--output-dir",
            str(self.output),
        ]
        for token in BAD_TOKENS:
            with (
                self.subTest(token=token),
                patch.object(sys, "argv", args + ["--fps", token]),
                patch.object(production_cli, "render") as api,
                redirect_stderr(io.StringIO()),
            ):
                with self.assertRaises(SystemExit) as caught:
                    production_cli.main()
                self.assertEqual(caught.exception.code, 2)
                api.assert_not_called()
                self.assertFalse(self.output.exists())
        with (
            patch.object(sys, "argv", args + ["--fps", "30"]),
            patch.object(production_cli, "render", return_value={}) as api,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(production_cli.main(), 0)
        self.assertEqual(api.call_args.kwargs["fps"], 30)


class AgentFPSTests(unittest.TestCase):
    setUp = test_production_agent.AgentProtocolTests.setUp
    invoke = test_production_agent.AgentProtocolTests.invoke
    arguments = test_production_agent.AgentProtocolTests.arguments
    successful_api = staticmethod(test_production_agent.AgentProtocolTests.successful_api)

    def test_invalid_tokens_have_only_argument_terminal_and_no_attempt(self):
        for token in BAD_TOKENS:
            with self.subTest(token=token), patch("scene_production.render") as api:
                code, events = self.invoke(self.arguments() + ["--fps", token])
                self.assertEqual(code, 2)
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["error"]["code"], "invalid_arguments")
                self.assertFalse(events[0]["retry_safe"])
                self.assertNotIn("secret", json.dumps(events))
                self.assertFalse(self.output.exists())
                api.assert_not_called()

    def test_intent_persists_in_all_events_and_defaults_stay_absent(self):
        for fps in (None, 30):
            self.output = self.root / str(fps)
            with patch("scene_production.render", side_effect=self.successful_api) as api:
                code, events = self.invoke(
                    self.arguments() + ([] if fps is None else ["--fps", str(fps)])
                )
            self.assertEqual(code, 0)
            self.assertEqual(api.call_args.kwargs.get("fps"), fps)
            for event in events:
                if fps is None:
                    self.assertNotIn("requested_fps", event)
                else:
                    self.assertEqual(event["requested_fps"], fps)
            self.assertEqual(json.loads((self.output / "attempt.json").read_text()), events[-1])
            self.assertEqual(
                [
                    json.loads(line)
                    for line in (self.output / "events.jsonl").read_text().splitlines()
                ],
                events,
            )

    def test_inspect_rejects_fps_and_describe_advertises_render_only(self):
        with patch("scene_production.render") as api:
            code, events = self.invoke(self.arguments("inspect") + ["--fps", "30"])
        self.assertEqual(code, 2)
        api.assert_not_called()
        self.assertFalse(self.output.exists())
        description = agent.describe_protocol()
        self.assertIn("fps", description["render_options"])
        self.assertNotIn("fps", description["inspect_options"])
        self.assertIn("240", json.dumps(description["render_options"]["fps"]))


class HandoffFPSTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.py"
        self.source.write_text("# public fixture\n")
        self.payload = dict(scene_file=str(self.source), scene="Demo", timeline=str(self.source))

    def ledger(self, name, payload):
        ledger = Ledger.create(self.root / name)
        self.addCleanup(ledger.close)
        ledger.add_authorization(
            dict(
                authorization_id="auth",
                source_kind="user_message",
                source_ref="message:1",
                question_text="Render?",
                original_text="yes",
                allowed_actions=["render"],
                allowed_resources=["scene"],
                restrictions=[],
            )
        )
        ledger.create_task(
            dict(
                task_id="task",
                title="Test",
                owner="owner",
                action="render",
                resource="scene",
                authorization_id="auth",
                kind="render",
                payload=payload,
            )
        )
        return ledger

    def test_missing_null_and_distinct_rates_survive_claim_and_reopen(self):
        payloads = [
            self.payload,
            dict(self.payload, fps=None),
            dict(self.payload, fps=30),
            dict(self.payload, fps=60),
        ]
        default = _arguments(self.payload, Path("attempt"))
        for index, payload in enumerate(payloads):
            before = deepcopy(payload)
            args = _arguments(payload, Path("attempt"))
            self.assertEqual(payload, before)
            if payload.get("fps") is None:
                self.assertEqual(args, default)
            else:
                self.assertEqual(args.count("--fps"), 1)
                self.assertEqual(args[args.index("--fps") + 1], str(payload["fps"]))
            ledger = self.ledger(str(index), payload)
            with patch(
                "scene_production.handoff.execution_identity.local_execution_identity",
                return_value=None,
            ):
                claim = ledger.claim_local_render("task", "worker", 1)
            self.assertEqual(claim["payload"], before)
            self.assertEqual(claim["fence"], 1)
            self.assertEqual(claim["authorization_revision"], 1)
            self.assertEqual(claim["execution_guard"]["state"], "not_started")
            with Ledger(ledger.directory) as reopened:
                self.assertEqual(reopened.get_task("task")["payload"], before)
                self.assertEqual(reopened.get_attempt(claim["attempt_id"])["payload"], before)

    def test_legacy_attempt_recovery_preserves_missing_and_null_payloads(self):
        for index, payload in enumerate((self.payload, dict(self.payload, fps=None))):
            with self.subTest(payload=payload):
                ledger = self.ledger(str(index), payload)
                ledger.clock = lambda: 100
                # Legacy/external claims have no local execution guard. Recovery
                # must not synthesize one or infer permission to render again.
                claim = ledger.claim("task", "worker", 1, lease_seconds=1)
                self.assertNotIn("execution_guard", claim)
                self.assertNotIn("requested_fps", claim)
                with Ledger(ledger.directory, clock=lambda: 102) as reopened:
                    recovered = reopened.reconcile()
                    self.assertEqual(recovered, [dict(claim, status="unknown")])
                    task = reopened.get_task("task")
                    attempt = reopened.get_attempt(claim["attempt_id"])
                    self.assertEqual(task["payload"], payload)
                    self.assertEqual(attempt["payload"], payload)
                    self.assertEqual((task["status"], task["fence"]), ("unknown", 2))
                    self.assertEqual(attempt["fence"], 1)
                    self.assertNotIn("requested_fps", task)
                    self.assertNotIn("requested_fps", attempt)
                    self.assertNotIn("execution_guard", attempt)
                    self.assertEqual(reopened.reconcile(), [])
                    with self.assertRaises(LedgerError):
                        reopened.claim("task", "worker", 1)
                with Ledger(ledger.directory) as reopened:
                    self.assertEqual(reopened.get_task("task")["payload"], payload)
                    self.assertEqual(reopened.get_attempt(claim["attempt_id"]), attempt)

    def test_invalid_persisted_tasks_never_claim_or_launch(self):
        # JSON storage rejects non-finite values independently; all JSON values
        # below can be stored, but none may acquire execution authority.
        values = (True, False, 0, -1, 241, 30.0, 29.97, "30", [], {})
        for index, value in enumerate(values):
            with self.subTest(value=value):
                ledger = self.ledger(str(index), dict(self.payload, fps=value))
                with (
                    patch.object(
                        ledger, "claim_local_render", wraps=ledger.claim_local_render
                    ) as claim,
                    patch("scene_production.handoff.runner.subprocess.Popen") as spawn,
                ):
                    with self.assertRaises(ValueError):
                        run_task(ledger, "task", "worker", 1)
                    claim.assert_not_called()
                    spawn.assert_not_called()
                task = ledger.get_task("task")
                self.assertEqual((task["status"], task["fence"]), ("pending", 0))
                self.assertFalse((Path(ledger.directory) / "outputs").exists())
        with self.assertRaises(ValueError):
            _arguments(dict(self.payload, fps=30, extra_args=["--fps", "60"]), Path("unused"))
