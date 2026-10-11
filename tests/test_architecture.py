"""Architecture guard: executable form of docs/adr/0001-render-core-boundary.md.

AST-based -- no project modules are imported (mirrors the discovery idiom), so
this runs headless without Qt installed. It enforces STATIC imports only:
plain ``import`` / ``from`` statements anywhere in the tree (including under
``if TYPE_CHECKING:``, and including relative imports, which are resolved
against the importing file's package) plus constant-string
``importlib.import_module("...")`` and ``__import__("...")`` calls. Out of
static reach (and out of scope): imports built from non-constant strings,
aliased forms (``from importlib import import_module as im``), ``__import__``
reaching submodules via ``fromlist``, and the two-argument relative
``import_module`` form.

Rules (one test each):
  1. No module under app/ imports explainer.
  2. No module under app/render/ imports Qt -- except app/render/worker.py,
     the named GUI-side QThread adapter.
  3. explainer/ imports from app only app.render.*, never app.render.worker.
  4. No module under app/render/ imports app.ui or app.bootstrap.
  5. No module under app/render/ other than worker.py itself imports
     app.render.worker -- it is not part of the core API, and importing it
     would drag Qt into the core transitively.

Run: python3 -m unittest tests.test_architecture
"""

import ast
import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QT_PACKAGES = ("PyQt5", "PyQt6", "PySide2", "PySide6", "qtpy")
WORKER_PATH = os.path.join("app", "render", "worker.py")


def _py_files(*relparts):
    root = os.path.join(REPO_ROOT, *relparts)
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name.endswith(".py"):
                found.append(os.path.join(dirpath, name))
    return found


def _imported_modules(path, rel):
    """Yield every module name statically imported by the file at *path*.

    *rel* is the repo-relative path; relative imports are resolved against
    its package (``app/render/foo.py`` resolving ``from ..ui import theme``
    yields ``app.ui.theme``). For ``from pkg import name`` the joined
    ``pkg.name`` is yielded: ``name`` may be a submodule (e.g. ``from
    app.render import worker``), and the joined form lets prefix rules catch
    that without resolving whether ``name`` is a module or a symbol.
    """
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    package = rel.split(os.sep)[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                parts = node.module.split(".") if node.module else []
            else:
                cut = len(package) - (node.level - 1)
                if cut < 0:
                    continue
                parts = package[:cut]
                if node.module:
                    parts = parts + node.module.split(".")
            if not parts:
                continue
            base = ".".join(parts)
            for alias in node.names:
                yield base + "." + alias.name
        elif isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else (func.id if isinstance(func, ast.Name) else None)
            )
            if name in ("import_module", "__import__") and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    yield arg.value


def _is_or_under(module, prefix):
    return module == prefix or module.startswith(prefix + ".")


class RenderCoreBoundaryTests(unittest.TestCase):
    def _check(self, files, predicate):
        self.assertTrue(files, "guard scanned no files -- tree layout changed?")
        bad = []
        for path in files:
            rel = os.path.relpath(path, REPO_ROOT)
            for module in _imported_modules(path, rel):
                if predicate(rel, module):
                    bad.append(rel + " imports " + module)
        self.assertEqual(bad, [])

    def test_app_never_imports_explainer(self):
        self._check(
            _py_files("app"),
            lambda rel, mod: _is_or_under(mod, "explainer"),
        )

    def test_render_core_is_qt_free_except_worker(self):
        def predicate(rel, mod):
            if rel == WORKER_PATH:
                return False
            return any(_is_or_under(mod, qt) for qt in QT_PACKAGES)

        self._check(_py_files("app", "render"), predicate)

    def test_explainer_uses_only_the_render_core(self):
        def predicate(rel, mod):
            if _is_or_under(mod, "app.render.worker"):
                return True
            return _is_or_under(mod, "app") and not _is_or_under(mod, "app.render")

        self._check(_py_files("explainer"), predicate)

    def test_render_core_never_imports_ui_or_bootstrap(self):
        self._check(
            _py_files("app", "render"),
            lambda rel, mod: _is_or_under(mod, "app.ui") or _is_or_under(mod, "app.bootstrap"),
        )

    def test_render_core_does_not_import_worker(self):
        def predicate(rel, mod):
            if rel == WORKER_PATH:
                return False
            return _is_or_under(mod, "app.render.worker")

        self._check(_py_files("app", "render"), predicate)

    def test_storyboard_core_imports_no_codegen_or_cite(self):
        # The IR core must not depend on codegen or the citation gate, so the
        # loader can run layout validation and the verifier can read citation
        # targets from the model without an import cycle.
        def predicate(rel, mod):
            return _is_or_under(mod, "explainer.codegen") or _is_or_under(mod, "explainer.cite")

        self._check(_py_files("explainer", "storyboard"), predicate)
