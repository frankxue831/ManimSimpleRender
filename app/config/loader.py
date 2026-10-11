"""Load and validate ManimSimpleRender config (schema v2).

Fixes the legacy gaps: the path is resolved relative to this module (not the
process CWD), and missing/malformed/invalid configs raise a typed
:class:`ConfigError` instead of an unguarded ``FileNotFoundError`` /
``JSONDecodeError`` / ``KeyError`` (legacy: ``config.py:3-5``, ``main.py:7``).
"""

import json
import logging
import os

from app.config.models import (
    AppConfig,
    ConfigError,
    EngineOption,
    ResolutionOption,
)
from app.render.quality import known_quality_flags

SCHEMA_VERSION = 2

# Recognized manim quality flags / renderer flags. The loader validates config
# values against these so a typo or a tampered entry (e.g. "-ql; rm -rf ~") fails
# at load instead of flowing into the manim argv (BL-15).
# Quality flags derive from the canonical ladder (app/render/quality.py, ADR 0001);
# engine flags are defined locally.
_VALID_RESOLUTION_FLAGS = frozenset(known_quality_flags())
_VALID_ENGINE_FLAGS = frozenset({"", "--renderer=opengl", "--renderer=cairo"})

_logger = logging.getLogger(__name__)


def _reject_duplicates(values, where, field, source):
    """Raise ConfigError if ``values`` contains a duplicate (case-sensitive)."""
    seen = set()
    for value in values:
        if value in seen:
            raise ConfigError(f"Duplicate {where} {field} {value!r}: {source}")
        seen.add(value)


# Packaged default config, resolved relative to this module so load_config()
# does not depend on the current working directory.
DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.v2.json")


def load_config(path=None):
    """Load and validate a config file, returning a typed :class:`AppConfig`.

    ``path=None`` loads the packaged default (:data:`DEFAULT_CONFIG_PATH`).
    Raises :class:`ConfigError` (chaining the underlying cause) on any failure.
    """
    config_path = DEFAULT_CONFIG_PATH if path is None else path
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Config file is not valid JSON ({config_path}): {exc}") from exc
    return _parse(raw, config_path)


def default_config():
    """Return the built-in default :class:`AppConfig` (mirrors packaged config.v2.json).

    Resolutions and engines are static and known, so a corrupt/missing packaged
    config can degrade to these defaults rather than killing the app (BL-33).
    """
    return AppConfig(
        schema_version=SCHEMA_VERSION,
        resolutions=[
            ResolutionOption(label="Low Quality (-ql)", flag="-ql"),
            ResolutionOption(label="Medium Quality (-qm)", flag="-qm"),
            ResolutionOption(label="High Quality (-qh)", flag="-qh"),
            ResolutionOption(label="4K Quality (-qk)", flag="-qk"),
        ],
        engines=[
            EngineOption(label="Default", renderer_flag=""),
            EngineOption(label="OpenGL (experimental)", renderer_flag="--renderer=opengl"),
        ],
    )


def load_config_or_default(path=None, load=load_config):
    """Load a config, falling back to :func:`default_config` for a broken *packaged* config.

    When ``path is None`` (the packaged default) and loading raises
    :class:`ConfigError`, log a warning and return the built-in defaults so the
    app still launches (BL-33). An explicitly user-supplied ``path`` re-raises so
    the caller can surface the error. ``load`` is injectable for testing.
    """
    try:
        return load(path)
    except ConfigError as exc:
        if path is not None:
            raise
        _logger.warning(
            "Packaged config failed to load (%s); falling back to built-in defaults.", exc
        )
        return default_config()


def _parse(raw, source):
    if not isinstance(raw, dict):
        raise ConfigError(f"Config root must be a JSON object: {source}")
    version = raw.get("schema_version")
    # Strict int check: bool is a subclass of int, and JSON 2.0 == 2, so reject both.
    if not isinstance(version, int) or isinstance(version, bool) or version != SCHEMA_VERSION:
        raise ConfigError(
            f"Unsupported schema_version {version!r} (expected int {SCHEMA_VERSION}): {source}"
        )
    for key in ("resolutions", "engines"):
        if key not in raw:
            raise ConfigError(f"Missing required config key '{key}': {source}")
    resolutions = _parse_resolutions(raw["resolutions"], source)
    engines = _parse_engines(raw["engines"], source)
    default_index = raw.get("default_resolution_index", 0)
    if (
        not isinstance(default_index, int)
        or isinstance(default_index, bool)
        or not (0 <= default_index < len(resolutions))
    ):
        raise ConfigError(
            f"default_resolution_index must be an int in [0, {len(resolutions)}): {source}"
        )
    return AppConfig(
        schema_version=SCHEMA_VERSION,
        resolutions=resolutions,
        engines=engines,
        default_resolution_index=default_index,
    )


def _parse_resolutions(items, source):
    if not isinstance(items, list) or not items:
        raise ConfigError(f"'resolutions' must be a non-empty list: {source}")
    parsed = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ConfigError(f"resolutions[{index}] must be an object: {source}")
        label = item.get("label")
        flag = item.get("flag")
        if not isinstance(label, str) or not label:
            raise ConfigError(f"resolutions[{index}].label must be a non-empty string: {source}")
        if not isinstance(flag, str) or not flag:
            raise ConfigError(f"resolutions[{index}].flag must be a non-empty string: {source}")
        if flag not in _VALID_RESOLUTION_FLAGS:
            raise ConfigError(
                f"resolutions[{index}].flag {flag!r} is not a recognized manim quality flag "
                f"(expected one of {sorted(_VALID_RESOLUTION_FLAGS)}): {source}"
            )
        parsed.append(ResolutionOption(label=label, flag=flag))
    _reject_duplicates([r.label for r in parsed], "resolution", "label", source)
    return parsed


def _parse_engines(items, source):
    if not isinstance(items, list) or not items:
        raise ConfigError(f"'engines' must be a non-empty list: {source}")
    parsed = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ConfigError(f"engines[{index}] must be an object: {source}")
        label = item.get("label")
        renderer_flag = item.get("renderer_flag")
        if not isinstance(label, str) or not label:
            raise ConfigError(f"engines[{index}].label must be a non-empty string: {source}")
        if not isinstance(renderer_flag, str):  # "" is allowed (cairo default)
            raise ConfigError(f"engines[{index}].renderer_flag must be a string: {source}")
        if renderer_flag not in _VALID_ENGINE_FLAGS:
            raise ConfigError(
                f"engines[{index}].renderer_flag {renderer_flag!r} is not recognized "
                f"(expected one of {sorted(_VALID_ENGINE_FLAGS)}): {source}"
            )
        parsed.append(EngineOption(label=label, renderer_flag=renderer_flag))
    _reject_duplicates([e.label for e in parsed], "engine", "label", source)
    _reject_duplicates([e.renderer_flag for e in parsed], "engine", "renderer_flag", source)
    return parsed
