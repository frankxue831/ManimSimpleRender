"""Tests for explainer.plan.client.

The Anthropic SDK is NOT installed in the test environment and must not be
required: these tests inject a fake module into sys.modules, which also pins
that the real client imports the SDK lazily rather than at module scope.
"""

import json
import sys
import types
import unittest
from unittest.mock import patch

from explainer.plan.client import AnthropicClient, ClientError, FakeClient, MaxTokensExhausted

SCHEMA = {"type": "object"}


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Response:
    def __init__(self, payload=None, stop_reason="end_turn"):
        self.stop_reason = stop_reason
        self.stop_details = None
        self.content = [_Block(json.dumps(payload or {"ok": True}))]


def _fake_sdk(response=None, boom=None):
    """Build a stand-in `anthropic` module exposing the beta messages API."""
    module = types.ModuleType("anthropic")
    captured = {}

    class _Messages:
        def create(self, **kwargs):
            captured.update(kwargs)
            if boom is not None:
                raise boom
            return response or _Response()

    class _Client:
        def __init__(self, *a, **k):
            self.beta = types.SimpleNamespace(messages=_Messages())

    module.Anthropic = _Client
    return module, captured


class FakeClientTests(unittest.TestCase):
    def test_returns_the_canned_payload(self):
        fake = FakeClient({"title": "T"})
        self.assertEqual(fake.draft("s", "p", SCHEMA), {"title": "T"})

    def test_records_calls_for_assertions(self):
        fake = FakeClient({"title": "T"})
        fake.draft("sys", "prompt", SCHEMA)
        self.assertEqual(fake.calls, [("sys", "prompt")])


class FakeClientSequenceTests(unittest.TestCase):
    def test_a_sequence_is_returned_call_by_call_last_repeating(self):
        client = FakeClient([{"n": 1}, {"n": 2}])
        self.assertEqual(client.draft("s", "p", {}), {"n": 1})
        self.assertEqual(client.draft("s", "p", {}), {"n": 2})
        self.assertEqual(client.draft("s", "p", {}), {"n": 2})
        self.assertEqual(len(client.calls), 3)

    def test_a_single_dict_repeats_forever(self):
        client = FakeClient({"n": 1})
        self.assertEqual(client.draft("s", "p", {}), {"n": 1})
        self.assertEqual(client.draft("s", "p", {}), {"n": 1})


class AnthropicClientTests(unittest.TestCase):
    def test_missing_sdk_reports_the_install_command(self):
        with patch.dict(sys.modules, {"anthropic": None}):
            with self.assertRaises(ClientError) as caught:
                AnthropicClient().draft("s", "p", SCHEMA)
        self.assertIn("planner", str(caught.exception))

    def test_missing_api_key_raises_client_error(self):
        # anthropic.Anthropic() itself raises synchronously when no API key
        # is configured (the real SDK: "The api_key client option must be
        # set..."). client.py is the one place API failures are translated
        # to ClientError, so this must not leak the raw SDK exception type.
        module = types.ModuleType("anthropic")

        class _NoKeyClient:
            def __init__(self, *a, **k):
                raise RuntimeError("The api_key client option must be set")

        module.Anthropic = _NoKeyClient
        with patch.dict(sys.modules, {"anthropic": module}):
            with self.assertRaises(ClientError):
                AnthropicClient().draft("s", "p", SCHEMA)

    def test_sends_schema_and_effort_inside_output_config(self):
        module, captured = _fake_sdk()
        with patch.dict(sys.modules, {"anthropic": module}):
            AnthropicClient(effort="high").draft("s", "p", SCHEMA)
        self.assertEqual(captured["output_config"]["format"]["schema"], SCHEMA)
        self.assertEqual(captured["output_config"]["effort"], "high")

    def test_never_sends_rejected_sampling_parameters(self):
        module, captured = _fake_sdk()
        with patch.dict(sys.modules, {"anthropic": module}):
            AnthropicClient().draft("s", "p", SCHEMA)
        for rejected in ("temperature", "top_p", "top_k"):
            self.assertNotIn(rejected, captured)

    def test_opts_into_server_side_refusal_fallbacks(self):
        module, captured = _fake_sdk()
        with patch.dict(sys.modules, {"anthropic": module}):
            AnthropicClient().draft("s", "p", SCHEMA)
        self.assertEqual(captured["fallbacks"], "default")
        self.assertIn("server-side-fallback-2026-07-01", captured["betas"])

    def test_refusal_raises_before_content_is_read(self):
        module, _ = _fake_sdk(_Response(stop_reason="refusal"))
        with patch.dict(sys.modules, {"anthropic": module}):
            with self.assertRaises(ClientError) as caught:
                AnthropicClient().draft("s", "p", SCHEMA)
        self.assertIn("refus", str(caught.exception).lower())

    def test_max_tokens_stop_reason_raises_max_tokens_exhausted(self):
        # P6: distinguishable from a generic ClientError -- MaxTokensExhausted
        # is a ClientError subtype, so `except ClientError` elsewhere still
        # catches it, but explainer/cli.py checks for this subtype FIRST so
        # it alone can map to exit 6 ("no usable draft") instead of exit 5
        # ("LLM failure"), without matching on the message string.
        module, _ = _fake_sdk(_Response(stop_reason="max_tokens"))
        with patch.dict(sys.modules, {"anthropic": module}):
            with self.assertRaises(MaxTokensExhausted) as caught:
                AnthropicClient().draft("s", "p", SCHEMA)
        self.assertIn("max-tokens", str(caught.exception).lower())
        self.assertIsInstance(caught.exception, ClientError)

    def test_refusal_is_client_error_but_not_max_tokens_exhausted(self):
        # The negative case: a refusal is still a plain ClientError, not the
        # exit-6 subtype -- collapsing every ClientError into exit 6 would
        # defeat the point of the split.
        module, _ = _fake_sdk(_Response(stop_reason="refusal"))
        with patch.dict(sys.modules, {"anthropic": module}):
            with self.assertRaises(ClientError) as caught:
                AnthropicClient().draft("s", "p", SCHEMA)
        self.assertNotIsInstance(caught.exception, MaxTokensExhausted)

    def test_returns_the_parsed_payload(self):
        module, _ = _fake_sdk(_Response({"title": "T"}))
        with patch.dict(sys.modules, {"anthropic": module}):
            self.assertEqual(AnthropicClient().draft("s", "p", SCHEMA), {"title": "T"})

    def test_transport_failure_is_wrapped(self):
        module, _ = _fake_sdk(boom=RuntimeError("network down"))
        with patch.dict(sys.modules, {"anthropic": module}):
            with self.assertRaises(ClientError):
                AnthropicClient().draft("s", "p", SCHEMA)

    def test_non_dict_json_payload_raises_client_error(self):
        # draft() is documented (design spec: `-> dict`) to return a dict.
        # Valid JSON that decodes to a list or a bare number must not be
        # returned as-is -- it must fail closed as ClientError, the single
        # boundary error type this module exists to guarantee.
        for payload in ([1, 2, 3], 42):
            with self.subTest(payload=payload):
                module, _ = _fake_sdk(_Response(payload))
                with patch.dict(sys.modules, {"anthropic": module}):
                    with self.assertRaises(ClientError):
                        AnthropicClient().draft("s", "p", SCHEMA)


if __name__ == "__main__":
    unittest.main()
