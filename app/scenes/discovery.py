"""AST-based discovery of renderable Manim Scene subclasses (import-free, no Qt).

Replaces the legacy static config 'scenes' list (which carried phantom names and
no file binding). Each renderable class is bound to its OWNING file, so duplicate
class names across files stay distinct.

Condition 1: because ``from manim import *`` defeats source tracking, recognition
relies on a ratified allowlist of Manim Scene base names
(:data:`RECOGNIZED_SCENE_BASES`). A class is renderable iff it defines
``construct()`` AND its base chain reaches a name in that allowlist. Resolution is
PER FILE: a base name is matched against the file's own classes (recursing through
same-file base chains) and its import aliases (``from manim import Scene as S`` ->
``S`` resolves as ``Scene``; a shadowing ``from x import Foo as Scene`` is
correctly rejected), so same-named bases in different files never cross-
contaminate. Abstract bases (no ``construct()``, e.g. ProxyScenarioBase) are
excluded. LIMITATION: a base imported from ANOTHER scanned file
(``from .base import MyBase``) is not followed (no module-graph walking), so such
a subclass is only discovered if it also inherits a recognized base directly.
LIMITATION: only top-level classes (``tree.body``) are inspected, so a Scene
subclass nested inside another class or a function is not discovered (BL-32) --
manim's CLI likewise requires a module-level scene name.
"""

import ast
import os
import tokenize
from dataclasses import dataclass

# Errors that mean "this file is not usable source" -- skipped by discovery and
# reported by unparsable_files. LookupError covers a bad PEP 263 declared codec.
_PARSE_ERRORS = (OSError, SyntaxError, UnicodeDecodeError, ValueError, LookupError)

# Ratified allowlist of Manim Scene base classes (Condition 1). Names outside
# this set are treated as non-Manim. Extend here if a scene subclasses another
# Manim scene type.
RECOGNIZED_SCENE_BASES = frozenset(
    {
        "Scene",
        "MovingCameraScene",
        "ThreeDScene",
        "SpecialThreeDScene",
        "ZoomedScene",
        "VectorScene",
        "LinearTransformationScene",
    }
)

# Directories never scanned for scenes: virtualenvs, caches, manim output, VCS/IDE
# metadata, build artifacts. Pruned during the recursive walk; hidden directories
# (names starting with ".") are skipped too.
_IGNORED_DIRS = frozenset(
    {
        "venv",
        ".venv",
        "__pycache__",
        "media",
        "output",
        "build",
        "dist",
        "node_modules",
        ".git",
        ".tox",
        ".idea",
        ".vscode",
    }
)


@dataclass(frozen=True)
class DiscoveredScene:
    """A renderable scene: its class name bound to the file that defines it."""

    file_path: str
    scene_class: str
    base: str  # immediate base name, informational (e.g. "Scene", "ProxyScenarioBase")
    relative_path: str = ""  # path relative to the scanned folder, for display


@dataclass(frozen=True)
class DiscoveryResult:
    """One-pass snapshot of discovery under a folder."""

    scenes: tuple[DiscoveredScene, ...]
    python_file_count: int
    skipped_files: tuple[tuple[str, str], ...]
    # Directories the walk could not list, as (relative_path, error). Reported
    # rather than fatal: an unreadable folder hides only its own subtree, so the
    # scenes found everywhere else stay usable.
    unreadable_dirs: tuple[tuple[str, str], ...] = ()


def _discover_from_tree(path, tree, input_folder):
    """Extract renderable scenes from one already-parsed AST (no I/O)."""
    # Resolve names within THIS file only -- its own classes plus its import
    # aliases. Bases imported from another module are intentionally not
    # followed (see the module docstring), which keeps same-named bases in
    # different files from cross-contaminating.
    discovered = []
    class_defs = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    class_table = {node.name: node for node in class_defs}
    aliases = _import_aliases(tree)
    for node in class_defs:
        if not _has_construct(node):
            continue  # abstract base / helper, not directly renderable
        if _class_resolves(node, class_table, aliases, set()):
            bases = _base_names(node)
            discovered.append(
                DiscoveredScene(
                    file_path=path,
                    scene_class=node.name,
                    base=bases[0] if bases else "",
                    relative_path=os.path.relpath(path, input_folder),
                )
            )
    return discovered


def discover_scene_result(input_folder):
    """One-pass discovery: parse each *.py once, return scenes + skips + count."""
    paths, unreadable_dirs = _python_files(input_folder)
    discovered = []
    skipped = []
    for path in paths:
        try:
            tree = _parse_file(path)
        except _PARSE_ERRORS as exc:
            detail = type(exc).__name__
            lineno = getattr(exc, "lineno", None)
            if lineno:
                detail += f" line {lineno}"
            skipped.append((os.path.relpath(path, input_folder), detail))
            continue  # skip unreadable/unparseable entry; do not crash discovery
        discovered.extend(_discover_from_tree(path, tree, input_folder))
    discovered.sort(key=lambda scene: (scene.file_path, scene.scene_class))
    return DiscoveryResult(tuple(discovered), len(paths), tuple(skipped), unreadable_dirs)


def discover_scenes(input_folder):
    """Return renderable :class:`DiscoveredScene` entries found under ``input_folder``.

    Recurses into subdirectories (pruning :data:`_IGNORED_DIRS` and hidden dirs).
    Pure AST inspection - never imports the scanned modules or manim. Files that
    fail to parse/read are skipped (not fatal), as are directories that cannot be
    listed; call :func:`discover_scene_result` to see what was skipped and why.
    Sorted by (file_path, class).
    """
    return list(discover_scene_result(input_folder).scenes)


def _parse_file(path):
    """Read and AST-parse ``path``, honoring a UTF-8 BOM and PEP 263 coding cookies.

    ``tokenize.open`` detects the source encoding exactly like CPython does (BL-04),
    so "UTF-8 with BOM" and ``# -*- coding: latin-1 -*-`` files parse instead of
    being silently dropped. Raises one of :data:`_PARSE_ERRORS` on failure.
    """
    with tokenize.open(path) as handle:
        return ast.parse(handle.read(), path)


def unparsable_files(input_folder):
    """Return ``[(relative_path, error)]`` for *.py files that fail to read/parse.

    Discovery skips such files (so it never crashes); this lets the UI tell the user
    a file was skipped -- e.g. a scene that vanished because of a syntax error -- in
    a non-fatal warning, instead of leaving them to guess (BL-16).
    """
    return list(discover_scene_result(input_folder).skipped_files)


def python_file_count(input_folder):
    """Count *.py files under ``input_folder`` (recursive, ignoring _IGNORED_DIRS).

    Used to distinguish an empty-of-Python folder from one whose Python files
    simply contain no renderable scenes, so the UI can give an actionable reason
    instead of one generic "nothing found" message.
    """
    paths, _unreadable = _python_files(input_folder)
    return len(paths)


def _python_files(input_folder):
    """Return ``(sorted *.py paths, unreadable directories)`` under ``input_folder``.

    Recurses into subdirs but prunes ignored/hidden directories. Uses os.walk (no
    globbing), so paths containing glob metacharacters ([], *, ?) are handled
    literally. os.walk swallows listing errors by default, which would report an
    unreadable tree as an honest-looking empty one; the ``onerror`` hook records
    each failure as ``(relative_path, error)`` instead, so the caller can surface
    it without losing the scenes found in the rest of the tree.
    """
    found = []
    unreadable = []

    def on_error(error):
        failed = getattr(error, "filename", None) or input_folder
        unreadable.append((os.path.relpath(failed, input_folder), type(error).__name__))

    for dirpath, dirnames, filenames in os.walk(input_folder, onerror=on_error):
        # prune in-place so os.walk never descends into ignored/hidden dirs
        dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS and not d.startswith(".")]
        for name in filenames:
            if name.endswith(".py"):
                found.append(os.path.join(dirpath, name))
    return sorted(found), tuple(sorted(unreadable))


def _base_names(node):
    names = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)  # e.g. manim.Scene -> "Scene"
    return names


def _has_construct(node):
    return any(
        isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == "construct"
        for child in node.body
    )


def _import_aliases(tree):
    """Map each locally-bound import name to its ORIGINAL imported name, for
    top-level ``from X import Name [as alias]`` (star imports are skipped -- those
    are handled by the name allowlist). Lets an aliased base resolve by its real
    name (e.g. ``from manim import Scene as S`` -> ``S`` resolves as ``Scene``),
    and lets a shadowing alias (``from x import Foo as Scene``) be rejected."""
    aliases = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    aliases[alias.asname or alias.name] = alias.name
    return aliases


def _class_resolves(node, class_table, aliases, seen):
    """True if ``node``'s base chain (within this file) reaches a recognized base.
    ``seen`` guards against base cycles."""
    if node.name in seen:
        return False
    seen.add(node.name)
    return any(_base_resolves(base, class_table, aliases, seen) for base in node.bases)


def _base_resolves(base, class_table, aliases, seen):
    if isinstance(base, ast.Attribute):
        return base.attr in RECOGNIZED_SCENE_BASES  # e.g. manim.Scene / m.Scene
    if isinstance(base, ast.Name):
        local = class_table.get(base.id)
        if local is not None:
            return _class_resolves(local, class_table, aliases, seen)  # same-file base
        return aliases.get(base.id, base.id) in RECOGNIZED_SCENE_BASES
    return False  # other base expressions (subscripts, calls) are not handled
