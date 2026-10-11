"""Tests for app.render.aggregation (outcome tally + accurate terminal message).

Fixes the legacy false-success bug: run() emitted "All rendering tasks completed!"
purely on the cancellation flag, never reflecting per-job failures (render.py:53-54).
Run: python3 -m unittest tests.test_aggregation
"""

import unittest

from app.render.aggregation import JobOutcome, RenderSummary


class JobOutcomeTests(unittest.TestCase):
    def test_outcome_members(self):
        self.assertEqual(
            {o.name for o in JobOutcome},
            {"SUCCESS", "FAILED", "CANCELLED", "MANIM_NOT_FOUND"},
        )


class RenderSummaryTests(unittest.TestCase):
    def test_mixed_counts_message(self):
        summary = RenderSummary()
        summary.add(JobOutcome.SUCCESS)
        summary.add(JobOutcome.SUCCESS)
        summary.add(JobOutcome.FAILED)
        message = summary.terminal_message()
        self.assertIn("2 succeeded", message)
        self.assertIn("1 failed", message)
        self.assertEqual((summary.succeeded, summary.failed), (2, 1))

    def test_all_failed_does_not_claim_success(self):
        summary = RenderSummary()
        for _ in range(3):
            summary.add(JobOutcome.FAILED)
        message = summary.terminal_message().lower()
        self.assertNotIn("completed", message)
        self.assertNotIn("success", message)  # "succeeded" ok; "success" must not appear
        self.assertIn("3 failed", message)

    def test_manim_not_found_surfaced_distinctly(self):
        summary = RenderSummary()
        summary.add(JobOutcome.MANIM_NOT_FOUND)
        self.assertIn("manim not found", summary.terminal_message().lower())
        self.assertEqual(summary.manim_not_found, 1)

    def test_manim_not_found_leads_message_not_zero_counts(self):
        # BL-27: when nothing could start, lead with the cause, not "0 succeeded".
        summary = RenderSummary()
        summary.add(JobOutcome.MANIM_NOT_FOUND)
        message = summary.terminal_message()
        self.assertTrue(message.lower().startswith("manim not found"))
        self.assertNotIn("0 succeeded", message)

    def test_manim_not_found_mixed_prepends_cause(self):
        summary = RenderSummary()
        summary.add(JobOutcome.SUCCESS)
        summary.add(JobOutcome.MANIM_NOT_FOUND)
        message = summary.terminal_message()
        self.assertIn("manim not found", message.lower())
        self.assertIn("1 succeeded", message)

    def test_cancelled_flavored_message(self):
        summary = RenderSummary()
        summary.add(JobOutcome.SUCCESS)
        summary.add(JobOutcome.CANCELLED)
        message = summary.terminal_message()
        self.assertIn("Cancelled", message)
        self.assertEqual(summary.cancelled, 1)

    def test_empty_summary_zero_state(self):
        self.assertIn("No render", RenderSummary().terminal_message())

    def test_total_counts_all_kinds(self):
        summary = RenderSummary()
        for outcome in JobOutcome:
            summary.add(outcome)
        self.assertEqual(summary.total, 4)


if __name__ == "__main__":
    unittest.main()
