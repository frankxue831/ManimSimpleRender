"""Tests for explainer.codegen.tts_compat -- persistent TTS cache + retry.

Why this module exists (dogfood findings, 2026-06-13): the adapter renders in a
fresh temp workdir, so manim-voiceover's audio cache never survived between
runs -- every render (including a -ql smoke before a -qh final of IDENTICAL
narration) re-paid every gTTS network call; and one transient gTTS failure
killed a whole render with no retry.

Imports manim_voiceover (heavy), so these skip where it is not installed
(mirrors the test_code_compat guard).
"""

import importlib.util
import os
import tempfile
import unittest
from unittest import mock


@unittest.skipUnless(importlib.util.find_spec("manim_voiceover"), "manim_voiceover not installed")
class TtsCacheDirTests(unittest.TestCase):
    def test_default_cache_dir_is_persistent_and_absolute(self):
        from explainer.codegen import tts_compat

        env = {k: v for k, v in os.environ.items() if k != tts_compat.CACHE_ENV_VAR}
        with mock.patch.dict(os.environ, env, clear=True):
            cache = tts_compat.tts_cache_dir()
        self.assertTrue(os.path.isabs(cache))
        self.assertIn(os.path.join(".cache", "explainer"), cache)

    def test_env_var_overrides_cache_dir(self):
        from explainer.codegen import tts_compat

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {tts_compat.CACHE_ENV_VAR: tmp}):
                self.assertEqual(tts_compat.tts_cache_dir(), tmp)

    def test_build_speech_service_creates_and_uses_persistent_cache(self):
        from pathlib import Path

        from explainer.codegen import tts_compat

        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, "voiceovers")
            with mock.patch.dict(os.environ, {tts_compat.CACHE_ENV_VAR: cache}):
                service = tts_compat.build_speech_service(lang="en")
            # Compare as Paths: upstream 0.3.7 stored the Path we passed, 0.4.0
            # coerces to str (and normalizes internally) -- the contract we rely
            # on is WHERE the cache lives, not the attribute's type.
            self.assertEqual(Path(service.cache_dir), Path(cache) / "en")
            self.assertTrue(Path(service.cache_dir).is_dir())

    def test_cache_is_partitioned_per_language(self):
        # Upstream's cache key is the text alone (no lang), so a shared dir
        # would serve English audio for a German render of identical text.
        from pathlib import Path

        from explainer.codegen import tts_compat

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {tts_compat.CACHE_ENV_VAR: tmp}):
                en = tts_compat.build_speech_service(lang="en")
                de = tts_compat.build_speech_service(lang="de")
        self.assertNotEqual(Path(en.cache_dir), Path(de.cache_dir))
        self.assertEqual(Path(en.cache_dir).name, "en")
        self.assertEqual(Path(de.cache_dir).name, "de")

    def test_corrupt_cache_json_is_healed_at_build(self):
        # Upstream writes cache.json non-atomically; one interrupt truncates it
        # and (with a persistent cache) would brick every future render.
        from manim_voiceover.defaults import DEFAULT_VOICEOVER_CACHE_JSON_FILENAME

        from explainer.codegen import tts_compat

        with tempfile.TemporaryDirectory() as tmp:
            corrupt = os.path.join(tmp, "en", DEFAULT_VOICEOVER_CACHE_JSON_FILENAME)
            os.makedirs(os.path.dirname(corrupt), exist_ok=True)
            with open(corrupt, "w", encoding="utf-8") as fh:
                fh.write('[{"truncated": ')
            with mock.patch.dict(os.environ, {tts_compat.CACHE_ENV_VAR: tmp}):
                tts_compat.build_speech_service(lang="en")
            self.assertFalse(os.path.exists(corrupt))


@unittest.skipUnless(importlib.util.find_spec("manim_voiceover"), "manim_voiceover not installed")
class RetryingGTTSServiceTests(unittest.TestCase):
    def _service(self, tmp, sleeps):
        from explainer.codegen.tts_compat import RetryingGTTSService

        return RetryingGTTSService(lang="en", cache_dir=tmp, sleep=sleeps.append)

    def test_transient_failures_are_retried_with_backoff(self):
        from manim_voiceover.services.gtts import GTTSService

        sleeps = []
        calls = {"n": 0}

        def flaky(self, text, cache_dir=None, path=None, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                # manim-voiceover (0.3.7 and 0.4.0 alike) raises a BARE Exception
                # on gTTS errors, so the retry has no narrower type to catch.
                raise Exception("gTTS gave an error.")
            return {"original_audio": "ok.mp3"}

        with tempfile.TemporaryDirectory() as tmp:
            service = self._service(tmp, sleeps)
            with mock.patch.object(GTTSService, "generate_from_text", new=flaky):
                result = service.generate_from_text("hello world")
        self.assertEqual(result, {"original_audio": "ok.mp3"})
        self.assertEqual(calls["n"], 3)
        self.assertEqual(sleeps, [2, 5])

    def test_raises_after_exhausting_retries(self):
        from manim_voiceover.services.gtts import GTTSService

        sleeps = []
        calls = {"n": 0}

        def always_down(self, text, cache_dir=None, path=None, **kwargs):
            calls["n"] += 1
            raise Exception("gTTS gave an error.")

        with tempfile.TemporaryDirectory() as tmp:
            service = self._service(tmp, sleeps)
            with mock.patch.object(GTTSService, "generate_from_text", new=always_down):
                with self.assertRaises(Exception):
                    service.generate_from_text("hello world")
        self.assertEqual(calls["n"], 4)  # 1 attempt + 3 retries
        self.assertEqual(sleeps, [2, 5, 10])

    def test_first_try_success_never_sleeps(self):
        from manim_voiceover.services.gtts import GTTSService

        sleeps = []

        def healthy(self, text, cache_dir=None, path=None, **kwargs):
            return {"original_audio": "ok.mp3"}

        with tempfile.TemporaryDirectory() as tmp:
            service = self._service(tmp, sleeps)
            with mock.patch.object(GTTSService, "generate_from_text", new=healthy):
                service.generate_from_text("hello world")
        self.assertEqual(sleeps, [])

    def test_corrupt_cache_json_is_quarantined_not_slept_on(self):
        # JSONDecodeError is not transient: backoff would just delay the same
        # failure. Quarantine the cache file and retry immediately instead.
        import json

        from manim_voiceover.defaults import DEFAULT_VOICEOVER_CACHE_JSON_FILENAME
        from manim_voiceover.services.gtts import GTTSService

        sleeps = []
        calls = {"n": 0}

        def corrupt_then_ok(self, text, cache_dir=None, path=None, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise json.JSONDecodeError("Expecting value", '[{"truncated": ', 0)
            return {"original_audio": "ok.mp3"}

        with tempfile.TemporaryDirectory() as tmp:
            cache_json = os.path.join(tmp, DEFAULT_VOICEOVER_CACHE_JSON_FILENAME)
            with open(cache_json, "w", encoding="utf-8") as fh:
                fh.write('[{"truncated": ')
            service = self._service(tmp, sleeps)
            with mock.patch.object(GTTSService, "generate_from_text", new=corrupt_then_ok):
                result = service.generate_from_text("hello world")
            self.assertFalse(os.path.exists(cache_json))  # quarantined
        self.assertEqual(result, {"original_audio": "ok.mp3"})
        self.assertEqual(sleeps, [])  # immediate retry, no backoff


if __name__ == "__main__":
    unittest.main()
