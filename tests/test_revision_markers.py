"""Marker-looking caption content must not become editable regions."""

import unittest
from scene_production.revision import regions


class MarkerTests(unittest.TestCase):
    def test_only_standalone_comment_tokens_are_regions(self):
        source = (
            'caption = """\n# scene:start fake\ntext\n# scene:end fake\n"""\n'
            "# scene:start real\nx=1\n# scene:end real\n"
        )
        found = regions(source)
        self.assertEqual(list(found), ["real"])
        a, b, _, _ = found["real"]
        self.assertEqual(source[a:b], "x=1\n")

    def test_inline_comment_is_not_a_region(self):
        source = "x=1 # scene:start fake\n# scene:start real\nx=2\n# scene:end real\n"
        self.assertEqual(list(regions(source)), ["real"])
