"""Tests for app.render.command_builder (pure manim-argv construction). Batch 2.

The command is invoked as a module of the CURRENT interpreter --
[sys.executable, '-m', 'manim', <file>, <scene>, <resolution_flag>,
'--media_dir', <out>](+ engine flag) -- so the spawn uses the same Python env
that the pre-flight probe checks (app.validation.manim_available via find_spec).
Run: python3 -m unittest tests.test_command_builder
"""

import shlex
import sys
import unittest

from app.render.command_builder import build_command, display_command, manim_base_command
from app.render.jobs import RenderJob


class BuildCommandTests(unittest.TestCase):
    def setUp(self):
        self.job = RenderJob(
            file_path="examples/scenes/simple.py", scene_class="ImprovedMindMapAnimation07"
        )

    def test_default_engine_exact_argv(self):
        cmd = build_command(
            self.job, resolution_flag="-qh", output_dir="out", engine_renderer_flag=""
        )
        self.assertEqual(
            cmd,
            [
                sys.executable,
                "-m",
                "manim",
                "examples/scenes/simple.py",
                "ImprovedMindMapAnimation07",
                "-qh",
                "--media_dir",
                "out",
            ],
        )
        self.assertEqual(len(cmd), 8)

    def test_invokes_manim_as_module_of_current_interpreter(self):
        # Robust for module-only AND pip-installed envs, and matches the probe's
        # interpreter -- no reliance on a bare `manim` shim being on PATH.
        cmd = build_command(self.job, "-qh", "out")
        self.assertEqual(cmd[0], sys.executable)
        self.assertEqual(cmd[1:3], ["-m", "manim"])

    def test_engine_flag_appended_last_when_nonempty(self):
        cmd = build_command(self.job, "-qk", "out", "--renderer=opengl")
        self.assertEqual(len(cmd), 9)
        self.assertEqual(cmd[-1], "--renderer=opengl")

    def test_empty_engine_flag_appends_no_token(self):
        cmd = build_command(self.job, "-ql", "out", "")
        self.assertEqual(len(cmd), 8)
        self.assertNotIn("", cmd)  # no stray empty token

    def test_file_and_scene_positionals(self):
        cmd = build_command(self.job, "-qm", "out")
        self.assertEqual(cmd[3], "examples/scenes/simple.py")  # file positional
        self.assertEqual(cmd[4], "ImprovedMindMapAnimation07")  # scene positional

    def test_manim_base_command_dev_uses_interpreter_module(self):
        self.assertEqual(manim_base_command(frozen=False), [sys.executable, "-m", "manim"])

    def test_manim_base_command_frozen_uses_external_manim_on_path(self):
        # In a frozen app, sys.executable is the GUI binary, so `-m manim` would
        # relaunch the GUI -> use an external manim on PATH instead.
        base = manim_base_command(frozen=True, which=lambda name: "/usr/local/bin/manim")
        self.assertEqual(base, ["/usr/local/bin/manim"])

    def test_manim_base_command_frozen_falls_back_to_bare_name(self):
        base = manim_base_command(frozen=True, which=lambda name: None)
        self.assertEqual(base, ["manim"])  # runner maps a missing binary to MANIM_NOT_FOUND

    def test_build_command_accepts_explicit_base(self):
        cmd = build_command(self.job, "-qh", "out", base_command=["/opt/manim"])
        self.assertEqual(cmd[0], "/opt/manim")
        self.assertEqual(cmd[1:3], ["examples/scenes/simple.py", "ImprovedMindMapAnimation07"])
        self.assertEqual(cmd[3:], ["-qh", "--media_dir", "out"])

    def test_returns_list_of_str_and_is_pure(self):
        first = build_command(self.job, "-qh", "out", "--renderer=opengl")
        second = build_command(self.job, "-qh", "out", "--renderer=opengl")
        self.assertEqual(first, second)
        self.assertTrue(all(isinstance(token, str) for token in first))


@unittest.skipIf(sys.platform == "win32", "POSIX shell-quoting semantics")
class DisplayCommandTests(unittest.TestCase):
    """BL-01: the copied/echoed command string must be shell-safe, not ' '.join()."""

    def test_round_trips_argv_with_spaces(self):
        job = RenderJob(file_path="/abs path/with spaces/file.py", scene_class="MyScene")
        argv = build_command(job, "-qh", "/out dir/x", "", base_command=["manim"])
        self.assertEqual(shlex.split(display_command(argv)), argv)

    def test_spaced_path_is_quoted_not_split(self):
        argv = build_command(RenderJob("/a b/f.py", "S"), "-qh", "/o p", "", base_command=["manim"])
        # naive ' '.join would re-split '/a b/f.py' into two shell words.
        self.assertNotEqual(display_command(argv), " ".join(argv))
        self.assertEqual(len(shlex.split(display_command(argv))), len(argv))

    def test_shell_metacharacters_are_neutralized(self):
        # A tampered flag must not become an executable clipboard string.
        argv = ["manim", "-ql; rm -rf ~", "/out"]
        tokens = shlex.split(display_command(argv))
        self.assertEqual(tokens, argv)  # ';' stays inside one token, not a command sep


if __name__ == "__main__":
    unittest.main()
