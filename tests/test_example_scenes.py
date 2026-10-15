"""Example scenes must still find the files they locate from __file__.

examples/scenes/proxy_pattern_scene.py builds ASSETS from its own path. When the
scenes moved from input/ to examples/scenes/, that path silently pointed at a
folder that does not exist, and the scene's fallback icon hid it: the render
still succeeded, with a substitute check mark. CI has no manim and never renders
these scenes, so this test reads them with ast instead of importing them.

Supported: a module-level `NAME = <expression using __file__>`, evaluated with
only Path and __file__ in scope, and `NAME / "a" / "b"` joins on that name. Any
other use of __file__ fails the test, so a new pattern is reported instead of
skipped; extend the test when that happens. Not followed: NAME.joinpath(...),
f-string joins, names derived from NAME, and a local variable that reuses NAME.
Run: python3 -m unittest tests.test_example_scenes
"""

import ast
import os
import unittest
from pathlib import Path

_SCENES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples", "scenes"
)


def _is_file_name(node):
    return isinstance(node, ast.Name) and node.id == "__file__"


def _literal_join(node, roots):
    """The path for `ROOT / "a" / "b"` on a root name, or None for anything else."""
    if isinstance(node, ast.Name):
        return roots.get(node.id)
    if (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Div)
        and isinstance(node.right, ast.Constant)
        and isinstance(node.right.value, str)
    ):
        left = _literal_join(node.left, roots)
        return None if left is None else left / node.right.value
    return None


def _file_relative_paths(path):
    """Yield (where, path) for each path the scene at *path* derives from __file__.

    Raises AssertionError for any use of __file__ outside the supported pattern.
    """
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    name = os.path.basename(path)
    roots, evaluated = {}, set()
    for node in tree.body:
        if not (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and any(_is_file_name(n) for n in ast.walk(node.value))
        ):
            continue
        where = f"{name}:{node.lineno}"
        code = compile(ast.Expression(node.value), path, "eval")
        try:
            value = eval(code, {"__builtins__": {}, "Path": Path, "__file__": path})
        except Exception as exc:
            raise AssertionError(f"{where}: cannot evaluate ({exc!r}); extend this test")
        if not isinstance(value, Path):
            raise AssertionError(f"{where}: {value!r} is not a Path; extend this test")
        roots[node.targets[0].id] = value
        evaluated.update(id(n) for n in ast.walk(node.value) if _is_file_name(n))
        yield where, value
    for node in ast.walk(tree):
        if _is_file_name(node) and id(node) not in evaluated:
            raise AssertionError(
                f"{name}:{node.lineno}: unsupported use of __file__; extend this test"
            )
        if isinstance(node, ast.BinOp):
            joined = _literal_join(node, roots)
            if joined is not None:
                yield f"{name}:{node.lineno}", joined


class ExampleSceneTests(unittest.TestCase):
    def test_file_relative_paths_exist(self):
        checked = []
        for name in sorted(os.listdir(_SCENES_DIR)):
            if not name.endswith(".py"):
                continue
            with self.subTest(scene=name):  # one scene's failure does not hide the next
                for where, target in _file_relative_paths(os.path.join(_SCENES_DIR, name)):
                    checked.append(str(target))
                    with self.subTest(at=where):
                        self.assertTrue(target.exists(), f"{where} -> {target}")
        # Guard against a vacuous pass: the proxy scene's icon must be among them.
        self.assertTrue(any(t.endswith("green_check.svg") for t in checked), checked)


if __name__ == "__main__":
    unittest.main()
