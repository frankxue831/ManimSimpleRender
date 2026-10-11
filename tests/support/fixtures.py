"""Builders for temporary scene trees used by the Tier A pipeline e2e tests."""

import os

_SCENE_TEMPLATE = (
    "from manim import *\n"
    "\n"
    "class {name}({base}):\n"
    "    def construct(self):\n"
    "        pass\n"
)


def write_scene(folder, filename, name, base="Scene"):
    """Write a single renderable scene file and return its path."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, filename)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(_SCENE_TEMPLATE.format(name=name, base=base))
    return path


def simple_scene_tree(root):
    """One file, one MovingCameraScene subclass (mirrors input/simple.py)."""
    write_scene(root, "simple.py", "DemoScene", base="MovingCameraScene")
    return root


def duplicate_name_tree(root):
    """Two files in different subdirs both defining `class Demo(Scene)` -- the real
    duplicate-class-name case (input/ has TestDebuggingWorkflowDetailed twice)."""
    write_scene(os.path.join(root, "a"), "scene.py", "Demo")
    write_scene(os.path.join(root, "b"), "scene.py", "Demo")
    return root


def mixed_tree(root):
    """A renderable scene, an abstract base (no construct), a non-scene, a broken file."""
    write_scene(root, "good.py", "GoodScene")
    with open(os.path.join(root, "abstract.py"), "w", encoding="utf-8") as handle:
        handle.write("from manim import *\nclass Base(Scene):\n    pass\n")  # no construct()
    with open(os.path.join(root, "notscene.py"), "w", encoding="utf-8") as handle:
        handle.write("class Helper:\n    pass\n")
    with open(os.path.join(root, "broken.py"), "w", encoding="utf-8") as handle:
        handle.write("class Oops(Scene)\n    def construct(self): pass\n")  # SyntaxError
    return root
