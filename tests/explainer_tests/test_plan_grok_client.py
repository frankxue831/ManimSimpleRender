"""Tests for explainer.plan.client.GrokCliClient.

The grok CLI is NOT assumed to exist in the test environment and no test may
run it: every test drives a fake subprocess.run. That is also the point of the
design -- the client shells out, so its whole contract is "what did we invoke,
and how did we read what came back".

The failure modes matter more than the happy path here. grok's headless final
turn is frequently cancelled before it emits structured output, which is the
single most likely way a real planner run breaks; see
test_cancelled_turn_is_a_client_error.
"""

import json
import subprocess
import unittest
from unittest.mock import patch

from explainer.plan.client import ClientError, GrokCliClient, MaxTokensExhausted

SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}}}
PAYLOAD = {"title": "t", "beats": []}


def _completed(payload=None, stop="end_turn", returncode=0, stdout=None):
    if stdout is None:
        body = {"stopReason": stop, "structuredOutput": payload, "text": "", "thought": ""}
        stdout = json.dumps(body)
    return subprocess.CompletedProcess(
        args=["grok"], returncode=returncode, stdout=stdout, stderr=""
    )


def _run(client=None, **kwargs):
    """Call draft() against a faked subprocess, returning (result_or_exc, argv)."""
    captured = {}

    def _fake_run(argv, **run_kwargs):
        captured["argv"] = argv
        captured["kwargs"] = run_kwargs
        if "boom" in kwargs:
            raise kwargs["boom"]
        return kwargs.get("completed", _completed(PAYLOAD))

    with patch("subprocess.run", _fake_run):
        result = (client or GrokCliClient()).draft("SYSTEM", "PROMPT", SCHEMA)
    return result, captured


class GrokCliClientHappyPathTests(unittest.TestCase):
    def test_returns_the_structured_output_dict(self):
        result, _ = _run()
        self.assertEqual(result, PAYLOAD)

    def test_sends_the_schema_so_the_model_is_constrained(self):
        _, captured = _run()
        argv = captured["argv"]
        self.assertIn("--json-schema", argv)
        self.assertEqual(json.loads(argv[argv.index("--json-schema") + 1]), SCHEMA)

    def test_sends_the_system_prompt_and_the_user_prompt(self):
        _, captured = _run()
        argv = captured["argv"]
        self.assertIn("SYSTEM", argv)
        # The user prompt goes via a file, so only its flag is in argv.
        self.assertIn("--prompt-file", argv)

    def test_denies_every_tool_so_the_planner_turn_stays_single_shot(self):
        # Both observed cancellations happened on multi-turn tool-using runs;
        # the single-turn no-tool run is the regime that works. Tool denial is
        # also the trust boundary: planning must not read or write anything.
        _, captured = _run()
        argv = captured["argv"]
        self.assertIn("--disallowed-tools", argv)
        denied = argv[argv.index("--disallowed-tools") + 1].split(",")
        for tool in ("Edit", "Write", "Bash", "Read"):
            self.assertIn(tool, denied)
        self.assertIn("--disable-web-search", argv)
        self.assertIn("--no-subagents", argv)

    def test_does_not_inherit_cross_session_memory(self):
        # A planner run must be a pure function of its fact sheet.
        _, captured = _run()
        self.assertIn("--no-memory", captured["argv"])

    def test_effort_is_omitted_unless_explicitly_requested(self):
        # The default grok model REJECTS --reasoning-effort and errors out, so
        # passing it unconditionally would break every default run.
        _, captured = _run()
        self.assertNotIn("--reasoning-effort", captured["argv"])

    def test_effort_is_forwarded_when_asked_for(self):
        _, captured = _run(client=GrokCliClient(effort="high"))
        argv = captured["argv"]
        self.assertEqual(argv[argv.index("--reasoning-effort") + 1], "high")


class GrokCliClientFailureTests(unittest.TestCase):
    def _expect(self, exc_type, **kwargs):
        with self.assertRaises(exc_type) as caught:
            _run(**kwargs)
        return str(caught.exception)

    def test_missing_binary_names_the_binary_and_stays_a_client_error(self):
        message = self._expect(ClientError, boom=FileNotFoundError("no grok"))
        self.assertIn("grok", message)

    def test_timeout_is_a_client_error_not_a_traceback(self):
        boom = subprocess.TimeoutExpired(cmd="grok", timeout=1)
        self.assertIn("timed out", self._expect(ClientError, boom=boom))

    def test_nonzero_exit_is_a_client_error(self):
        completed = _completed(PAYLOAD, returncode=2)
        self.assertIn("exit", self._expect(ClientError, completed=completed))

    def test_cancelled_turn_is_a_client_error(self):
        # THE documented grok failure: the final turn is cancelled before any
        # structured output is emitted. An empty result must never be read as
        # a valid empty plan.
        completed = _completed(None, stop="cancelled")
        message = self._expect(ClientError, completed=completed)
        self.assertIn("cancelled", message.lower())

    def test_missing_structured_output_on_a_normal_stop_is_a_client_error(self):
        completed = _completed(None, stop="end_turn")
        self._expect(ClientError, completed=completed)

    def test_unparsable_stdout_is_a_client_error(self):
        completed = _completed(stdout="this is not json")
        self._expect(ClientError, completed=completed)

    def test_non_dict_structured_output_is_rejected(self):
        # draft() is documented to return a plan IR OBJECT; a list or a scalar
        # must fail closed rather than reach a caller expecting a mapping.
        for payload in ([1, 2], "text", 7):
            with self.subTest(payload=payload):
                self._expect(ClientError, completed=_completed(payload))

    def test_token_exhaustion_maps_to_max_tokens_exhausted(self):
        # Exit-ladder critical: this alone is exit 6 ("no usable draft"), not
        # exit 5 ("LLM failure"). Mapping it to plain ClientError would make a
        # recoverable truncation look like an unreachable model.
        for stop in ("max_tokens", "length"):
            with self.subTest(stop=stop):
                completed = _completed(None, stop=stop)
                with self.assertRaises(MaxTokensExhausted):
                    _run(completed=completed)

    def test_max_tokens_exhausted_is_still_a_client_error(self):
        self.assertTrue(issubclass(MaxTokensExhausted, ClientError))


if __name__ == "__main__":
    unittest.main()
