"""Resolve an installed font and check its glyph coverage before rendering."""

import ctypes
import ctypes.util
import os
from pathlib import Path
import subprocess
import sys


def select_font(available, supports, requested=None):
    if requested:
        if requested not in available:
            raise ValueError("SCENE_FONT is not installed: " + requested)
        if not supports(requested):
            raise ValueError("SCENE_FONT has missing glyphs: " + requested)
        return requested
    preferred = ["PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", "WenQuanYi Zen Hei"]
    for family in dict.fromkeys(preferred + sorted(available)):
        if family in available and supports(family):
            return family
    raise ValueError("No installed font covers this scene. Set SCENE_FONT to a CJK font.")


def _library(name):
    found = ctypes.util.find_library(name)
    if found:
        return ctypes.CDLL(found)
    # pkg-config is already a Manim/Pango build prerequisite on macOS/Linux.
    directory = subprocess.check_output(
        [
            "pkg-config",
            "--variable=libdir",
            {"pango-1.0": "pango", "pangocairo-1.0": "pangocairo"}.get(name, name),
        ],
        text=True,
        timeout=10,
    ).strip()
    suffix = ".dylib" if sys.platform == "darwin" else ".so"
    return ctypes.CDLL(str(Path(directory) / ("lib" + name + suffix)))


def scene_font(required):
    import manimpango

    pango = _library("pango-1.0")
    cairo = _library("pangocairo-1.0")
    gobject = _library("gobject-2.0")

    def bind(lib, name, result, *args):
        fn = getattr(lib, name)
        fn.restype, fn.argtypes = result, list(args)
        return fn

    ptr = ctypes.c_void_p
    font_map = bind(cairo, "pango_cairo_font_map_get_default", ptr)()
    context = bind(pango, "pango_font_map_create_context", ptr, ptr)(font_map)
    desc_new = bind(pango, "pango_font_description_from_string", ptr, ctypes.c_char_p)
    desc_free = bind(pango, "pango_font_description_free", None, ptr)
    load = bind(pango, "pango_context_load_font", ptr, ptr, ptr)
    has = bind(pango, "pango_font_has_char", ctypes.c_int, ptr, ctypes.c_uint32)
    unref = bind(gobject, "g_object_unref", None, ptr)
    chars = {ord(c) for c in required if not c.isspace()}

    def supports(family):
        desc = desc_new((family + " 24").encode())
        font = load(context, desc)
        desc_free(desc)
        if not font:
            return False
        try:
            return all(has(font, code) for code in chars)
        finally:
            unref(font)

    try:
        return select_font(manimpango.list_fonts(), supports, os.environ.get("SCENE_FONT"))
    finally:
        unref(context)
