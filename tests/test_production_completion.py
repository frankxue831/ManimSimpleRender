"""Fail-closed interpretation of captured scene-agent protocol, without media tools."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scene_production.completion import read_completion

TOKEN = "a" * 32
OTHER_TOKEN = "b" * 32


def event(kind, **fields):
    return dict(
        protocol_version=1,
        event=kind,
        run_token=TOKEN,
        operation="render",
        retry_safe=False,
        **fields,
    )


def success(operation="render"):
    artifacts = {
        name: {"path": "/absent/production/scene-test/" + name, "sha256": "a" * 64}
        for name in ("receipt", "source", "timeline", "video", "frame_000")
    }
    events = [
        event("started", status="running", attempt_dir="/absent"),
        event(
            "terminal",
            status="succeeded",
            exit_code=0,
            artifacts=artifacts,
            semantic_acceptance=False,
            verification_scope="retained_artifact_bytes",
        ),
    ]
    for item in events:
        item["operation"] = operation
    return events


def capture(events):
    return "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in events)


class CompletionReaderTests(unittest.TestCase):
    def read(self, events=None, returncode=0, operation="render", **kwargs):
        return read_completion(
            capture(success() if events is None else events),
            returncode,
            expected_operation=operation,
            **kwargs,
        )

    def assert_rejected(self, result):
        self.assertEqual(result.status, "unknown")
        self.assertIsNone(result.terminal, "invalid evidence must not expose consumable artifacts")

    def test_render_and_inspect_success_capture_actual_started_token(self):
        for operation in ("render", "inspect"):
            with self.subTest(operation=operation):
                events = success(operation)
                result = self.read(events, operation=operation)
                self.assertEqual(result.status, "succeeded")
                self.assertEqual(result.reason, "terminal_succeeded")
                self.assertEqual(result.run_token, events[0]["run_token"])
                self.assertEqual(result.terminal, events[-1])
                self.assertFalse(result.terminal["retry_safe"])

    def test_optional_independently_saved_token_must_match(self):
        self.assertEqual(self.read(expected_token=TOKEN).status, "succeeded")
        self.assert_rejected(self.read(expected_token=OTHER_TOKEN))
        for token in (True, 2, {}, [], "", "not-a-token"):
            with self.subTest(token=token):
                self.assert_rejected(self.read(expected_token=token))

    def test_no_io_or_subprocess_or_rehash_even_for_nonexistent_artifacts(self):
        data = capture(success())
        with (
            patch("builtins.open", side_effect=AssertionError("no IO")),
            patch.object(Path, "open", side_effect=AssertionError("no IO")),
            patch.object(Path, "stat", side_effect=AssertionError("no IO")),
            patch("hashlib.sha256", side_effect=AssertionError("no hash")),
            patch("subprocess.Popen", side_effect=AssertionError("no subprocess")),
        ):
            result = read_completion(data, 0, expected_operation="render")
        self.assertEqual(result.status, "succeeded")

    def test_explicit_render_failures_remain_failed_without_retry_advice(self):
        for operation in ("render", "inspect"):
            for code in (
                "input_or_tool_missing",
                "invalid_input",
                "operation_failed",
                "verification_failed",
            ):
                with self.subTest(operation=operation, code=code):
                    started = success(operation)[0]
                    terminal = event("terminal", status="failed", exit_code=1, error={"code": code})
                    terminal["operation"] = operation
                    result = self.read([started, terminal], 1, operation)
                    self.assertEqual(result.status, "failed")
                    self.assertEqual(result.terminal, terminal)
                    self.assertFalse(result.terminal["retry_safe"])

    def test_preclaim_failures_have_no_started_event(self):
        for operation in ("render", "inspect"):
            for code in ("output_exists", "output_unavailable"):
                with self.subTest(operation=operation, code=code):
                    terminal = event("terminal", status="failed", exit_code=1, error={"code": code})
                    terminal["operation"] = operation
                    result = self.read([terminal], 1, operation)
                    self.assertEqual(result.status, "failed")
                    self.assertEqual(result.run_token, TOKEN)
                    self.assert_rejected(self.read([success(operation)[0], terminal], 1, operation))

    def test_usage_failure_with_null_operation_is_recognized(self):
        terminal = event(
            "terminal", status="failed", exit_code=2, error={"code": "invalid_arguments"}
        )
        terminal["operation"] = None
        for operation in ("render", "inspect", "describe"):
            result = self.read([terminal], 2, operation)
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.terminal, terminal)
        self.assert_rejected(self.read([success()[0], terminal], 2))
        del terminal["operation"]
        self.assert_rejected(self.read([terminal], 2))

    def test_null_operation_is_not_a_wildcard(self):
        for events, code in (
            (success(), 0),
            ([event("terminal", status="failed", exit_code=1, error={"code": "output_exists"})], 1),
        ):
            events[-1]["operation"] = None
            self.assert_rejected(self.read(events, code))

    def test_unknown_terminals_preserve_unknown_without_claiming_cleanup(self):
        for code, error in (
            (3, "persistence_failed"),
            (3, "transport_failed"),
            (130, "interrupted"),
            (143, "interrupted"),
        ):
            for include_started in (False, True):
                with self.subTest(code=code, error=error, started=include_started):
                    terminal = event(
                        "terminal", status="unknown", exit_code=code, error={"code": error}
                    )
                    if code in (130, 143):
                        terminal["process_cleanup_verified"] = False
                    events = [success()[0], terminal] if include_started else [terminal]
                    result = self.read(events, code)
                    self.assertEqual(result.status, "unknown")
                    self.assertEqual(result.reason, "terminal_unknown")
                    self.assertEqual(result.terminal, terminal)

    def test_missing_unobserved_or_unexpected_process_exit_cannot_confirm(self):
        for code in (None, True, False, 0.0, "0", [], 1, 2, 3, -9, -15, 130, 143, 255):
            with self.subTest(code=code):
                self.assert_rejected(self.read(returncode=code))

    def test_terminal_exit_has_strict_integer_type(self):
        for value in (True, False, 0.0, "0", None, [], 1):
            events = success()
            events[-1]["exit_code"] = value
            self.assert_rejected(self.read(events))

    def test_required_envelope_fields_are_strict(self):
        mutations = {
            "protocol_version": (True, 1.0, "1", 2, None, []),
            "retry_safe": (True, 0, "false", None),
            "run_token": (True, None, 2, [], "", "x" * 32, "A" * 32, "a" * 31),
            "operation": (True, None, [], "inspect", "describe", "reinspect"),
        }
        for index in (0, 1):
            for field, values in mutations.items():
                for value in values:
                    with self.subTest(index=index, field=field, value=value):
                        events = success()
                        events[index][field] = value
                        self.assert_rejected(self.read(events))
                events = success()
                del events[index][field]
                self.assert_rejected(self.read(events))

    def test_token_mismatch_and_same_token_other_operation_are_rejected(self):
        events = success()
        events[-1]["run_token"] = OTHER_TOKEN
        self.assert_rejected(self.read(events))
        self.assert_rejected(self.read(success("inspect")))

    def test_event_order_count_and_kinds_are_exact(self):
        started, terminal = success()
        for events in (
            [],
            [started],
            [terminal],
            [terminal, started],
            [terminal, terminal],
            [started, started],
            [started, terminal, terminal],
            [started, started, terminal],
            [started, {"event": "progress"}, terminal],
            [None],
            [True],
            [0],
            ["x"],
            [[]],
        ):
            with self.subTest(events=events):
                self.assert_rejected(self.read(events))
        for index in (0, 1):
            events = success()
            events[index]["event"] = "other"
            self.assert_rejected(self.read(events))

    def test_started_shape_cannot_contain_terminal_evidence(self):
        for field, value in (
            ("status", "failed"),
            ("attempt_dir", None),
            ("attempt_dir", "relative"),
            ("exit_code", 0),
            ("error", {}),
            ("description", {}),
            ("artifacts", {}),
        ):
            events = success()
            events[0][field] = value
            self.assert_rejected(self.read(events))

    def test_unsupported_status_and_exit_pairs_are_unknown(self):
        for status, code in (
            ("success", 0),
            ("running", 0),
            (None, 0),
            ([], 0),
            ("failed", 0),
            ("succeeded", 1),
            ("unknown", 1),
            ("failed", 3),
        ):
            events = success()
            events[-1].update(status=status, exit_code=code)
            self.assert_rejected(self.read(events, code))

    def test_success_must_not_mix_in_failure_or_acceptance_claims(self):
        for field, value in (
            ("error", None),
            ("process_cleanup_verified", False),
            ("description", {}),
            ("semantic_acceptance", True),
            ("semantic_acceptance", 0),
            ("verification_scope", "all_source_and_environment"),
        ):
            events = success()
            events[-1][field] = value
            self.assert_rejected(self.read(events))
        for field in ("semantic_acceptance", "verification_scope", "artifacts"):
            events = success()
            del events[-1][field]
            self.assert_rejected(self.read(events))

    def test_safety_fields_do_not_accept_bool_coercion(self):
        for field in ("process_cleanup_verified", "semantic_acceptance"):
            for value in (0, 1, "false", None, [], {}):
                with self.subTest(field=field, value=value):
                    events = success()
                    events[-1][field] = value
                    self.assert_rejected(self.read(events))
        events = success()
        events[-1]["process_cleanup_verified"] = True
        self.assert_rejected(self.read(events))

    def test_describe_cannot_supply_started_or_render_artifacts(self):
        terminal = event("terminal", status="succeeded", exit_code=0, description={"commands": {}})
        terminal["operation"] = "describe"
        self.assertEqual(self.read([terminal], operation="describe").status, "succeeded")
        started = success("describe")[0]
        self.assert_rejected(self.read([started, terminal], operation="describe"))
        for value in (None, [], "text"):
            terminal["description"] = value
            self.assert_rejected(self.read([terminal], operation="describe"))
        del terminal["description"]
        self.assert_rejected(self.read([terminal], operation="describe"))
        terminal["description"] = {"commands": {}}
        terminal["artifacts"] = success()[-1]["artifacts"]
        self.assert_rejected(self.read([terminal], operation="describe"))

    def test_artifact_manifest_shape_is_validated_but_never_its_bytes(self):
        for name in ("receipt", "source", "timeline", "video", "frame_000"):
            events = success()
            del events[-1]["artifacts"][name]
            self.assert_rejected(self.read(events))
        for manifest in (None, [], "path", {}):
            events = success()
            events[-1]["artifacts"] = manifest
            self.assert_rejected(self.read(events))
        for record in (
            None,
            [],
            {},
            {"path": "relative", "sha256": "a" * 64},
            {"path": "/absent", "sha256": "bad"},
            {"path": "/absent", "sha256": True},
        ):
            events = success()
            events[-1]["artifacts"]["video"] = record
            self.assert_rejected(self.read(events))
        for key in ("frame_002", "frame_256", "unrecognized"):
            events = success()
            events[-1]["artifacts"][key] = events[-1]["artifacts"]["video"]
            self.assert_rejected(self.read(events))

    def test_optional_frame_trace_and_cross_platform_paths(self):
        events = success()
        events[0]["attempt_dir"] = "C:\\attempt"
        for item in events[-1]["artifacts"].values():
            item["path"] = "C:\\attempt\\production\\scene-test\\video.mp4"
        events[-1]["artifacts"]["frame_trace"] = {
            "path": "C:\\attempt\\trace.json",
            "sha256": "a" * 64,
        }
        self.assertEqual(self.read(events).status, "succeeded")

    def test_failure_without_started_cannot_look_like_completed_operation(self):
        terminal = event(
            "terminal", status="failed", exit_code=1, error={"code": "operation_failed"}
        )
        self.assert_rejected(self.read([terminal], 1))
        for error in (None, "operation_failed", {}, {"code": "unrecognized"}):
            terminal["error"] = error
            self.assert_rejected(self.read([success()[0], terminal], 1))

    def test_failed_or_unknown_must_not_supply_success_payload(self):
        for status, code, error in (
            ("failed", 1, "operation_failed"),
            ("unknown", 3, "transport_failed"),
        ):
            for key in ("artifacts", "description", "verification_scope"):
                terminal = event("terminal", status=status, exit_code=code, error={"code": error})
                terminal[key] = {}
                self.assert_rejected(self.read([success()[0], terminal], code))

    def test_invalid_jsonl_never_salvages_a_successful_prefix(self):
        good = capture(success())
        for text in (
            "",
            "\n",
            " \n",
            good[:-1],
            good + "\n",
            good + " \n",
            "log text\n" + good,
            good + "log text\n",
            good + "{",
            good + "{\n",
            "\ufeff" + good,
            good.replace("\n", "\r"),
        ):
            with self.subTest(text=text):
                self.assert_rejected(read_completion(text, 0, expected_operation="render"))

    def test_jsonl_requires_objects_and_rejects_duplicate_keys_at_any_depth(self):
        good = capture(success())
        for text in (
            good.replace('"protocol_version": 1', '"protocol_version": 9, "protocol_version": 1'),
            good.replace('"sha256": "', '"sha256": "bad", "sha256": "'),
            good.replace('"event": "terminal"', '"extra": {"x": 1, "x": 2}, "event": "terminal"'),
        ):
            self.assert_rejected(read_completion(text, 0, expected_operation="render"))

    def test_nonfinite_numbers_are_rejected_even_in_uninterpreted_metadata(self):
        for value in ("NaN", "Infinity", "-Infinity", "1e999"):
            text = capture(success()).replace(
                '"event": "terminal"', '"unused": ' + value + ', "event": "terminal"'
            )
            self.assert_rejected(read_completion(text, 0, expected_operation="render"))

    def test_utf8_and_crlf_captures_work_without_splitlines_corruption(self):
        events = success()
        events[-1]["diagnostics"] = {"hint": "one\u2028two\u2029three"}
        text = capture(events)
        for data in (text, text.encode("utf-8"), text.replace("\n", "\r\n")):
            self.assertEqual(
                read_completion(data, 0, expected_operation="render").status, "succeeded"
            )
        self.assert_rejected(
            read_completion(text.encode() + b"\xff\n", 0, expected_operation="render")
        )

    def test_invalid_capture_and_expected_operation_are_unknown(self):
        for value in (None, {}, [], True):
            self.assert_rejected(read_completion(value, 0, expected_operation="render"))
        for value in (None, {}, [], True, "reinspect", "bad"):
            self.assert_rejected(read_completion(capture(success()), 0, expected_operation=value))

    def test_extreme_json_nesting_is_unknown_not_an_exception(self):
        text = "[" * 2000 + "0" + "]" * 2000 + "\n"
        self.assert_rejected(read_completion(text, 0, expected_operation="render"))

    def test_diagnostics_do_not_establish_completion_or_liveness(self):
        events = success()
        events[-1].update(phase="complete", elapsed_seconds=100, timestamp="diagnostic-only")
        before = deepcopy(events)
        self.assertEqual(self.read(events).status, "succeeded")
        self.assertEqual(events, before)
        self.assert_rejected(self.read([{"status": "success", "phase": "complete"}]))
        self.assert_rejected(self.read([events[0]]))


class RealAgentCompletionTests(unittest.TestCase):
    def invoke(self, args, expected_operation):
        result = subprocess.run(
            [sys.executable, "-m", "scene_production.agent", *args],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            check=False,
            timeout=10,
        )
        return result, read_completion(
            result.stdout, result.returncode, expected_operation=expected_operation
        )

    def test_actual_describe_is_terminal_only_success(self):
        process, result = self.invoke(["describe"], "describe")
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(process.stdout.splitlines()), 1)
        self.assertIn("commands", result.terminal["description"])
        self.assertIsNone(result.terminal.get("artifacts"))
        self.assertEqual(result.run_token, result.terminal["run_token"])
        self.assertEqual(
            read_completion(process.stdout, 0, expected_operation="render").status, "unknown"
        )

    def test_actual_parse_error_has_null_operation_and_no_started(self):
        process, result = self.invoke(["render"], "render")
        self.assertEqual(process.returncode, 2)
        self.assertEqual(result.status, "failed")
        self.assertIsNone(result.terminal["operation"])

    def test_actual_preclaim_and_started_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            args = [
                "render",
                "--scene-file",
                str(Path(directory) / "absent.py"),
                "--scene",
                "Demo",
                "--timeline",
                str(Path(directory) / "absent.json"),
                "--output-dir",
                directory,
            ]
            process, result = self.invoke(args, "render")
            self.assertEqual(process.returncode, 1)
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.terminal["error"]["code"], "output_exists")
            args[-1] = str(Path(directory) / "new-attempt")
            process, result = self.invoke(args, "render")
            self.assertEqual(process.returncode, 1)
            self.assertEqual(result.status, "failed")
            started = json.loads(process.stdout.splitlines()[0])
            self.assertEqual(result.run_token, started["run_token"])
            self.assertEqual(result.terminal["error"]["code"], "input_or_tool_missing")
