"""Tests for app.scenes.discovery (AST-based, import-free scene discovery). Batch 3.

Runs against the real input/ tree and confirms: renderable vs excluded (abstract
base), MovingCameraScene recognition, scene-to-file binding (so duplicate class
names across files stay distinct), and that discovery works with manim NOT
installed (proving it never imports the scanned modules).
Run: python3 -m unittest tests.test_discovery
"""

import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from unittest import mock

import app.scenes.discovery as discovery_mod
from app.scenes.discovery import (
    RECOGNIZED_SCENE_BASES,
    discover_scenes,
    python_file_count,
    unparsable_files,
)
from tests.support import fixtures

INPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "input")


def _write(tmp, name, body):
    with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
        f.write(body)


class DiscoverySnapshotTests(unittest.TestCase):
    def test_each_python_file_is_parsed_once_with_diagnostics(self):
        with tempfile.TemporaryDirectory() as folder:
            fixtures.mixed_tree(folder)
            expected_paths = [
                os.path.join(folder, name)
                for name in ("abstract.py", "broken.py", "good.py", "notscene.py")
            ]
            original = discovery_mod._parse_file
            with (
                mock.patch.object(discovery_mod, "_parse_file", wraps=original) as parse_mock,
                mock.patch.object(
                    discovery_mod, "_python_files", wraps=discovery_mod._python_files
                ) as files_mock,
            ):
                result = discovery_mod.discover_scene_result(folder)
            self.assertEqual(files_mock.call_count, 1)
            self.assertEqual(result.python_file_count, 4)
            self.assertEqual(parse_mock.call_count, 4)
            self.assertCountEqual(
                [call.args[0] for call in parse_mock.call_args_list],
                expected_paths,
            )
            self.assertIsInstance(result.scenes, tuple)
            self.assertIsInstance(result.skipped_files, tuple)
            self.assertEqual([s.scene_class for s in result.scenes], ["GoodScene"])
            self.assertEqual(result.skipped_files[0][0], "broken.py")
            self.assertIn("SyntaxError", result.skipped_files[0][1])
            with self.assertRaises(FrozenInstanceError):
                result.python_file_count = 0

    def test_snapshot_scenes_are_sorted_by_file_path_and_scene_class(self):
        with tempfile.TemporaryDirectory() as tmp:
            z_path = os.path.join(tmp, "z.py")
            a_path = os.path.join(tmp, "a.py")
            _write(
                tmp,
                "z.py",
                "from manim import *\n"
                "class ZedScene(Scene):\n"
                "    def construct(self):\n"
                "        pass\n",
            )
            _write(
                tmp,
                "a.py",
                "from manim import *\n"
                "class AlphaScene(Scene):\n"
                "    def construct(self):\n"
                "        pass\n",
            )
            result = discovery_mod.discover_scene_result(tmp)
            self.assertEqual(
                [(s.file_path, s.scene_class) for s in result.scenes],
                [(a_path, "AlphaScene"), (z_path, "ZedScene")],
            )

    def test_walk_error_is_reported_instead_of_silently_empty(self):
        error = PermissionError(13, "Permission denied")
        error.filename = "ignored"

        def fake_walk(input_folder, onerror=None):
            if onerror is not None:
                onerror(error)
            return iter(())

        with mock.patch.object(discovery_mod.os, "walk", side_effect=fake_walk):
            result = discovery_mod.discover_scene_result("ignored")
        self.assertEqual(result.scenes, ())
        self.assertEqual(result.unreadable_dirs, ((".", "PermissionError"),))

    def test_unreadable_subdir_is_recorded_but_sibling_scenes_survive(self):
        """One unlistable folder must not discard scenes found elsewhere in the tree."""
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                tmp,
                "good.py",
                "from manim import *\n"
                "class GoodScene(Scene):\n"
                "    def construct(self):\n"
                "        pass\n",
            )
            locked = os.path.join(tmp, "locked")
            os.mkdir(locked)
            error = PermissionError(13, "Permission denied")
            error.filename = locked
            real_walk = discovery_mod.os.walk

            def fake_walk(folder, onerror=None):
                for entry in real_walk(folder):
                    if entry[0] == locked:
                        if onerror is not None:
                            onerror(error)
                        continue
                    yield entry

            with mock.patch.object(discovery_mod.os, "walk", side_effect=fake_walk):
                result = discovery_mod.discover_scene_result(tmp)
            self.assertEqual([s.scene_class for s in result.scenes], ["GoodScene"])
            self.assertEqual(result.unreadable_dirs, (("locked", "PermissionError"),))

    @unittest.skipIf(
        hasattr(os, "geteuid") and os.geteuid() == 0, "root bypasses directory permissions"
    )
    def test_real_unreadable_subdir_does_not_discard_sibling_scenes(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                tmp,
                "good.py",
                "from manim import *\n"
                "class GoodScene(Scene):\n"
                "    def construct(self):\n"
                "        pass\n",
            )
            locked = os.path.join(tmp, "locked")
            os.mkdir(locked)
            os.chmod(locked, 0o000)
            try:
                result = discovery_mod.discover_scene_result(tmp)
            finally:
                os.chmod(locked, 0o700)  # let TemporaryDirectory clean up
            self.assertEqual([s.scene_class for s in result.scenes], ["GoodScene"])
            self.assertEqual([path for path, _ in result.unreadable_dirs], ["locked"])


class DiscoverRealTreeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenes = discover_scenes(INPUT_DIR)
        cls.by_name = {}
        for scene in cls.scenes:
            cls.by_name.setdefault(scene.scene_class, []).append(scene)

    def test_renderable_class_set(self):
        names = {s.scene_class for s in self.scenes}
        self.assertEqual(
            names,
            {
                "ProxyScenario1Scene",
                "ProxyScenario2Scene",
                "ImprovedMindMapAnimation07",
                "TestDebuggingWorkflow",
                "TestDebuggingWorkflowDetailed",
                "TestDebuggingWorkflowImproved",
            },
        )

    def test_seven_entries_total(self):
        # TestDebuggingWorkflowDetailed appears in two files -> 7 entries, 6 unique names.
        self.assertEqual(len(self.scenes), 7)

    def test_abstract_base_excluded(self):
        # ProxyScenarioBase resolves to Scene but defines no construct() -> excluded.
        self.assertNotIn("ProxyScenarioBase", self.by_name)

    def test_movingcamerascene_subclass_is_renderable(self):
        entry = self.by_name["ImprovedMindMapAnimation07"][0]
        self.assertTrue(entry.file_path.endswith("simple.py"))

    def test_scene_bound_to_owning_file(self):
        self.assertTrue(
            self.by_name["ProxyScenario1Scene"][0].file_path.endswith("proxy_pattern_scene.py")
        )

    def test_duplicate_class_name_bound_to_each_owning_file(self):
        # The same class name defined in two files yields two distinct entries,
        # each bound to its owning file (Cond 2) -- so duplicates stay independently
        # selectable without relying on a separate detection helper.
        entries = self.by_name["TestDebuggingWorkflowDetailed"]
        files = sorted(os.path.basename(s.file_path) for s in entries)
        self.assertEqual(
            files, ["test_debugging_workflow.py", "test_debugging_workflow_improved.py"]
        )


class DiscoverRobustnessTests(unittest.TestCase):
    def test_discovery_runs_without_manim_installed(self):
        # manim is not installed here; pure-AST discovery must still work.
        scenes = discover_scenes(INPUT_DIR)
        self.assertTrue(any(s.scene_class == "TestDebuggingWorkflow" for s in scenes))

    def test_broken_file_is_skipped_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "broken.py"), "w", encoding="utf-8") as f:
                f.write("class Oops(Scene)\n    def construct(self): pass\n")  # SyntaxError
            with open(os.path.join(tmp, "good.py"), "w", encoding="utf-8") as f:
                f.write(
                    "from manim import *\n"
                    "class GoodScene(Scene):\n"
                    "    def construct(self):\n"
                    "        pass\n"
                )
            scenes = discover_scenes(tmp)
        self.assertEqual({s.scene_class for s in scenes}, {"GoodScene"})

    def test_glob_metacharacters_in_path_do_not_break_discovery(self):
        # A folder path containing glob metachars ([], *, ?) must be treated as a
        # literal path, not a pattern -- else discovery silently finds nothing.
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "proj[v2]")
            os.makedirs(folder)
            with open(os.path.join(folder, "s.py"), "w", encoding="utf-8") as f:
                f.write(
                    "from manim import *\n"
                    "class A(Scene):\n"
                    "    def construct(self):\n"
                    "        pass\n"
                )
            self.assertEqual(python_file_count(folder), 1)
            self.assertEqual({s.scene_class for s in discover_scenes(folder)}, {"A"})

    def test_unreadable_entry_is_skipped_not_crash(self):
        # open() can raise OSError (a broken symlink, a perms error, or a file
        # removed between the walk and the open). Discovery must skip it, not crash
        # the GUI (browse/restore call discovery directly).
        with tempfile.TemporaryDirectory() as tmp:
            try:  # a broken *.py symlink: os.walk lists it, open() -> FileNotFoundError
                os.symlink(os.path.join(tmp, "missing.py"), os.path.join(tmp, "broken.py"))
            except (OSError, NotImplementedError):
                self.skipTest("symlinks not supported on this platform")
            with open(os.path.join(tmp, "good.py"), "w", encoding="utf-8") as f:
                f.write(
                    "from manim import *\n"
                    "class GoodScene(Scene):\n"
                    "    def construct(self):\n"
                    "        pass\n"
                )
            scenes = discover_scenes(tmp)  # must not raise
            self.assertEqual({s.scene_class for s in scenes}, {"GoodScene"})

    def test_utf8_bom_file_is_discovered(self):
        # BL-04: a valid source saved as "UTF-8 with BOM" (common on Windows/VS Code)
        # must parse; encoding='utf-8' left the BOM in the string -> SyntaxError -> dropped.
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "bom.py"), "w", encoding="utf-8-sig") as f:
                f.write(
                    "from manim import *\n"
                    "class BomScene(Scene):\n"
                    "    def construct(self):\n"
                    "        pass\n"
                )
            scenes = discover_scenes(tmp)
        self.assertEqual({s.scene_class for s in scenes}, {"BomScene"})

    def test_pep263_latin1_cookie_file_is_discovered(self):
        # BL-04: a PEP 263 coding cookie with a non-UTF-8 byte must be honored like
        # CPython does; encoding='utf-8' raised UnicodeDecodeError -> silently dropped.
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "lat.py"), "w", encoding="latin-1") as f:
                f.write(
                    "# -*- coding: latin-1 -*-\n"
                    "from manim import *\n"
                    "# caf\xe9 comment with a latin-1 byte\n"
                    "class LatScene(Scene):\n"
                    "    def construct(self):\n"
                    "        pass\n"
                )
            scenes = discover_scenes(tmp)
        self.assertEqual({s.scene_class for s in scenes}, {"LatScene"})

    def test_unparsable_files_reports_skipped_with_error(self):
        # BL-16: a syntax-error file is skipped by discovery; surface it so an editing
        # user knows the scene vanished because the file failed to parse.
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "broken.py", "class Oops(Scene)\n    def construct(self): pass\n")
            _write(
                tmp,
                "good.py",
                "from manim import *\nclass G(Scene):\n    def construct(self):\n        pass\n",
            )
            skipped = unparsable_files(tmp)
        self.assertEqual(len(skipped), 1)
        rel, err = skipped[0]
        self.assertEqual(rel, "broken.py")
        self.assertIn("SyntaxError", err)

    def test_unparsable_files_empty_when_all_parse(self):
        self.assertEqual(unparsable_files(INPUT_DIR), [])

    def test_nested_class_limitation_is_documented(self):
        # BL-32: scanning only tree.body misses Scene subclasses nested in a class/func.
        self.assertIn("nested", discovery_mod.__doc__.lower())

    def test_recursive_discovery_finds_subdir_scenes(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub = os.path.join(tmp, "pkg", "scenes")
            os.makedirs(sub)
            for path, cls in (
                (os.path.join(tmp, "root.py"), "RootScene"),
                (os.path.join(sub, "deep.py"), "DeepScene"),
            ):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(
                        "from manim import *\n"
                        f"class {cls}(Scene):\n"
                        "    def construct(self):\n"
                        "        pass\n"
                    )
            scenes = discover_scenes(tmp)
            self.assertEqual({s.scene_class for s in scenes}, {"RootScene", "DeepScene"})

    def test_ignored_directories_are_not_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            for junk in ("venv", "__pycache__", ".hidden", "media", "output"):
                d = os.path.join(tmp, junk)
                os.makedirs(d)
                with open(os.path.join(d, "x.py"), "w", encoding="utf-8") as f:
                    f.write(
                        "from manim import *\n"
                        "class JunkScene(Scene):\n"
                        "    def construct(self):\n"
                        "        pass\n"
                    )
            with open(os.path.join(tmp, "real.py"), "w", encoding="utf-8") as f:
                f.write(
                    "from manim import *\n"
                    "class RealScene(Scene):\n"
                    "    def construct(self):\n"
                    "        pass\n"
                )
            scenes = discover_scenes(tmp)
            self.assertEqual({s.scene_class for s in scenes}, {"RealScene"})
            self.assertEqual(python_file_count(tmp), 1)  # junk dirs not counted either

    def test_relative_path_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub = os.path.join(tmp, "pkg")
            os.makedirs(sub)
            with open(os.path.join(sub, "a.py"), "w", encoding="utf-8") as f:
                f.write(
                    "from manim import *\n"
                    "class A(Scene):\n"
                    "    def construct(self):\n"
                    "        pass\n"
                )
            scene = [s for s in discover_scenes(tmp) if s.scene_class == "A"][0]
            self.assertEqual(scene.relative_path, os.path.join("pkg", "a.py"))

    def test_recognized_bases_include_minimum_allowlist(self):
        for base in ("Scene", "MovingCameraScene", "ThreeDScene", "ZoomedScene"):
            self.assertIn(base, RECOGNIZED_SCENE_BASES)

    def test_unknown_base_is_not_renderable(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "x.py"), "w", encoding="utf-8") as f:
                f.write("class NotAScene(SomethingElse):\n    def construct(self):\n        pass\n")
            self.assertEqual(discover_scenes(tmp), [])


class Cond1ResolverTests(unittest.TestCase):
    """Per-file import + base-scope resolution (still pure AST; no user code imported)."""

    def _names(self, tmp):
        return {s.scene_class for s in discover_scenes(tmp)}

    def test_aliased_manim_import_is_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                tmp,
                "a.py",
                "from manim import Scene as S\n"
                "class X(S):\n"
                "    def construct(self):\n"
                "        pass\n",
            )
            self.assertEqual(self._names(tmp), {"X"})

    def test_module_attribute_base_is_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                tmp,
                "a.py",
                "import manim as m\n"
                "class X(m.Scene):\n"
                "    def construct(self):\n"
                "        pass\n",
            )
            self.assertEqual(self._names(tmp), {"X"})

    def test_star_import_still_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                tmp,
                "a.py",
                "from manim import *\n"
                "class X(Scene):\n"
                "    def construct(self):\n"
                "        pass\n",
            )
            self.assertEqual(self._names(tmp), {"X"})

    def test_shadowing_alias_is_not_resolved(self):
        # `Scene` here is actually `Foo` from another library -> not a manim scene.
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                tmp,
                "a.py",
                "from widgets import Foo as Scene\n"
                "class X(Scene):\n"
                "    def construct(self):\n"
                "        pass\n",
            )
            self.assertEqual(self._names(tmp), set())

    def test_same_named_bases_are_isolated_per_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                tmp,
                "a.py",
                "from manim import *\n"
                "class Base(Scene):\n"
                "    pass\n"
                "class AScene(Base):\n"
                "    def construct(self):\n"
                "        pass\n",
            )
            _write(
                tmp,
                "b.py",
                "class Base:\n"
                "    pass\n"
                "class BThing(Base):\n"
                "    def construct(self):\n"
                "        pass\n",
            )
            self.assertEqual(self._names(tmp), {"AScene"})  # B's non-scene Base is isolated

    def test_same_file_base_chain_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                tmp,
                "a.py",
                "from manim import *\n"
                "class Base(Scene):\n"
                "    pass\n"
                "class X(Base):\n"
                "    def construct(self):\n"
                "        pass\n",
            )
            self.assertEqual(self._names(tmp), {"X"})

    def test_base_cycle_does_not_recurse_forever(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                tmp,
                "a.py",
                "class A(B):\n"
                "    def construct(self):\n"
                "        pass\n"
                "class B(A):\n"
                "    def construct(self):\n"
                "        pass\n",
            )
            self.assertEqual(self._names(tmp), set())  # neither resolves; must not hang

    def test_cross_file_imported_base_is_documented_limitation(self):
        # Following imports into other files is intentionally out of scope (no
        # module-graph walking), so a base imported from another module is not
        # resolved. Recorded as a test so the boundary is explicit.
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "base.py", "from manim import *\nclass MyBase(Scene):\n    pass\n")
            _write(
                tmp,
                "x.py",
                "from base import MyBase\n"
                "class X(MyBase):\n"
                "    def construct(self):\n"
                "        pass\n",
            )
            self.assertNotIn("X", self._names(tmp))


if __name__ == "__main__":
    unittest.main()
