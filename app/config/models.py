"""Typed configuration models for ManimSimpleRender (config schema v2).

Collapses the legacy parallel arrays (``resolutions`` / ``resolution_flags``)
into a single list of :class:`ResolutionOption`, and moves the engine ->
renderer-flag map into data as :class:`EngineOption` (replacing the old
``"OpenGL" in text`` substring check). No Qt, no I/O here.
"""

from dataclasses import dataclass


class ConfigError(Exception):
    """Raised when a config file is missing, malformed, or fails schema validation."""


@dataclass
class ResolutionOption:
    """A selectable render resolution: display label + emitted manim quality flag."""

    label: str
    flag: str


@dataclass
class EngineOption:
    """A selectable render engine: display label + emitted renderer flag.

    ``renderer_flag`` is ``""`` for the default (cairo) engine, which the
    command builder treats as "append no engine token".
    """

    label: str
    renderer_flag: str


@dataclass
class AppConfig:
    """The fully-parsed, validated application config (schema v2)."""

    schema_version: int
    resolutions: list  # list[ResolutionOption]
    engines: list  # list[EngineOption]
    # Index of the resolution selected on a fresh launch; data-driven so the default
    # is a fast preview, not the heaviest preset by list position (BL-17).
    default_resolution_index: int = 0
