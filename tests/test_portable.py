"""Portable archive contract, including hostile input and static inspection."""

import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch


class PortableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "scene.py").write_text("# ordinary trusted source\n")
        self.spec = {
            "version": 1,
            "name": "test-project",
            "manifest": "scene.manifest.json",
            "files": [
                {"path": "scene.py", "role": "source", "license": "owner-provided"},
                {"path": "scene.manifest.json", "role": "config", "license": "owner-provided"},
            ],
            "requirements": {
                "python_min": [3, 12],
                "python_max": [3, 14],
                "packages": {},
                "tools": [],
                "fonts": [],
            },
        }
        (self.root / "scene.manifest.json").write_text(
            json.dumps({"source": "scene.py", "timeline": "timeline.json", "dependencies": []})
        )
        (self.root / "timeline.json").write_text("{}")
        self.spec["files"].append(
            {"path": "timeline.json", "role": "config", "license": "owner-provided"}
        )
        self.spec_path = self.root / "project.json"
        self.archive = self.root / "project.zip"

    def export(self):
        from scene_production import portable

        self.spec_path.write_text(json.dumps(self.spec))
        return portable.export_project(self.spec_path, self.archive)

    def test_roundtrip_exports_only_selected_files(self):
        from scene_production import portable

        (self.root / "unselected.txt").write_text("not exported")
        self.export()
        report = portable.inspect_bundle(self.archive)
        self.assertEqual(len(report["files"]), 3)
        dest = self.root / "received"
        portable.unpack_bundle(self.archive, dest)
        self.assertEqual((dest / "scene.py").read_text(), "# ordinary trusted source\n")
        self.assertFalse((dest / "unselected.txt").exists())

    def test_cache_secret_and_symlink_inputs_refused(self):
        for relative in [".env", "__pycache__/x.pyc", "secret.pem"]:
            with self.subTest(relative=relative):
                self.spec["files"][0]["path"] = relative
                with self.assertRaises(ValueError):
                    self.export()
        self.spec["files"][0]["path"] = "link.py"
        (self.root / "link.py").symlink_to(self.root / "scene.py")
        with self.assertRaises(ValueError):
            self.export()

    def test_missing_selected_helper_refused(self):
        (self.root / "scene.manifest.json").write_text(
            json.dumps(
                {"source": "scene.py", "timeline": "timeline.json", "dependencies": ["helper.py"]}
            )
        )
        with self.assertRaisesRegex(ValueError, "helper.py"):
            self.export()

    def test_existing_destination_not_modified(self):
        from scene_production import portable

        self.export()
        dest = self.root / "received"
        dest.mkdir()
        (dest / "keep").write_text("keep")
        with self.assertRaises((ValueError, FileExistsError)):
            portable.unpack_bundle(self.archive, dest)
        self.assertEqual((dest / "keep").read_text(), "keep")

    def test_archive_traversal_extra_alias_and_links_refused(self):
        from scene_production import portable

        self.export()
        original = self.archive.read_bytes()
        for name, link in [
            ("../escaped", False),
            ("extra.txt", False),
            ("SCENE.py", False),
            ("link", True),
        ]:
            with self.subTest(name=name):
                self.archive.write_bytes(original)
                with zipfile.ZipFile(self.archive, "a") as archive:
                    entry = zipfile.ZipInfo(name)
                    if link:
                        entry.external_attr = 0o120777 << 16
                    archive.writestr(entry, "bad")
                with self.assertRaises(ValueError):
                    portable.unpack_bundle(self.archive, self.root / "reject")
                self.assertFalse((self.root / "reject").exists())

    def test_tamper_detected_and_inspection_never_executes_source(self):
        from scene_production import portable

        marker = self.root / "EXECUTED"
        code = "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('bad')\n"
        (self.root / "scene.py").write_text(code)
        self.export()
        portable.inspect_bundle(self.archive)
        self.assertFalse(marker.exists())
        with zipfile.ZipFile(self.archive) as archive:
            contents = {n: archive.read(n) for n in archive.namelist()}
        contents["scene.py"] = b"tampered"
        with zipfile.ZipFile(self.archive, "w") as archive:
            for name, value in contents.items():
                archive.writestr(name, value)
        with self.assertRaisesRegex(ValueError, "hash"):
            portable.inspect_bundle(self.archive)

    def valid_project(self):
        source = "# scene:start first\nvalue = 1\n# scene:end first\n"
        (self.root / "scene.py").write_text(source)
        entry = {
            "version": 1,
            "source": "scene.py",
            "timeline": "timeline.json",
            "scene": "Demo",
            "dependencies": [],
            "brief": {
                "audience": "developers",
                "goal": "test",
                "sources": ["owner"],
                "pitfalls": ["trusted code"],
            },
            "segments": [
                {"id": "first", "captions": ["first"], "objects": ["box"], "depends_on": []}
            ],
        }
        (self.root / "scene.manifest.json").write_text(json.dumps(entry))
        (self.root / "timeline.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "segments": [{"label": "first", "start": 0, "end": 1, "samples": [0.5]}],
                }
            )
        )

    def test_preflight_missing_requirements_are_actionable(self):
        from scene_production import portable

        self.valid_project()
        self.spec["requirements"].update(
            packages={"manim": "0.20.1"}, tools=["ffmpeg"], fonts=["Absent CJK"]
        )
        self.export()
        dest = self.root / "received"
        portable.unpack_bundle(self.archive, dest)
        env = {"python": [3, 13], "packages": {}, "tools": [], "fonts": [], "system": "test"}
        report = portable.preflight(dest, env)
        self.assertFalse(report["ready"])
        self.assertEqual({e["code"] for e in report["errors"]}, {"package", "tool", "font"})
        self.assertIn("Absent CJK", str(report["errors"]))

    def test_preflight_does_not_execute_project_and_detects_drift(self):
        from scene_production import portable

        self.valid_project()
        marker = self.root / "executed"
        source = self.root / "scene.py"
        source.write_text(
            "from pathlib import Path\nPath("
            + repr(str(marker))
            + ").write_text('bad')\n"
            + source.read_text()
        )
        self.export()
        dest = self.root / "received"
        portable.unpack_bundle(self.archive, dest)
        env = {"python": [3, 13], "packages": {}, "tools": [], "fonts": [], "system": "test"}
        self.assertTrue(portable.preflight(dest, env)["ready"])
        self.assertFalse(marker.exists())
        (dest / "scene.py").write_text("changed")
        self.assertFalse(portable.preflight(dest, env)["ready"])

    def test_private_key_payload_and_oversized_archive_refused(self):
        from scene_production import portable

        (self.root / "scene.py").write_text("-----BEGIN RSA PRIVATE KEY-----")
        with self.assertRaisesRegex(ValueError, "private-key"):
            self.export()
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("oversized.py", b"0" * (portable.MAX_FILE_BYTES + 1))
        with self.assertRaises(ValueError):
            portable.inspect_bundle(self.archive)

    def test_export_budget_rejects_before_reading_excess_payload(self):
        from scene_production import portable

        initial = sum((self.root / f["path"]).stat().st_size for f in self.spec["files"])
        for name in ["one.bin", "two.bin", "excess.bin"]:
            (self.root / name).write_bytes(b"x" * 64)
            self.spec["files"].append({"path": name, "role": "asset", "license": "owner"})
        original_open = Path.open

        def guarded_open(path, *args, **kwargs):
            if path.name == "excess.bin":
                raise AssertionError("export read beyond aggregate budget")
            return original_open(path, *args, **kwargs)

        with patch.object(portable, "MAX_TOTAL_BYTES", initial + 128):
            with patch.object(Path, "open", guarded_open):
                with self.assertRaisesRegex(ValueError, "64 MiB|aggregate"):
                    self.export()

    def test_preflight_rejects_malformed_provenance(self):
        from scene_production import portable

        self.valid_project()
        self.export()
        dest = self.root / "received"
        portable.unpack_bundle(self.archive, dest)
        original = json.loads((dest / "handoff.json").read_text())
        for field, value in [("producer", None), ("tool", [])]:
            data = dict(original)
            data[field] = value
            (dest / "handoff.json").write_text(json.dumps(data))
            report = portable.preflight(
                dest,
                {"python": [3, 13], "packages": {}, "tools": [], "fonts": [], "system": "test"},
            )
            self.assertFalse(report["ready"])
            self.assertIn("provenance", str(report["errors"]))

    def test_preflight_requires_all_declared_inputs_in_inventory(self):
        from scene_production import portable

        self.valid_project()
        (self.root / "helper.py").write_text("helper = 1\n")
        entry = json.loads((self.root / "scene.manifest.json").read_text())
        entry["dependencies"] = ["helper.py"]
        (self.root / "scene.manifest.json").write_text(json.dumps(entry))
        self.spec["files"].append({"path": "helper.py", "role": "source", "license": "owner"})
        self.export()
        for omitted in ["helper.py", "scene.py", "timeline.json"]:
            dest = self.root / omitted.replace(".", "-")
            portable.unpack_bundle(self.archive, dest)
            spec = json.loads((dest / "project.json").read_text())
            spec["files"] = [f for f in spec["files"] if f["path"] != omitted]
            handoff = json.loads((dest / "handoff.json").read_text())
            handoff["project"] = spec
            handoff["files"] = [f for f in handoff["files"] if f["path"] != omitted]
            (dest / "project.json").write_text(json.dumps(spec))
            (dest / "handoff.json").write_text(json.dumps(handoff))
            report = portable.preflight(
                dest,
                {"python": [3, 13], "packages": {}, "tools": [], "fonts": [], "system": "test"},
            )
            self.assertFalse(report["ready"], omitted)
            self.assertIn(omitted, str(report["errors"]))

    def test_selected_aggregate_archive_limit_is_strict(self):
        from scene_production import portable

        self.export()
        size = sum((self.root / f["path"]).stat().st_size for f in self.spec["files"])
        with patch.object(portable, "MAX_TOTAL_BYTES", size - 1):
            with self.assertRaisesRegex(ValueError, "aggregate"):
                portable.inspect_bundle(self.archive)

    def test_reserved_prefix_and_unicode_aliases_are_refused(self):
        from scene_production import portable

        (self.root / "handoff.json").mkdir()
        (self.root / "handoff.json/input.py").write_text("x=1")
        self.spec["files"].append(
            {"path": "handoff.json/input.py", "role": "source", "license": "owner"}
        )
        with self.assertRaises(ValueError):
            self.export()
        self.spec["files"].pop()
        self.export()
        with zipfile.ZipFile(self.archive, "a") as archive:
            archive.writestr("café.txt", "first")
            archive.writestr("cafe\u0301.txt", "second")
        with self.assertRaisesRegex(ValueError, "alias"):
            portable.inspect_bundle(self.archive)

    def test_project_json_is_read_with_a_bound(self):
        from scene_production import portable

        self.spec_path.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
        with patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded spec read")):
            with self.assertRaisesRegex(ValueError, "2 MiB"):
                portable.export_project(self.spec_path, self.archive)

    def test_invalid_role_is_clean_validation_error(self):
        self.spec["files"][0]["role"] = []
        with self.assertRaisesRegex(ValueError, "role"):
            self.export()

    def test_rebased_prefix_refused_before_zipfile(self):
        from scene_production import portable
        import struct

        self.export()
        original = self.archive.read_bytes()
        for prefix in (b"UNTRUSTED PREFIX\n", b"PK\x03\x04UNTRUSTED PREFIX\n"):
            raw = bytearray(original)
            end = len(raw) - 22
            directory = struct.unpack_from("<L", raw, end + 16)[0]
            count = struct.unpack_from("<H", raw, end + 10)[0]
            cursor = directory
            for _ in range(count):
                offset = struct.unpack_from("<L", raw, cursor + 42)[0]
                struct.pack_into("<L", raw, cursor + 42, offset + len(prefix))
                name, extra, comment = struct.unpack_from("<HHH", raw, cursor + 28)
                cursor += 46 + name + extra + comment
            struct.pack_into("<L", raw, end + 16, directory + len(prefix))
            self.archive.write_bytes(prefix + raw)
            with (
                self.subTest(prefix=prefix),
                patch.object(portable.zipfile, "ZipFile", side_effect=AssertionError("parsed")),
            ):
                with self.assertRaisesRegex(ValueError, "prefix|header"):
                    portable.inspect_bundle(self.archive)

    def test_legacy_recipe_inventory_remains_readable(self):
        from scene_production import portable

        self.export()
        with zipfile.ZipFile(self.archive) as z:
            payloads = {name: z.read(name) for name in z.namelist()}
        handoff = json.loads(payloads["handoff.json"])
        del handoff["commands"]["receiving_launcher"]
        handoff["commands"]["render"] = ["python", "-m", "scene_production", "--help"]
        payloads["handoff.json"] = json.dumps(handoff).encode()
        with zipfile.ZipFile(self.archive, "w") as z:
            for name, raw in payloads.items():
                z.writestr(name, raw)
        self.assertEqual(portable.inspect_bundle(self.archive)["version"], 1)

    def test_entry_budget_precedes_zipfile_directory_parsing(self):
        from scene_production import portable

        with zipfile.ZipFile(self.archive, "w") as z:
            for n in range(1024):
                z.writestr(str(n), b"")
        with patch.object(portable.zipfile, "ZipFile", side_effect=AssertionError("parsed")):
            with self.assertRaisesRegex(ValueError, "budget"):
                portable.inspect_bundle(self.archive)

    def test_false_small_entry_count_does_not_bypass_preparse_budget(self):
        from scene_production import portable
        import struct

        with zipfile.ZipFile(self.archive, "w") as z:
            for n in range(1024):
                z.writestr(str(n), b"")
        raw = bytearray(self.archive.read_bytes())
        end = raw.rindex(b"PK\x05\x06")
        struct.pack_into("<HH", raw, end + 8, 1, 1)
        self.archive.write_bytes(raw)
        with patch.object(portable.zipfile, "ZipFile", side_effect=AssertionError("parsed")):
            with self.assertRaisesRegex(ValueError, "budget|directory"):
                portable.inspect_bundle(self.archive)

    def test_container_size_budget_precedes_zipfile_open(self):
        from scene_production import portable

        with self.archive.open("wb") as f:
            f.truncate(72 * 1024 * 1024 + 1)
        with patch.object(portable.zipfile, "ZipFile", side_effect=AssertionError("parsed")):
            with self.assertRaisesRegex(ValueError, "budget"):
                portable.inspect_bundle(self.archive)

    def test_central_directory_size_budget_precedes_zipfile_open(self):
        from scene_production import portable
        import struct

        self.export()
        raw = bytearray(self.archive.read_bytes())
        end = raw.rindex(b"PK\x05\x06")
        struct.pack_into("<L", raw, end + 12, 2 * 1024 * 1024)
        self.archive.write_bytes(raw)
        with patch.object(portable.zipfile, "ZipFile", side_effect=AssertionError("parsed")):
            with self.assertRaisesRegex(ValueError, "budget"):
                portable.inspect_bundle(self.archive)

    def test_ambiguous_end_record_in_comment_refused_before_zipfile(self):
        from scene_production import portable
        import struct

        self.export()
        raw = bytearray(self.archive.read_bytes())
        end = raw.rindex(b"PK\x05\x06")
        fake = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 1024, 1024, 0, 0, 0)
        comment = fake + b"trailing comment"
        struct.pack_into("<H", raw, end + 20, len(comment))
        self.archive.write_bytes(raw + comment)
        with patch.object(portable.zipfile, "ZipFile", side_effect=AssertionError("parsed")):
            with self.assertRaisesRegex(ValueError, "end|uncommented"):
                portable.inspect_bundle(self.archive)

    def test_zip64_locator_cannot_override_prevalidated_end_record(self):
        from scene_production import portable
        import struct

        self.export()
        with zipfile.ZipFile(self.archive) as z:
            selected = [(i, z.read(i)) for i in z.infolist()]
        locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, 0, 1)
        selected[-1][0].comment = locator
        with zipfile.ZipFile(self.archive, "w") as z:
            for info, raw in selected:
                z.writestr(info, raw)
        self.assertEqual(self.archive.read_bytes()[-42:-38], b"PK\x06\x07")
        with patch.object(portable.zipfile, "ZipFile", side_effect=AssertionError("parsed")):
            with self.assertRaisesRegex(ValueError, "ZIP64"):
                portable.inspect_bundle(self.archive)


if __name__ == "__main__":
    unittest.main()
