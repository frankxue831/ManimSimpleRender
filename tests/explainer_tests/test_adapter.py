"""Tests for explainer.render.adapter -- the verify -> codegen -> render orchestrator.

These run WITHOUT a real manim (the runner is faked), so they cover the seams the
review flagged as untested: resolution->quality mapping, the citation gate firing
BEFORE any render side effect, theme-derived quality, and child-only PYTHONPATH.
"""

import os
import unittest
from unittest.mock import patch

from app.render.aggregation import JobOutcome

from explainer.cite.verifier import CiteFailure
from explainer.render.adapter import (
    CitationGateError,
    REPO_ROOT,
    render_storyboard,
    resolution_to_quality,
)

EXAMPLE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "examples",
    "evidence-budget.story.yaml",
)


class ResolutionToQualityTests(unittest.TestCase):
    def test_known_resolutions(self):
        self.assertEqual(resolution_to_quality("480p15"), "-ql")
        self.assertEqual(resolution_to_quality("720p30"), "-qm")
        self.assertEqual(resolution_to_quality("1080p30"), "-qh")
        self.assertEqual(resolution_to_quality("1440p60"), "-qp")
        self.assertEqual(resolution_to_quality("2160p60"), "-qk")

    def test_unknown_defaults_to_medium(self):
        self.assertEqual(resolution_to_quality("weird"), "-qm")
        self.assertEqual(resolution_to_quality(""), "-qm")
        self.assertEqual(resolution_to_quality(None), "-qm")
        self.assertEqual(resolution_to_quality("\u00b2p30"), "-qm")  # isdigit-but-not-int


class _FakeResult:
    def __init__(self, outcome):
        self.outcome = outcome


class _FakeRunner:
    """Records what the adapter handed the runner, returns SUCCESS -- no manim."""

    last = {}

    def run_job(self, job, command, cancel_event=None, on_log=None, env=None):
        _FakeRunner.last = {
            "command": list(command),
            "parent_pythonpath": os.environ.get("PYTHONPATH"),
            "env": dict(env or {}),
        }
        return _FakeResult(JobOutcome.SUCCESS)


class _ExplodingRunner:
    def __init__(self, *a, **k):
        raise AssertionError("runner must NOT be constructed when the citation gate fails")


class _RaisingRunner:
    def run_job(self, job, command, cancel_event=None, on_log=None, env=None):
        raise RuntimeError("boom mid-render")


class AdapterOrchestrationTests(unittest.TestCase):
    @patch("explainer.render.adapter.RenderRunner", _ExplodingRunner)
    @patch("explainer.render.adapter.verify_storyboard")
    def test_citation_gate_fires_before_any_render(self, mock_verify):
        mock_verify.return_value = [CiteFailure("b", "evidence.py:1", ("24",))]
        prev = os.environ.get("PYTHONPATH")
        with self.assertRaises(CitationGateError):
            render_storyboard(EXAMPLE, "/tmp/should_not_render.mp4")
        # gate fired before the runner was built AND before PYTHONPATH was touched
        self.assertEqual(os.environ.get("PYTHONPATH"), prev)

    @patch("explainer.render.adapter.shutil.copyfile")
    @patch("explainer.render.adapter.glob.glob", return_value=["/tmp/x/ExplainerVideo.mp4"])
    @patch("explainer.render.adapter.RenderRunner", _FakeRunner)
    @patch("explainer.render.adapter.verify_storyboard", return_value=[])
    def test_quality_derives_from_theme_and_pythonpath_is_child_only(self, *_mocks):
        prev = os.environ.get("PYTHONPATH")
        os.environ.pop("PYTHONPATH", None)
        try:
            render_storyboard(EXAMPLE, "/tmp/out_explainer_test.mp4")  # quality omitted
            self.assertIn("-qh", _FakeRunner.last["command"])  # theme 1080p30 -> -qh
            self.assertIn(REPO_ROOT, _FakeRunner.last["env"]["PYTHONPATH"])  # repo on child path
            self.assertNotIn("PYTHONPATH", os.environ)  # restored to "unset"
        finally:
            if prev is not None:
                os.environ["PYTHONPATH"] = prev
            else:
                os.environ.pop("PYTHONPATH", None)

    @patch("explainer.render.adapter.shutil.copyfile")
    @patch("explainer.render.adapter.glob.glob", return_value=["/tmp/x/ExplainerVideo.mp4"])
    @patch("explainer.render.adapter.RenderRunner", _FakeRunner)
    @patch("explainer.render.adapter.verify_storyboard", return_value=[])
    def test_render_uses_child_only_pythonpath(self, *_mocks):
        with patch.dict(
            os.environ,
            {"PYTHONPATH": "parent-only", "ADAPTER_SENTINEL": "preserve-me"},
            clear=False,
        ):
            render_storyboard(EXAMPLE, "/tmp/out_explainer_env_test.mp4")

            self.assertEqual(os.environ["PYTHONPATH"], "parent-only")
            self.assertEqual(_FakeRunner.last["parent_pythonpath"], "parent-only")
            self.assertEqual(
                _FakeRunner.last["env"]["PYTHONPATH"],
                REPO_ROOT + os.pathsep + "parent-only",
            )
            self.assertEqual(_FakeRunner.last["env"]["ADAPTER_SENTINEL"], "preserve-me")

    @patch("explainer.render.adapter.shutil.rmtree")
    @patch("explainer.render.adapter.RenderRunner", _RaisingRunner)
    @patch("explainer.render.adapter.verify_storyboard", return_value=[])
    def test_workdir_cleaned_on_unexpected_error(self, _mock_verify, mock_rmtree):
        # ARCH-08: an unexpected error mid-render must clean the temp workdir
        # (RenderError still keeps it for debugging; this is a non-RenderError path).
        with self.assertRaises(RuntimeError):
            render_storyboard(EXAMPLE, "/tmp/out_arch08.mp4")
        self.assertTrue(mock_rmtree.called)


if __name__ == "__main__":
    unittest.main()
