"""Private implementation identity distinguishes changed code without Git."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.render.implementation import implementation_identity, write_implementation_manifest


class ImplementationIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in (
            "app/__init__.py",
            "explainer/__init__.py",
            "scene_production/__init__.py",
            "app/config/config.v2.json",
            "app/ui/theme.qss",
        ):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("original")
        root_patch = patch("app.render.implementation._SOURCE_ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def test_source_fingerprint_tracks_code_not_scenes_or_environment(self):
        before = implementation_identity()["source"]
        (self.root / "private_scene.py").write_text("SECRET_SCENE")
        with patch.dict(os.environ, {"PRIVATE_AUTH": "SECRET_AUTH"}):
            unchanged = implementation_identity()
        self.assertEqual(before, unchanged["source"])
        (self.root / "app/__init__.py").write_text("changed")
        after = implementation_identity()["source"]
        self.assertNotEqual(before["sha256"], after["sha256"])
        self.assertEqual(before["files"].keys(), after["files"].keys())
        encoded = json.dumps(unchanged)
        for private in (str(self.root), "SECRET_AUTH", "SECRET_SCENE", "private_scene.py"):
            self.assertNotIn(private, encoded)

    def test_unavailable_source_is_explicit_unknown_not_partial_fingerprint(self):
        (self.root / "app/__init__.py").unlink()
        source = implementation_identity()["source"]
        self.assertEqual(source["status"], "unknown")
        self.assertNotIn("sha256", source)

    def test_symlink_does_not_follow_arbitrary_private_file(self):
        private = self.root / "private.py"
        private.write_text("SECRET_SOURCE")
        (self.root / "app/linked.py").symlink_to(private)
        result = implementation_identity()
        self.assertEqual(result["source"]["status"], "unknown")
        self.assertNotIn("SECRET_SOURCE", json.dumps(result))

    def test_manifest_is_private_correlated_and_exclusive(self):
        path = self.root / "implementation.json"
        self.assertTrue(write_implementation_manifest(path, operation="render", run_id="run-1"))
        first = path.read_bytes()
        value = json.loads(first)
        self.assertEqual(value["run_id"], "run-1")
        self.assertEqual(value["implementation"]["source"]["status"], "observed")
        if os.name == "posix":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertFalse(write_implementation_manifest(path, operation="render", run_id="run-2"))
        self.assertEqual(path.read_bytes(), first)

    def test_write_failure_is_best_effort_and_partial_file_is_removed(self):
        path = self.root / "implementation.json"
        with patch("app.render.implementation.json.dump", side_effect=OSError("SECRET")):
            self.assertFalse(
                write_implementation_manifest(path, operation="render", run_id="run-1")
            )
        self.assertFalse(path.exists())
