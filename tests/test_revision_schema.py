"""Authoring mistakes have bounded, consistent manifest diagnostics."""

import json
import unittest
from scene_production import revision as rev
import tests.test_scene_revision as fixtures


class ManifestSchemaTests(unittest.TestCase):
    setUp = fixtures.RevisionTests.setUp
    make_run = fixtures.RevisionTests.make_run

    def test_captions_remain_required_and_must_be_a_list_of_nonblank_strings(self):
        valid = json.loads(self.manifest.read_text())
        invalid = [None, "caption", {}, True, 1, [None], [1], [False], [[]], [""], [" \t\n"]]
        for value in invalid:
            with self.subTest(captions=value):
                m = json.loads(json.dumps(valid))
                m["segments"][0]["captions"] = value
                self.manifest.write_text(json.dumps(m))
                with self.assertRaisesRegex(ValueError, "invalid segment fields"):
                    rev.manifest(self.manifest)
        del valid["segments"][0]["captions"]
        self.manifest.write_text(json.dumps(valid))
        with self.assertRaisesRegex(ValueError, "invalid segment fields"):
            rev.manifest(self.manifest)

    def test_caption_free_segments_still_require_objects_and_valid_dependencies(self):
        valid = json.loads(self.manifest.read_text())
        for segment in valid["segments"]:
            segment["captions"] = []
        for change in (
            {"objects": []},
            {"depends_on": ["missing"]},
            {"depends_on": ["intro"]},
            {"depends_on": ["target"]},
        ):
            with self.subTest(change=change):
                m = json.loads(json.dumps(valid))
                m["segments"][0].update(change)
                self.manifest.write_text(json.dumps(m))
                with self.assertRaisesRegex(ValueError, "objects and valid dependencies"):
                    rev.manifest(self.manifest)

    def test_caption_free_segments_still_require_brief_sources_and_pitfalls(self):
        valid = json.loads(self.manifest.read_text())
        for segment in valid["segments"]:
            segment["captions"] = []
        for field in ("sources", "pitfalls"):
            with self.subTest(field=field):
                m = json.loads(json.dumps(valid))
                m["brief"][field] = []
                self.manifest.write_text(json.dumps(m))
                with self.assertRaisesRegex(ValueError, "invalid brief fields"):
                    rev.manifest(self.manifest)

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
