"""Tests for explainer.plan.critique -- the bounded quality-revision pass.

The pass must be strictly optional in effect: any failure (transport, token
budget, a revision that never parses) returns None and the caller keeps the
draft it already validated.
"""

import unittest

from explainer.plan.client import ClientError, FakeClient, MaxTokensExhausted
from explainer.plan.critique import (
    MAX_REVISIONS_LIMIT,
    build_revision_prompt,
    draft_plan_ir,
    revise_plan,
)
from explainer.plan.facts import FactSheet, FocusTarget
from explainer.plan.plan_ir import PlanError

SHEET = FactSheet(
    repo_root="/repo",
    commit="",
    dirty=False,
    topic="how the floor works",
    targets=(
        FocusTarget(
            path="a.py",
            start=1,
            end=2,
            text="grounding_floor = 0.4\nmultiplier = 0.5",
            language="python",
            kind="code",
        ),
    ),
    beat_budget=2,
)


def _payload(narration="A confirmed memory keeps a floor."):
    beat = {
        "id": "floor",
        "canvas_kind": "code",
        "focus_index": 0,
        "cite_start": 1,
        "cite_end": 1,
        "display": "grounding_floor = 0.4",
        "narration": narration,
        "caption": "",
        "caption_cite": None,
        "regions": [],
        "diagram": None,
    }
    second = dict(
        beat,
        id="mult",
        cite_start=2,
        cite_end=2,
        display="multiplier = 0.5",
        narration="A drifted one fades faster.",
    )
    return {"title": "T", "subtitle": "S", "beats": [beat, second]}


class _RaisingClient:
    def draft(self, system, prompt, schema):
        raise ClientError("boom")


class DraftPlanIrTests(unittest.TestCase):
    def test_returns_raw_and_parsed_plan(self):
        raw, plan = draft_plan_ir(FakeClient(_payload()), SHEET, "base", 1)
        self.assertEqual(raw, _payload())
        self.assertEqual(plan.beats[0].id, "floor")

    def test_exhausted_repairs_raise_the_pinned_message(self):
        bad = {"title": "", "subtitle": "", "beats": []}
        with self.assertRaises(PlanError) as caught:
            draft_plan_ir(FakeClient(bad), SHEET, "base", 1)
        self.assertIn("no usable plan after 2 attempt(s)", str(caught.exception))


class RevisePlanTests(unittest.TestCase):
    def test_a_valid_revision_is_returned(self):
        improved = _payload(narration="The floor is the whole argument.")
        result = revise_plan(FakeClient(improved), SHEET, _payload(), 1)
        self.assertIsNotNone(result)
        raw, plan = result
        self.assertIn("whole argument", plan.beats[0].narration)

    def test_a_revision_that_never_parses_returns_none(self):
        bad = {"title": "", "subtitle": "", "beats": []}
        client = FakeClient(bad)
        self.assertIsNone(revise_plan(client, SHEET, _payload(), 1))
        self.assertEqual(len(client.calls), 2)  # initial try + one repair

    def test_a_client_failure_returns_none(self):
        self.assertIsNone(revise_plan(_RaisingClient(), SHEET, _payload(), 1))

    def test_revision_prompt_carries_the_draft_and_the_fact_sheet(self):
        prompt = build_revision_prompt(SHEET, _payload())
        self.assertIn("DRAFT PLAN:", prompt)
        self.assertIn('"grounding_floor = 0.4"', prompt)
        self.assertIn("focus_index: 0", prompt)

    def test_limit_is_a_small_positive_cap(self):
        self.assertGreaterEqual(MAX_REVISIONS_LIMIT, 1)
        self.assertLessEqual(MAX_REVISIONS_LIMIT, 5)

    def test_max_tokens_exhaustion_during_revision_returns_none(self):
        # MaxTokensExhausted is a ClientError subclass: mid-revision it must
        # fall back like any client failure, never escape to the exit-6
        # path reserved for first-draft exhaustion.
        class _BudgetBoom:
            def draft(self, system, prompt, schema):
                raise MaxTokensExhausted("budget")

        self.assertIsNone(revise_plan(_BudgetBoom(), SHEET, _payload(), 1))


if __name__ == "__main__":
    unittest.main()
