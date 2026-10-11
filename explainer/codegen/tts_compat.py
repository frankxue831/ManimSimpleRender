"""Thin wrapper over manim-voiceover's gTTS service (isolates the fragile bits).

Adds two production needs the stock ``GTTSService`` lacks, both found while
dogfooding (2026-06-13):

- a PERSISTENT audio cache. The render adapter works in a fresh temp dir per
  render, so the stock per-media-dir cache never survived a run: every render
  re-paid every gTTS network call, including a ``-ql`` smoke followed by a
  ``-qh`` final of IDENTICAL narration. The cache lives in one user-level dir
  (override with ``$EXPLAINER_TTS_CACHE``) keyed by narration text, so retries
  and re-renders reuse audio. It is partitioned PER LANGUAGE because
  upstream's cache key is the text alone (no lang/tld) -- a shared dir would
  serve English audio for a German render of the same text, forever.
- bounded retry with backoff per synthesis call. gTTS is a live Google
  endpoint and fails transiently (observed between two back-to-back renders);
  without retry one blip kills the whole render.

A corrupt ``cache.json`` (upstream writes it non-atomically; one interrupt can
truncate it) is quarantined rather than retried: with a persistent cache that
corruption would otherwise brick every future render. No locking is attempted;
two simultaneous renders sharing the cache can tear cache.json, which the next
run then heals at the cost of re-synthesis.

Like ``code_compat``, the generated scene imports this at render time via
``PYTHONPATH`` = repo root. Keep it pure stdlib + manim_voiceover.
"""

import json
import os
import sys
import time
from pathlib import Path

from manim_voiceover.defaults import DEFAULT_VOICEOVER_CACHE_JSON_FILENAME
from manim_voiceover.services.gtts import GTTSService

CACHE_ENV_VAR = "EXPLAINER_TTS_CACHE"
_DEFAULT_CACHE = os.path.join("~", ".cache", "explainer", "voiceovers")
RETRY_DELAYS_S = (2, 5, 10)


def tts_cache_dir():
    """The persistent voiceover cache dir (``$EXPLAINER_TTS_CACHE`` overrides)."""
    return os.path.expanduser(os.environ.get(CACHE_ENV_VAR) or _DEFAULT_CACHE)


def _quarantine_cache_json(cache_dir):
    """Remove a corrupt cache.json; losing it only costs re-synthesis."""
    try:
        (Path(cache_dir) / DEFAULT_VOICEOVER_CACHE_JSON_FILENAME).unlink(missing_ok=True)
    except OSError:
        pass  # unreadable AND undeletable: the synthesis call will surface it


def _heal_cache(cache_dir):
    """Delete cache.json if it is not valid JSON (truncated by an interrupt)."""
    cache_json = Path(cache_dir) / DEFAULT_VOICEOVER_CACHE_JSON_FILENAME
    if not cache_json.exists():
        return
    try:
        json.loads(cache_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _quarantine_cache_json(cache_dir)


class RetryingGTTSService(GTTSService):
    """``GTTSService`` with bounded retry/backoff around each synthesis call.

    Cache hits return inside the first attempt without touching the network.
    manim-voiceover (0.3.7 and 0.4.0 alike) raises a BARE ``Exception`` on gTTS
    errors, so there is no narrower type to catch; the attempt bound keeps that safe. A corrupt
    cache.json (``JSONDecodeError``) is NOT transient: it is quarantined and
    the attempt repeated immediately instead of sleeping. NOTE: when sharing a
    ``cache_dir`` across renders, partition it per language (upstream's cache
    key is the text alone) -- ``build_speech_service`` does this for you.
    """

    def __init__(self, lang="en", delays_s=RETRY_DELAYS_S, sleep=time.sleep, **kwargs):
        self._delays_s = tuple(delays_s)
        self._sleep = sleep
        super().__init__(lang=lang, **kwargs)

    def generate_from_text(self, text, cache_dir=None, path=None, **kwargs):
        last = len(self._delays_s)
        for attempt in range(last + 1):
            try:
                return super().generate_from_text(text, cache_dir=cache_dir, path=path, **kwargs)
            except json.JSONDecodeError:
                # Quarantine BEFORE the final re-raise so even a failed render
                # leaves a healed cache for the next run; retry immediately
                # (backoff would just delay the same local failure).
                _quarantine_cache_json(cache_dir or self.cache_dir)
                if attempt == last:
                    raise
            except Exception:
                if attempt == last:
                    raise
                delay = self._delays_s[attempt]
                print(
                    f"tts_compat: gTTS attempt {attempt + 1} failed; retrying in {delay}s",
                    file=sys.stderr,
                )
                self._sleep(delay)


def build_speech_service(lang="en"):
    """The speech service generated scenes install: persistent cache + retry.

    ``cache_dir`` is passed as a ``Path``: 0.3.7's SpeechService joined it with
    the ``/`` operator (a plain str raised TypeError at the first synthesis
    call); 0.4.0 accepts either and normalizes internally, so a Path works on
    both. Partitioned per language -- see module docstring.
    """
    cache = Path(tts_cache_dir()) / str(lang)
    cache.mkdir(parents=True, exist_ok=True)
    _heal_cache(cache)
    return RetryingGTTSService(lang=lang, cache_dir=cache)
