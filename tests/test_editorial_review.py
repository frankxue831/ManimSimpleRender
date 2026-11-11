import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import mock_open, patch

from scene_production import editorial


class EditorialReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root / "delivered.mp4"
        self.video.write_bytes(b"original audiovisual bytes")
        self.frame = self.root / "frame.png"
        self.frame.write_bytes(b"frame")
        self.assessment = {
            "reviewer": "reviewer agent",
            "scope": "Three sampled frames; audio not heard",
            "decision": "changes_requested",
            "findings": [
                {
                    "id": "F1",
                    "observed": "The arrow appears without a cause.",
                    "acceptance_question": "Can the viewer see what causes the arrow?",
                    "section": "lookup",
                    "time_seconds": 1.5,
                }
            ],
            "evidence": [str(self.frame)],
        }

    def test_candidate_requires_fresh_assessment_and_can_reference_finding(self):
        first = self.root / "review-1.json"
        editorial.record_review(self.video, self.assessment, first)
        same = editorial.compare(first, self.video)
        self.assertTrue(same["assessment_current"])
        candidate = self.root / "candidate.mp4"
        candidate.write_bytes(b"changed audiovisual bytes")
        result = editorial.compare(first, candidate)
        self.assertFalse(result["assessment_current"])
        self.assertTrue(result["candidate_requires_fresh_assessment"])
        self.assertEqual(result["scope"], "Three sampled frames; audio not heard")
        self.assertEqual(result["readiness"], "not_determined")
        self.assertEqual(result["findings"][0]["id"], "F1")
        second = self.root / "review-2.json"
        assessment = {
            "reviewer": "second reviewer",
            "scope": "Candidate frames only",
            "decision": "approved",
            "findings": [],
            "evidence": [],
        }
        editorial.record_review(candidate, assessment, second, previous=first, addresses=["F1"])
        saved = json.loads(second.read_text())
        self.assertEqual(saved["previous"]["addressed_findings"], ["F1"])
        self.assertEqual(saved["previous"]["review"]["sha256"], editorial.digest(first))
        result = editorial.compare(second, candidate)
        self.assertTrue(result["assessment_current"])
        self.assertEqual(result["previous"]["addressed_findings"], ["F1"])
        self.assertEqual(result["previous"]["review"]["sha256"], editorial.digest(first))
        self.assertTrue(first.exists())

    def test_replaced_audio_or_video_bytes_invalidates_assessment(self):
        review = self.root / "review.json"
        editorial.record_review(self.video, self.assessment, review)
        self.video.write_bytes(b"changed audio alone")
        result = editorial.compare(review, self.video)
        self.assertFalse(result["assessment_current"])
        self.assertFalse(result["reviewed_video_available_and_unchanged"])

    def test_invalid_or_unknown_findings_are_rejected(self):
        review = self.root / "review.json"
        broken = dict(self.assessment, findings=[{"id": "F1", "observed": "unclear"}])
        with self.assertRaises(ValueError):
            editorial.record_review(self.video, broken, review)
        self.assertFalse(review.exists())
        editorial.record_review(self.video, self.assessment, review)
        with self.assertRaises(ValueError):
            editorial.record_review(
                self.video,
                self.assessment,
                self.root / "new.json",
                previous=review,
                addresses=["unknown"],
            )

    def test_existing_review_is_never_overwritten_and_evidence_drift_is_visible(self):
        review = self.root / "review.json"
        editorial.record_review(self.video, self.assessment, review)
        before = review.read_bytes()
        with self.assertRaises(FileExistsError):
            editorial.record_review(self.video, self.assessment, review)
        self.assertEqual(review.read_bytes(), before)
        self.frame.write_bytes(b"changed")
        self.assertFalse(editorial.compare(review, self.video)["evidence"][0]["unchanged"])

    def test_cleanup_failure_after_publication_reports_success_with_warning(self):
        output = self.root / "review.json"
        with patch("os.unlink", side_effect=PermissionError("cleanup denied")):
            result = editorial.record_review(self.video, self.assessment, output)
        self.assertTrue(output.is_file())
        self.assertTrue(editorial.compare(output, self.video)["assessment_current"])
        self.assertEqual(result["publication_warning"]["code"], "temporary_cleanup_failed")
        self.assertTrue(Path(result["publication_warning"]["temporary_path"]).exists())

    def test_cleanup_failure_does_not_hide_publication_error(self):
        output = self.root / "review.json"
        with patch("os.link", side_effect=OSError("publication failed")):
            with patch("os.unlink", side_effect=PermissionError("cleanup denied")):
                with self.assertRaisesRegex(OSError, "publication failed") as error:
                    editorial.record_review(self.video, self.assessment, output)
        self.assertFalse(output.exists())
        self.assertTrue(any("cleanup" in note for note in error.exception.__notes__))

    def test_publication_failure_leaves_no_review_or_temporary_files(self):
        output = self.root / "review.json"
        before = set(self.root.iterdir())
        with patch("os.link", side_effect=OSError("simulated failure")):
            with self.assertRaises(OSError):
                editorial.record_review(self.video, self.assessment, output)
        self.assertEqual(set(self.root.iterdir()), before)

    def test_invalid_unicode_does_not_create_a_partial_review(self):
        output = self.root / "invalid-unicode.json"
        with self.assertRaises(UnicodeEncodeError):
            editorial.record_review(self.video, dict(self.assessment, reviewer="\ud800"), output)
        self.assertFalse(output.exists())

    def test_oversized_review_is_rejected_before_creating_output(self):
        text = "😀" * 4000
        findings = [
            {"id": f"F{i}", "observed": text, "acceptance_question": text, "section": text}
            for i in range(100)
        ]
        output = self.root / "oversized.json"
        with self.assertRaisesRegex(ValueError, "exceeds 4 MiB"):
            editorial.record_review(self.video, dict(self.assessment, findings=findings), output)
        self.assertFalse(output.exists())

    def test_unrepresentable_time_is_rejected(self):
        finding = dict(self.assessment["findings"][0], time_seconds=10**1000)
        with self.assertRaises(ValueError):
            editorial.record_review(
                self.video, dict(self.assessment, findings=[finding]), self.root / "bad.json"
            )

    def test_approved_review_cannot_include_unresolved_findings(self):
        assessment = dict(self.assessment, decision="approved")
        with self.assertRaises(ValueError):
            editorial.record_review(self.video, assessment, self.root / "review.json")

    def test_candidate_can_link_a_prior_review_with_no_findings(self):
        for decision in ("approved", "needs_review"):
            with self.subTest(decision=decision):
                prior = self.root / f"{decision}-prior.json"
                candidate = self.root / f"{decision}-candidate.json"
                assessment = dict(self.assessment, decision=decision, findings=[])
                editorial.record_review(self.video, assessment, prior)
                editorial.record_review(self.video, assessment, candidate, previous=prior)
                result = editorial.compare(candidate, self.video)
                self.assertEqual(result["previous"]["addressed_findings"], [])
                self.assertEqual(result["previous"]["review"]["sha256"], editorial.digest(prior))
                self.assertTrue(result["previous_review_unchanged"])

    def test_symlink_loop_is_reported_as_changed(self):
        review = self.root / "review.json"
        editorial.record_review(self.video, self.assessment, review)
        self.frame.unlink()
        try:
            self.frame.symlink_to(self.frame)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        self.assertFalse(editorial.compare(review, self.video)["evidence"][0]["unchanged"])

    def test_record_and_cli_handle_unresolvable_artifact(self):
        review = self.root / "review.json"
        editorial.record_review(self.video, self.assessment, review)
        candidate = self.root / "loop.mp4"
        try:
            candidate.symlink_to(candidate)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        with self.assertRaises((OSError, ValueError)):
            editorial.record_review(candidate, self.assessment, self.root / "bad.json")
        with patch.object(Path, "resolve", side_effect=RuntimeError("symlink loop")):
            with self.assertRaisesRegex(ValueError, "symlink loop"):
                editorial.record_review(self.video, self.assessment, self.root / "bad.json")
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            with self.assertRaises(SystemExit) as exit_code:
                editorial.main(["compare", "--review", str(review), "--candidate", str(candidate)])
        self.assertEqual(exit_code.exception.code, 2)
        self.assertIn("editorial review failed:", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_symlink_loops_mark_reviewed_video_and_previous_as_changed(self):
        first, second = self.root / "first.json", self.root / "second.json"
        editorial.record_review(self.video, self.assessment, first)
        editorial.record_review(self.video, self.assessment, second, previous=first)
        candidate = self.root / "candidate.mp4"
        candidate.write_bytes(self.video.read_bytes())
        for path in (self.video, first):
            path.unlink()
            try:
                path.symlink_to(path)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
        result = editorial.compare(second, candidate)
        self.assertFalse(result["reviewed_video_available_and_unchanged"])
        self.assertFalse(result["previous_review_unchanged"])

    def test_load_rejects_noncanonical_assessment_and_excess_evidence(self):
        review = self.root / "review.json"
        editorial.record_review(self.video, self.assessment, review)
        valid = json.loads(review.read_text())
        cases = (
            ("array of pairs", list(valid["assessment"].items())),
            ("null", None),
            ("scalar", 42),
            ("nested evidence", dict(valid["assessment"], evidence=[])),
        )
        for label, assessment in cases:
            with self.subTest(case=label):
                malformed = dict(valid, assessment=assessment)
                review.write_text(json.dumps(malformed))
                with self.assertRaises(ValueError):
                    editorial.compare(review, self.video)
        malformed = dict(valid, evidence=valid["evidence"] * 101)
        review.write_text(json.dumps(malformed))
        with self.assertRaises(ValueError):
            editorial.compare(review, self.video)
        review.write_text(json.dumps(dict(valid, evidence=valid["evidence"] * 100)))
        self.assertEqual(len(editorial.compare(review, self.video)["evidence"]), 100)

    def test_load_caps_read_and_accepts_exact_limit(self):
        review = self.root / "review.json"
        editorial.record_review(self.video, self.assessment, review)
        raw = review.read_bytes()
        limit = 4 * 1024 * 1024
        review.write_bytes(raw + b" " * (limit - len(raw)))
        self.assertTrue(editorial.compare(review, self.video)["assessment_current"])
        review.write_bytes(raw + b" " * (limit + 1 - len(raw)))
        with self.assertRaisesRegex(ValueError, "exceeds 4 MiB"):
            editorial.compare(review, self.video)
        opener = mock_open(read_data=b"x" * (limit + 1))
        with patch.object(Path, "open", opener):
            with self.assertRaisesRegex(ValueError, "exceeds 4 MiB"):
                editorial._load(review)
        opener().read.assert_called_once_with(limit + 1)

    def test_changed_prior_review_is_detected(self):
        first, second = self.root / "first.json", self.root / "second.json"
        editorial.record_review(self.video, self.assessment, first)
        editorial.record_review(
            self.video, self.assessment, second, previous=first, addresses=["F1"]
        )
        first.write_text("{}")
        self.assertFalse(editorial.compare(second, self.video)["previous_review_unchanged"])


if __name__ == "__main__":
    unittest.main()
