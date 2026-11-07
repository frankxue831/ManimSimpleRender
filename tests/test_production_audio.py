"""Local WAV snapshot and stream-copy preview contracts, without Qt or TTS."""

import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

from scene_production import audio, digest


def wav(path, *, samples=8000, rate=16000, channels=1, bits=16):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(bits // 8)
        stream.setframerate(rate)
        stream.writeframes(b"\x01\x00" * (samples * channels * bits // 16))
    return path


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="audio 100% ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / "run"
        self.run.mkdir()
        self.video = self.run / "video.mp4"
        self.video.write_bytes(b"test video")
        self.receipt = self.run / "result.json"
        self.receipt.write_text("{}")
        sidecar = self.root / "captions"
        sidecar.mkdir()
        self.caption_path = sidecar / "manifest.json"
        self.caption_path.write_text("{}")
        self.captions = dict(
            manifest_path=str(self.caption_path),
            origin_receipt=audio._entry(self.receipt),
            video=audio._entry(self.video),
            fps=30,
            duration_frames=60,
            width=16,
            height=16,
            cues=[
                dict(id="one", text="First", start_frame=0, end_frame=30),
                dict(id="two", text="Second", start_frame=30, end_frame=60),
            ],
        )
        self.mapping = {
            "one": wav(self.root / "one %05d.wav"),
            "two": wav(self.root / "two ' ; .wav"),
        }
        self.info = dict(
            codec_name="h264",
            width=16,
            height=16,
            pix_fmt="yuv420p",
            fps="30",
            frames=60,
            duration_exact="2",
            packet_sha256="a" * 64,
        )
        self.sidecar_mock = patch.object(audio, "verify_sidecar", return_value=self.captions)
        self.sidecar_mock.start()
        self.addCleanup(self.sidecar_mock.stop)
        self.media_mock = patch.object(audio, "_video_info", return_value=self.info)
        self.probe = self.media_mock.start()
        self.addCleanup(self.media_mock.stop)

    def bind(self, output="audio", mapping=None, **kwargs):
        return audio.bind_audio(
            self.caption_path,
            self.mapping if mapping is None else mapping,
            self.root / output,
            **kwargs,
        )

    def test_complete_mapping_freezes_bytes_without_changing_inputs(self):
        before = {p: digest(p) for p in [self.video, self.caption_path, *self.mapping.values()]}
        result = self.bind()
        self.assertEqual(result["status"], "checked_requires_listening")
        self.assertFalse(result["semantic_acceptance"])
        self.assertFalse(result["listened"])
        self.assertFalse(result["revision_eligible"])
        self.assertEqual(result["cues"][1]["offset_samples"], 16000)
        self.assertEqual(before, {p: digest(p) for p in before})
        self.assertEqual(audio.verify_audio(result["manifest_path"]), result)
        for row, source in zip(result["cues"], self.mapping.values()):
            self.assertNotEqual(Path(row["wav"]["path"]), source)
            self.assertEqual(row["wav"]["sha256"], digest(source))

    def test_original_wav_can_change_after_snapshot(self):
        result = self.bind()
        self.mapping["one"].write_bytes(b"edited by author later")
        self.assertEqual(audio.verify_audio(result["manifest_path"]), result)

    def test_mapping_missing_extra_or_nonmapping_rejected(self):
        for mapping in ({}, {"one": self.mapping["one"]}, dict(self.mapping, other="unused"), []):
            with self.subTest(mapping=mapping), self.assertRaisesRegex(ValueError, "every cue"):
                self.bind(mapping=mapping)
        self.assertFalse((self.root / "audio").exists())

    def test_overlong_wav_rejected(self):
        wav(self.mapping["one"], samples=16001)
        with self.assertRaisesRegex(ValueError, "exceeds its caption"):
            self.bind()

    def test_exact_window_is_allowed(self):
        wav(self.mapping["one"], samples=16000)
        self.bind()

    def test_audio_layouts_must_match(self):
        for fields in ({"channels": 2}, {"rate": 8000}, {"bits": 32}):
            wav(self.mapping["two"], **fields)
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, "same channels"):
                self.bind()

    def test_sample_grid_must_be_exact(self):
        self.captions["cues"][0]["start_frame"] = 1
        with self.assertRaisesRegex(ValueError, "sample grid"):
            self.bind()

    def test_overlapping_cues_rejected(self):
        self.captions["cues"][1]["start_frame"] = 29
        with self.assertRaisesRegex(ValueError, "nonoverlapping"):
            self.bind()

    def test_truncated_wav_rejected(self):
        path = self.mapping["one"]
        path.write_bytes(path.read_bytes()[:-2])
        with self.assertRaisesRegex(ValueError, "declared size"):
            self.bind()

    def test_invalid_pcm_declarations_rejected(self):
        for offset, value, message in (
            (20, 3, "integer PCM"),
            (22, 3, "integer PCM"),
            (32, 4, "block alignment"),
            (34, 8, "integer PCM"),
        ):
            path = wav(self.mapping["one"])
            data = bytearray(path.read_bytes())
            struct.pack_into("<H", data, offset, value)
            path.write_bytes(data)
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, message):
                self.bind()

    def test_empty_and_non_riff_rejected(self):
        wav(self.mapping["one"], samples=0)
        with self.assertRaisesRegex(ValueError, "nonempty"):
            self.bind()
        self.mapping["one"].write_bytes(b"RF64" + b"0" * 60)
        with self.assertRaisesRegex(ValueError, "RIFF"):
            self.bind()

    def test_frozen_wav_tampering_rejected(self):
        result = self.bind()
        wav(Path(result["cues"][0]["wav"]["path"]), samples=9000)
        with self.assertRaisesRegex(ValueError, "metadata or SHA-256"):
            audio.verify_audio(result["manifest_path"])

    def test_caption_manifest_tampering_rejected(self):
        result = self.bind()
        self.caption_path.write_text('{"changed":true}')
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            audio.verify_audio(result["manifest_path"])

    def test_manifest_types_and_unknown_fields_are_rejected(self):
        result = self.bind()
        path = Path(result["manifest_path"])
        original = path.read_text()
        for change in ({"fps": 30.0}, {"schema_version": True}, {"unknown": 1}):
            value = json.loads(original)
            value.update(change)
            path.write_text(json.dumps(value))
            with self.subTest(change=change), self.assertRaises(ValueError):
                audio.verify_audio(path)

    def test_verify_video_info_does_not_invoke_media_tools(self):
        result = self.bind()
        self.probe.reset_mock()
        with patch.object(audio, "_tool", side_effect=AssertionError("no media tools")):
            self.assertEqual(audio.verify_audio(result["manifest_path"]), result)
        self.probe.assert_not_called()

    def assert_bad_video_info(self, changes):
        result = self.bind()
        path = Path(result["manifest_path"])
        original = path.read_text()
        self.probe.reset_mock()
        with patch.object(audio, "_tool", side_effect=AssertionError("no media tools")):
            for change in changes:
                value = json.loads(original)
                value["video_info"].update(change)
                path.write_text(json.dumps(value))
                with self.subTest(change=change), self.assertRaises(ValueError):
                    audio.verify_audio(path)
        self.probe.assert_not_called()

    def test_video_info_schema_rejects_missing_extra_and_wrong_container(self):
        result = self.bind()
        path = Path(result["manifest_path"])
        original = path.read_text()
        for info in (
            None,
            [],
            {},
            dict(self.info, extra=1),
            {key: value for key, value in self.info.items() if key != "packet_sha256"},
        ):
            value = json.loads(original)
            value["video_info"] = info
            path.write_text(json.dumps(value))
            with self.subTest(info=info), self.assertRaises(ValueError):
                audio.verify_audio(path)

    def test_video_info_rejects_boolean_and_wrong_numeric_types(self):
        self.assert_bad_video_info(
            [
                {"width": True},
                {"height": False},
                {"frames": True},
                {"width": 16.0},
                {"height": "16"},
                {"frames": 60.0},
                {"fps": 30},
                {"fps": True},
                {"duration_exact": 2},
                {"duration_exact": False},
            ]
        )

    def test_video_info_requires_positive_geometry_and_nonempty_format(self):
        self.assert_bad_video_info(
            [
                {"width": 0},
                {"height": -1},
                {"frames": 0},
                {"width": 32},
                {"height": 32},
                {"codec_name": ""},
                {"codec_name": True},
                {"pix_fmt": " "},
                {"pix_fmt": None},
            ]
        )

    def test_video_info_clock_must_match_caption(self):
        self.assert_bad_video_info(
            [
                {"fps": "15"},
                {"fps": "NaN"},
                {"fps": "1/0"},
                {"frames": 59},
                {"duration_exact": "3"},
                {"duration_exact": "NaN"},
                {"duration_exact": "1/0"},
            ]
        )

    def test_video_info_requires_lowercase_sha256(self):
        self.assert_bad_video_info(
            [
                {"packet_sha256": "a" * 63},
                {"packet_sha256": "a" * 65},
                {"packet_sha256": "g" * 64},
                {"packet_sha256": "A" * 64},
                {"packet_sha256": True},
                {"packet_sha256": None},
            ]
        )

    def test_unsupported_aac_sample_rate_rejected_before_copy(self):
        wav(self.mapping["one"], rate=192000)
        with self.assertRaisesRegex(ValueError, "AAC-supported"):
            self.bind()
        self.assertFalse((self.root / "audio").exists())

    def test_manifest_metadata_tampering_rejected(self):
        result = self.bind()
        path = Path(result["manifest_path"])
        value = json.loads(path.read_text())
        value["cues"][0]["offset_samples"] = 4000
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "metadata"):
            audio.verify_audio(path)

    def test_external_frozen_path_rejected(self):
        result = self.bind()
        path = Path(result["manifest_path"])
        value = json.loads(path.read_text())
        value["cues"][0]["wav"]["path"] = str(self.mapping["one"])
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "inside its audio directory"):
            audio.verify_audio(path)

    def test_claims_cannot_be_escalated(self):
        result = self.bind()
        path = Path(result["manifest_path"])
        value = json.loads(path.read_text())
        value["semantic_acceptance"] = True
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "checked audio binding"):
            audio.verify_audio(path)

    def test_output_is_new_and_outside_source_directories(self):
        for output in ("run/audio", "captions/audio", "run", "captions"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                self.bind(output)
        (self.root / "dangling").symlink_to(self.root / "absent")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.bind("dangling")
        self.bind()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.bind()

    def test_timeouts_reject_unbounded_or_boolean_values(self):
        for timeout in (0, -1, float("nan"), float("inf"), True, 301, "30"):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.bind(tool_timeout=timeout)

    def test_video_clock_must_match_caption_clock(self):
        self.probe.return_value = dict(self.info, frames=59)
        with self.assertRaisesRegex(ValueError, "clock differs"):
            self.bind()

    def test_tool_uses_shell_false_and_bounded_timeout(self):
        result = subprocess.CompletedProcess([], 0, b"ok", b"")
        with patch.object(audio.subprocess, "run", return_value=result) as run:
            self.assertEqual(audio._tool(["ffprobe", "100%.mp4"], 12), b"ok")
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(run.call_args.kwargs["timeout"], 12)
        self.assertEqual(run.call_args.args[0], ["ffprobe", "100%.mp4"])

    def preview(self, result):
        return audio.preview_audio(
            result["manifest_path"], self.root / "preview 100%", tool_timeout=10
        )

    def test_preview_is_independent_and_preserves_video(self):
        result = self.bind()

        def tool(argv, timeout):
            self.assertEqual(timeout, 10)
            if "-filter_complex" in argv:
                self.assertEqual(argv[argv.index("-c:v") + 1], "copy")
                self.assertNotIn("-shortest", argv)
                self.assertEqual(argv[argv.index("-i") + 1], str(self.video))
                Path(argv[-1]).write_bytes(b"preview")
            return b""

        with patch.object(audio, "_tool", side_effect=tool):
            preview = self.preview(result)
        self.assertTrue(preview["video_stream_unchanged"])
        self.assertFalse(preview["revision_eligible"])
        self.assertEqual(audio.verify_audio(result["manifest_path"]), result)

    def test_changed_video_output_never_gets_success_manifest(self):
        result = self.bind()
        self.probe.side_effect = [self.info, dict(self.info, packet_sha256="b" * 64)]
        with (
            patch.object(audio, "_tool", return_value=b""),
            self.assertRaisesRegex(ValueError, "changed video"),
        ):
            self.preview(result)
        self.assertFalse((self.root / "preview 100%" / "manifest.json").exists())

    def test_changed_binding_during_preview_rejected(self):
        result = self.bind()

        def tool(argv, timeout):
            if "-filter_complex" in argv:
                Path(argv[-1]).write_bytes(b"preview")
                Path(result["manifest_path"]).write_text("{}")
            return b""

        with (
            patch.object(audio, "_tool", side_effect=tool),
            self.assertRaisesRegex(ValueError, "SHA-256"),
        ):
            self.preview(result)
        self.assertFalse((self.root / "preview 100%" / "manifest.json").exists())

    def test_tool_timeout_never_gets_success_manifest(self):
        result = self.bind()
        with patch.object(audio, "_tool", side_effect=subprocess.TimeoutExpired("ffmpeg", 10)):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.preview(result)
        self.assertFalse((self.root / "preview 100%" / "manifest.json").exists())

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg/ffprobe")
    def test_real_ffmpeg_preview_stream_identity_and_duration(self):
        self.media_mock.stop()
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=32x32:r=30:d=2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(self.video),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        # Exercise the real successful-run -> captions -> WAV -> preview chain.
        from scene_production.captions import bind

        self.sidecar_mock.stop()
        source = self.run / "source.py"
        source.write_text("# historical scene snapshot\n")
        timeline = self.run / "timeline.json"
        timeline.write_text(
            json.dumps({"version": 1, "segments": [{"label": "demo", "start": 0, "end": 2}]})
        )
        self.receipt.write_text(
            json.dumps(
                {
                    "status": "success",
                    "run_dir": str(self.run),
                    "source": str(self.root / "original.py"),
                    "source_sha256": digest(source),
                    "timeline_sha256": digest(timeline),
                    "video": str(self.video),
                    "video_sha256": digest(self.video),
                }
            )
        )
        events = self.root / "events.json"
        events.write_text(
            json.dumps({"fps": 30, "duration_frames": 60, "events": self.captions["cues"]})
        )
        captions = bind(self.receipt, events, self.root / "real captions")
        self.caption_path = Path(captions["manifest_path"])
        inputs = [*self.run.iterdir(), *self.caption_path.parent.iterdir(), *self.mapping.values()]
        before = {p: digest(p) for p in inputs}
        result = self.bind()
        made = self.preview(result)
        self.assertEqual(made["video_info"], result["video_info"])
        self.assertEqual(made["video_info"]["frames"], 60)
        self.assertEqual(made["video_info"]["duration_exact"], "2")
        self.assertEqual(before, {p: digest(p) for p in before})
        self.assertEqual(made["preview"]["sha256"], digest(made["preview"]["path"]))


if __name__ == "__main__":
    unittest.main()
