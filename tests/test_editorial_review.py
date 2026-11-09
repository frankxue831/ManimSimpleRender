import json
from pathlib import Path
import tempfile
import unittest

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
