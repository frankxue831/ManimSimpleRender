"""Initial receiving bootstrap must not import the untrusted working directory."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scene_production.portable import export_project

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scene_production" / "launcher.py"


class ReceiverLauncherTests(unittest.TestCase):
    def test_isolated_launcher_ignores_project_shadow_modules_and_pythonpath(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "project.zip"
            export_project(ROOT / "examples/handoff/project.json", archive)
            project = root / "untrusted project"
            project.mkdir()
            marker = project / "EXECUTED"
            code = "from pathlib import Path\nPath('EXECUTED').write_text('ran')\n"
            for name in ("scene_production.py", "platform.py", "sitecustomize.py"):
                (project / name).write_text(code)
            received = root / "received"
            for args, statuses in (
                (["inspect", str(archive)], {0}),
                (["unpack", str(archive), str(received)], {0}),
                (["preflight", str(received)], {0, 2}),
            ):
                with self.subTest(command=args[0]):
                    result = subprocess.run(
                        [sys.executable, "-I", str(LAUNCHER), "portable", *args],
                        cwd=project,
                        env={
                            "PATH": os.defpath,
                            "PYTHONPATH": str(project) + os.pathsep + str(ROOT),
                        },
                        capture_output=True,
                        text=True,
                        timeout=20,
                    )
                    self.assertIn(result.returncode, statuses, result.stderr)
                    json.loads(result.stdout)
                    self.assertFalse(marker.exists())

    def test_exported_preferred_recipe_uses_the_isolated_trusted_launcher(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            handoff = export_project(ROOT / "examples/handoff/project.json", root / "project.zip")
            # Recipes are declarative: resolve the trusted tool, never imported code.
            command = list(handoff["commands"]["render"])
            token = handoff["commands"]["receiving_launcher"]["argv_placeholder"]
            self.assertIn(token, command)
            command[0] = sys.executable
            command[command.index(token)] = str(LAUNCHER)
            command.append("--help")  # Exercise this actual recipe's bootstrap, not rendering.
            for name in ("scene_production.py", "platform.py", "sitecustomize.py"):
                (root / name).write_text("from pathlib import Path\nPath('EXECUTED').touch()\n")
            result = subprocess.run(
                command,
                cwd=root,
                env={"PATH": os.defpath, "PYTHONPATH": str(root)},
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--output-dir", result.stdout)
            self.assertFalse((root / "EXECUTED").exists())

    def test_launcher_requires_isolated_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run(
                [sys.executable, str(LAUNCHER), "portable", "--help"],
                cwd=folder,
                env={"PATH": os.defpath},
                capture_output=True,
                text=True,
                timeout=20,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("-I", result.stderr)


class ReinspectionLauncherTests(unittest.TestCase):
    def test_inspect_help_uses_reinspection_parser_without_project_imports(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ("scene_production.py", "platform.py", "sitecustomize.py"):
                (root / name).write_text("from pathlib import Path\nPath('EXECUTED').touch()\n")
            result = subprocess.run(
                [sys.executable, "-I", str(LAUNCHER), "inspect", "--help"],
                cwd=root,
                env={"PATH": os.defpath, "PYTHONPATH": str(root)},
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--from-run", result.stdout)
            self.assertNotIn("--scene-file", result.stdout)
            self.assertFalse((root / "EXECUTED").exists())
