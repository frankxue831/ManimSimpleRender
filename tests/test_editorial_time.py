"""Timestamp compatibility tests use hand-built legacy records, never migration."""

import contextlib
import io
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from scene_production import editorial


class EditorialTimeTests(unittest.TestCase):
    timestamp = "2026-11-11T12:34:56.789Z"

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

    def legacy(self):
        return {
            "schema_version": 1,
            "video": editorial._entry(self.video),
            "assessment": {k: v for k, v in self.assessment.items() if k != "evidence"},
            "evidence": [editorial._entry(self.frame)],
            "previous": None,
        }

    def save(self, data):
        path = self.root / "prior.json"
        path.write_text(json.dumps(data))
        return path

    def test_frozen_clock_and_exact_keys(self):
        target = self.root / "out.json"
        with patch.object(editorial, "_prepared_at", return_value=self.timestamp) as clock:
            result = editorial.record_review(self.video, self.assessment, target)
        clock.assert_called_once_with()
        self.assertEqual(set(result), set(self.legacy()) | {"record_prepared_at"})
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["record_prepared_at"], self.timestamp)
        self.assertEqual(json.loads(target.read_text()), result)
        self.assertEqual(
            editorial.compare(target, self.video)["record_prepared_at"], self.timestamp
        )

    def test_mixed_links_and_clock_order(self):
        for version in (1, 2):
            for timestamp in (self.timestamp, "2000-01-01T00:00:00.000Z"):
                data = self.legacy()
                if version == 2:
                    data.update(schema_version=2, record_prepared_at=self.timestamp)
                prior = self.save(data)
                raw = prior.read_bytes()
                self.assertEqual(
                    editorial.compare(prior, self.video)["record_prepared_at"],
                    self.timestamp if version == 2 else None,
                )
                target = self.root / f"out-{version}-{timestamp[:4]}.json"
                with patch.object(editorial, "_prepared_at", return_value=timestamp):
                    result = editorial.record_review(
                        self.video, self.assessment, target, prior, ["F1"]
                    )
                self.assertEqual(prior.read_bytes(), raw)
                self.assertEqual(result["previous"]["review"]["sha256"], editorial.digest(prior))
                comparison = editorial.compare(target, self.video)
                self.assertEqual(comparison["readiness"], "not_determined")
                self.assertNotIn("duration", comparison)

    def test_mixed_version_addresses_and_finding_free_links(self):
        for version in (1, 2):
            data = self.legacy()
            data["assessment"]["findings"] = [
                dict(self.assessment["findings"][0], id=identifier) for identifier in ("F1", "F2")
            ]
            if version == 2:
                data.update(schema_version=2, record_prepared_at=self.timestamp)
            prior = self.save(data)
            raw = prior.read_bytes()
            target = self.root / f"multiple-{version}.json"
            result = editorial.record_review(
                self.video, self.assessment, target, prior, ["F1", "F2"]
            )
            self.assertEqual(result["previous"]["addressed_findings"], ["F1", "F2"])
            self.assertEqual(prior.read_bytes(), raw)
            for addresses in (["F1", "F1"], ["unknown"]):
                rejected = self.root / "rejected.json"
                with self.assertRaises(ValueError):
                    editorial.record_review(self.video, self.assessment, rejected, prior, addresses)
                self.assertFalse(rejected.exists())
                self.assertFalse(list(self.root.glob(".editorial-*")))
            data["assessment"].update(decision="approved", findings=[])
            prior = self.save(data)
            raw = prior.read_bytes()
            target = self.root / f"finding-free-{version}.json"
            result = editorial.record_review(self.video, self.assessment, target, prior, [])
            self.assertEqual(result["previous"]["addressed_findings"], [])
            self.assertEqual(result["previous"]["review"]["sha256"], editorial.digest(prior))
            self.assertEqual(prior.read_bytes(), raw)

    def test_invalid_timestamps_and_versions(self):
        invalid = [
            None,
            1,
            "",
            "2026-11-11",
            "2026-11-11T12:34:56.789+00:00",
            "2026-11-11T12:34:56Z",
            "2026-11-11T12:34:56.7890Z",
            "2026-02-30T12:34:56.789Z",
            "2026-11-11T12:34:60.789Z",
        ]
        for value in invalid:
            with self.subTest(value=value):
                data = dict(self.legacy(), schema_version=2, record_prepared_at=value)
                with self.assertRaises(ValueError):
                    editorial._load(self.save(data))
                with patch.object(editorial, "_prepared_at", return_value=value):
                    with self.assertRaises(ValueError):
                        editorial.record_review(self.video, self.assessment, self.root / "out.json")
                self.assertFalse(list(self.root.glob(".editorial-*")))
                self.assertFalse((self.root / "out.json").exists())
        for version in (True, 0, 3):
            with self.assertRaises(ValueError):
                editorial._load(self.save(dict(self.legacy(), schema_version=version)))
        with self.assertRaises(ValueError):
            editorial._load(self.save(dict(self.legacy(), schema_version=2)))

    def test_strict_legacy_and_finding_keys(self):
        with self.assertRaises(ValueError):
            editorial._load(self.save(dict(self.legacy(), record_prepared_at=self.timestamp)))
        for version in (1, 2):
            data = self.legacy()
            data["assessment"]["findings"] = [
                dict(self.assessment["findings"][0], category="layout")
            ]
            if version == 2:
                data.update(schema_version=2, record_prepared_at=self.timestamp)
            with self.assertRaises(ValueError):
                editorial._load(self.save(data))
        for key in ("record_prepared_at", "category"):
            with self.assertRaises(ValueError):
                editorial.record_review(
                    self.video,
                    dict(self.assessment, **{key: self.timestamp}),
                    self.root / "out.json",
                )

    def test_duplicate_keys_and_extra_keys(self):
        for version in (1, 2):
            data = self.legacy()
            if version == 2:
                data.update(schema_version=2, record_prepared_at=self.timestamp)
            path = self.save(data)
            path.write_text(
                path.read_text().replace(
                    '"schema_version":', '"schema_version": 1, "schema_version":', 1
                )
            )
            with self.assertRaises(ValueError):
                editorial._load(path)
            with self.assertRaises(ValueError):
                editorial._load(self.save(dict(data, extra=True)))

    def test_cli_clock_failure_has_no_success_json(self):
        assessment = self.root / "assessment.json"
        assessment.write_text(json.dumps(self.assessment))
        output = self.root / "out.json"
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(editorial, "datetime") as clock:
            clock.now.side_effect = OSError("clock failed")
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as raised:
                    editorial.main(
                        [
                            "record",
                            "--video",
                            str(self.video),
                            "--assessment",
                            str(assessment),
                            "--output",
                            str(output),
                        ]
                    )
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("editorial review failed", stderr.getvalue())
        self.assertFalse(output.exists())

    def test_clock_failure_before_temporary_output(self):
        with patch.object(editorial, "datetime") as clock:
            clock.now.side_effect = RuntimeError("clock failed")
            with self.assertRaises(ValueError):
                editorial.record_review(self.video, self.assessment, self.root / "out.json")
        self.assertFalse((self.root / "out.json").exists())
        self.assertFalse(list(self.root.glob(".editorial-*")))


if __name__ == "__main__":
    unittest.main()
