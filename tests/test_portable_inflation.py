"""Output limits apply to actual inflation, including spoofed ZIP size claims."""

import io
from pathlib import Path
import tempfile
import struct
import unittest
import zipfile
import zlib
from unittest.mock import patch

from scene_production import portable


class InflationTests(unittest.TestCase):
    def archive(self, payload, method=zipfile.ZIP_DEFLATED):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=method) as archive:
            archive.writestr("payload", payload)
        buf.seek(0)
        return buf

    def test_public_inspector_rejects_spoofed_inventory(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("handoff.json", b"{}" + b" " * 1000000)
            archive.writestr("project.json", b"{}")
        raw = bytearray(buf.getvalue())
        central = raw.index(b"PK\x01\x02")
        struct.pack_into("<L", raw, central + 24, 2)
        struct.pack_into("<L", raw, 22, 2)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.zip"
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, "exceeds"):
                portable.inspect_bundle(path)
            dest = Path(directory) / "unpacked"
            with self.assertRaisesRegex(ValueError, "exceeds"):
                portable.unpack_bundle(path, dest)
            self.assertFalse(dest.exists())

    def test_supported_methods_roundtrip(self):
        for method in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            for payload in (b"", b"hello", b"x" * 150000):
                with self.subTest(method=method, size=len(payload)):
                    with zipfile.ZipFile(self.archive(payload, method)) as archive:
                        self.assertEqual(portable._entry_bytes(archive, "payload", 200000), payload)

    def test_forged_small_size_never_inflates_unbounded_output(self):
        buf = self.archive(b"{}" + b" " * 1000000)
        raw = bytearray(buf.getvalue())
        central = raw.index(b"PK\x01\x02")
        struct.pack_into("<L", raw, central + 24, 2)
        struct.pack_into("<L", raw, 22, 2)
        decoder = zlib.decompressobj(-zlib.MAX_WBITS)
        calls = []

        class BoundedDecoder:
            def decompress(self, data, max_length):
                calls.append(max_length)
                result = decoder.decompress(data, max_length)
                assert len(result) <= max_length
                return result

            def __getattr__(self, name):
                return getattr(decoder, name)

        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            with patch.object(portable.zlib, "decompressobj", return_value=BoundedDecoder()):
                with self.assertRaisesRegex(ValueError, "exceeds"):
                    portable._entry_bytes(archive, "payload", 2 * 1024 * 1024)
        self.assertEqual(calls, [3])

    def test_unsupported_codecs_refused_before_decompression(self):
        for method in (zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA):
            with self.subTest(method=method):
                with zipfile.ZipFile(self.archive(b"x" * 10000, method)) as archive:
                    with patch.object(archive, "open", side_effect=AssertionError("opened")):
                        with self.assertRaisesRegex(ValueError, "only stored or DEFLATE"):
                            portable._entry_bytes(archive, "payload", 100000)

    def test_actual_output_budget_and_crc(self):
        with zipfile.ZipFile(self.archive(b"hello")) as archive:
            with self.assertRaisesRegex(ValueError, "exceeds"):
                portable._entry_bytes(archive, "payload", 3)
        with zipfile.ZipFile(self.archive(b"hello")) as archive:
            archive.getinfo("payload").CRC = 0
            with self.assertRaisesRegex(ValueError, "CRC"):
                portable._entry_bytes(archive, "payload", 10)

    def test_truncated_deflate_stream_rejected(self):
        with zipfile.ZipFile(self.archive(b"hello" * 100)) as archive:
            archive.getinfo("payload").compress_size -= 1
            with self.assertRaisesRegex(ValueError, "incomplete"):
                portable._entry_bytes(archive, "payload", 1000)
