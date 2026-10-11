"""Bounded revisions preserve the diagram and expose actual step changes."""

import copy
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def example():
    return {
        "version": 1,
        "title": "Cache",
        "font": "PingFang SC",
        "nodes": [
            {"id": "a", "label": "Request", "x": -3, "y": 0},
            {"id": "b", "label": "Cache", "x": 3, "y": 0},
            {"id": "c", "label": "Not shown", "x": 0, "y": 2},
        ],
        "edges": [{"from": "a", "to": "b"}],
        "steps": [
            {"show": ["a"], "highlight": ["a"], "caption": "First request", "duration": 5},
            {"show": ["b"], "highlight": ["b"], "caption": "Reusable result", "duration": 5},
        ],
    }


class ConceptRevision(unittest.TestCase):
    def validate_revision(self, original, candidate):
        self.assertTrue(
            (ROOT / "concept_render/revision.py").is_file(),
            "bounded concept revision validation is missing",
        )
        return importlib.import_module("concept_render.revision").validate_revision(
            original, candidate
        )

    def test_caption_and_duration_change_report_does_not_mutate_inputs(self):
        original = example()
        candidate = copy.deepcopy(original)
        candidate["steps"][0]["caption"] = "Read the existing result"
        candidate["steps"][0]["duration"] = 7
        before = copy.deepcopy((original, candidate))
        report = self.validate_revision(original, candidate)
        self.assertEqual(
            report,
            {
                "changes": [
                    {
                        "step": 1,
                        "field": "caption",
                        "before": "First request",
                        "after": "Read the existing result",
                    },
                    {"step": 1, "field": "duration", "before": 5, "after": 7},
                ],
                "duration_before": 10,
                "duration_after": 12,
            },
        )
        self.assertEqual((original, candidate), before)

    def test_all_static_diagram_fields_are_immutable(self):
        edits = {
            "version": lambda x: x.update(version=2),
            "title": lambda x: x.update(title="Changed title"),
            "font": lambda x: x.update(font="Other font"),
            "nodes": lambda x: x["nodes"][0].update(x=-2),
            "edges": lambda x: x.update(edges=[]),
        }
        for field, edit in edits.items():
            with self.subTest(field=field):
                candidate = example()
                edit(candidate)
                with self.assertRaises(ValueError):
                    self.validate_revision(example(), candidate)

    def test_step_count_is_preserved(self):
        candidate = example()
        candidate["steps"].append(
            {"show": [], "highlight": [], "caption": "Extra step", "duration": 2}
        )
        with self.assertRaisesRegex(ValueError, "step count"):
            self.validate_revision(example(), candidate)

    def test_noop_is_not_a_revision(self):
        with self.assertRaisesRegex(ValueError, "change"):
            self.validate_revision(example(), example())

    def test_highlight_permutation_alone_is_not_a_revision(self):
        original = example()
        original["steps"][1]["highlight"] = ["a", "b"]
        candidate = copy.deepcopy(original)
        candidate["steps"][1]["highlight"] = ["b", "a"]
        with self.assertRaisesRegex(ValueError, "change"):
            self.validate_revision(original, candidate)

    def test_highlight_permutation_is_omitted_from_actual_change_report(self):
        original = example()
        original["steps"][1]["highlight"] = ["a", "b"]
        candidate = copy.deepcopy(original)
        candidate["steps"][1]["highlight"] = ["b", "a"]
        candidate["steps"][1]["caption"] = "Use the cached result"
        report = self.validate_revision(original, candidate)
        self.assertEqual(
            report["changes"],
            [
                {
                    "step": 2,
                    "field": "caption",
                    "before": "Reusable result",
                    "after": "Use the cached result",
                }
            ],
        )

    def test_show_permutation_within_a_step_changes_reveal_order(self):
        original = example()
        original["steps"][0]["show"] = ["a", "b"]
        original["steps"][1]["show"] = []
        candidate = copy.deepcopy(original)
        candidate["steps"][0]["show"] = ["b", "a"]
        report = self.validate_revision(original, candidate)
        self.assertEqual(
            report["changes"],
            [{"step": 1, "field": "show", "before": ["a", "b"], "after": ["b", "a"]}],
        )

    def test_reordered_reveals_are_allowed_and_reported(self):
        candidate = example()
        candidate["steps"][0]["show"] = ["b"]
        candidate["steps"][0]["highlight"] = ["b"]
        candidate["steps"][1]["show"] = ["a"]
        candidate["steps"][1]["highlight"] = ["a"]
        report = self.validate_revision(example(), candidate)
        self.assertEqual(
            report["changes"],
            [
                {"step": 1, "field": "show", "before": ["a"], "after": ["b"]},
                {"step": 1, "field": "highlight", "before": ["a"], "after": ["b"]},
                {"step": 2, "field": "show", "before": ["b"], "after": ["a"]},
                {"step": 2, "field": "highlight", "before": ["b"], "after": ["a"]},
            ],
        )

    def test_cannot_remove_or_introduce_a_shown_node(self):
        for shown in ([], ["b", "c"]):
            with self.subTest(shown=shown):
                candidate = example()
                candidate["steps"][1]["show"] = shown
                candidate["steps"][1]["highlight"] = []
                with self.assertRaisesRegex(ValueError, "shown nodes"):
                    self.validate_revision(example(), candidate)

    def test_invalid_original_or_candidate_is_rejected_by_native_grammar(self):
        for invalid_side in ("original", "candidate"):
            for bad_step in (
                {"highlight": ["b"]},
                {"duration": 11},
                {"duration": True},
                {"duration": float("nan")},
                {"caption": "x" * 121},
                {"code": "print('no')"},
            ):
                with self.subTest(side=invalid_side, bad=bad_step):
                    original = example()
                    candidate = example()
                    candidate["steps"][0]["caption"] = "Modified"
                    target = original if invalid_side == "original" else candidate
                    target["steps"][0].update(bad_step)
                    with self.assertRaises(ValueError):
                        self.validate_revision(original, candidate)

    def run_cli(self, original_bytes, candidate_bytes):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original.json"
            candidate = root / "candidate.json"
            original.write_bytes(original_bytes)
            candidate.write_bytes(candidate_bytes)
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "concept_render.revision",
                    "--original",
                    str(original),
                    "--candidate",
                    str(candidate),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(original.read_bytes(), original_bytes)
            self.assertEqual(candidate.read_bytes(), candidate_bytes)
            self.assertEqual(
                sorted(path.name for path in root.iterdir()), ["candidate.json", "original.json"]
            )
            return result

    def test_cli_reports_json_without_writing_input_or_output_files(self):
        candidate = example()
        candidate["steps"][0]["duration"] = 7
        result = self.run_cli(json.dumps(example()).encode(), json.dumps(candidate).encode())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {
                "changes": [{"step": 1, "field": "duration", "before": 5, "after": 7}],
                "duration_before": 10,
                "duration_after": 12,
            },
        )

    def test_cli_invalid_input_has_no_success_output_or_traceback(self):
        for invalid in (b"not json", b"\xff", b"[" * 10000 + b"]" * 10000, b" " * (64 * 1024 + 1)):
            with self.subTest(invalid=invalid[:30]):
                result = self.run_cli(json.dumps(example()).encode(), invalid)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertIn("concept revision:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_cli_rejects_duplicate_fields_in_an_otherwise_valid_candidate(self):
        candidate = example()
        candidate["steps"][0]["duration"] = 7
        encoded = (
            json.dumps(candidate)
            .replace('"title": "Cache"', '"title": "Wrong", "title": "Cache"')
            .encode()
        )
        result = self.run_cli(json.dumps(example()).encode(), encoded)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Duplicate JSON field", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_cli_original_is_also_size_limited(self):
        original = json.dumps(example()).encode() + b" " * (64 * 1024)
        candidate = example()
        candidate["steps"][0]["duration"] = 7
        result = self.run_cli(original, json.dumps(candidate).encode())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("64 KiB", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_cli_io_failure_is_distinct_from_a_candidate_requiring_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / "missing.json")
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "concept_render.revision",
                    "--original",
                    missing,
                    "--candidate",
                    missing,
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=10,
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("concept revision unavailable:", result.stderr)


if __name__ == "__main__":
    unittest.main()
