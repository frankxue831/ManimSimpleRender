"""Tests for app.validation.validate_render_request (pure, no widgets). Batch 5.

Preserves the 4 legacy gate rules (ui.py:205-210, 237-255) as data-only logic:
both folders non-empty, input exists, output exists, >=1 scene selected.
Run: python3 -m unittest tests.test_validation
"""

import tempfile
import unittest

from app.validation import ValidationResult, manim_available, validate_render_request


class ValidateRenderRequestTests(unittest.TestCase):
    def test_both_folders_empty(self):
        result = validate_render_request("", "", ["SomeScene"])
        self.assertFalse(result.ok)
        self.assertTrue(any("both" in e.lower() for e in result.errors))

    def test_missing_input_folder(self):
        with tempfile.TemporaryDirectory() as out:
            result = validate_render_request("/no/such/input/dir", out, ["SomeScene"])
        self.assertFalse(result.ok)
        self.assertTrue(any("input" in e.lower() for e in result.errors))

    def test_missing_output_folder_is_allowed_for_creation(self):
        # A non-existent output folder is OK; the controller creates it at render time.
        with tempfile.TemporaryDirectory() as inp:
            result = validate_render_request(inp, "/no/such/output/dir", ["SomeScene"])
        self.assertTrue(result.ok)

    def test_no_scenes_selected(self):
        with tempfile.TemporaryDirectory() as inp, tempfile.TemporaryDirectory() as out:
            result = validate_render_request(inp, out, [])
        self.assertFalse(result.ok)
        self.assertTrue(any("scene" in e.lower() for e in result.errors))

    def test_all_rules_satisfied(self):
        with tempfile.TemporaryDirectory() as inp, tempfile.TemporaryDirectory() as out:
            result = validate_render_request(inp, out, ["ProxyScenario1Scene"])
        self.assertTrue(result.ok)
        self.assertEqual(result.errors, [])

    def test_multiple_errors_accumulate(self):
        result = validate_render_request("", "", [])
        self.assertFalse(result.ok)
        self.assertGreaterEqual(len(result.errors), 2)

    def test_returns_data_only(self):
        result = validate_render_request("", "", [])
        self.assertIsInstance(result, ValidationResult)
        self.assertIsInstance(result.errors, list)


class ManimAvailableTests(unittest.TestCase):
    """Pre-flight probe (find_spec) so a missing manim is reported before a render.

    Probes the SAME interpreter that runs the render -- the command base is
    [sys.executable, '-m', 'manim', ...] -- so the probe and the spawn agree
    (robust for both module-only and pip-installed envs).
    """

    def test_true_when_find_spec_resolves_manim(self):
        self.assertTrue(manim_available(find_spec=lambda name: object()))

    def test_false_when_find_spec_returns_none(self):
        self.assertFalse(manim_available(find_spec=lambda name: None))

    def test_probes_for_the_manim_module(self):
        seen = []

        def fake_find_spec(name):
            seen.append(name)
            return object()

        manim_available(find_spec=fake_find_spec)
        self.assertEqual(seen, ["manim"])

    def test_false_when_find_spec_raises(self):
        # find_spec can raise (e.g. ModuleNotFoundError when a parent package is
        # absent); treat any resolution failure as "not available", never crash.
        def boom(name):
            raise ModuleNotFoundError(name)

        self.assertFalse(manim_available(find_spec=boom))

    def test_frozen_checks_path_not_import(self):
        # In a frozen app the render uses an EXTERNAL manim (not `-m manim` in the
        # bundled interpreter), so the gate must probe PATH, not find_spec.
        self.assertTrue(manim_available(frozen=True, which=lambda name: "/x/manim"))
        self.assertFalse(manim_available(frozen=True, which=lambda name: None))


if __name__ == "__main__":
    unittest.main()
