"""Authoring mistakes have bounded, consistent manifest diagnostics."""

import json
import unittest
from scene_production import revision as rev
import tests.test_scene_revision as fixtures


class ManifestSchemaTests(unittest.TestCase):
    setUp = fixtures.RevisionTests.setUp
    make_run = fixtures.RevisionTests.make_run

    def test_invalid_field_types_raise_value_error(self):
        valid = json.loads(self.manifest.read_text())
        mutations = [
            lambda m: m.update(scene=True),
            lambda m: m.update(source=3),
            lambda m: m.update(dependencies="helper.py"),
            lambda m: m.update(brief=[]),
            lambda m: m["brief"].update(audience=True),
            lambda m: m["brief"].update(sources="url"),
            lambda m: m.update(segments=None),
            lambda m: m["segments"][0].update(objects="dot"),
            lambda m: m["segments"][0].update(depends_on="intro"),
            lambda m: m["segments"].__setitem__(0, None),
        ]
        for mutation in mutations:
            m = json.loads(json.dumps(valid))
            mutation(m)
            self.manifest.write_text(json.dumps(m))
            with self.subTest(manifest=m):
                with self.assertRaises(ValueError):
                    rev.manifest(self.manifest)
