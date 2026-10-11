"""Helpers for asserting on manim's on-disk output layout (Tier A / Tier B)."""

import os


def media_output_path(media_dir, file_path, scene, quality_dir):
    """The path manim writes a scene's video to: videos/<stem>/<quality>/<Scene>.mp4."""
    stem = os.path.splitext(os.path.basename(file_path))[0]
    return os.path.join(media_dir, "videos", stem, quality_dir, f"{scene}.mp4")


def find_videos(media_dir):
    """Every *.mp4 written anywhere under ``media_dir`` (recursive)."""
    found = []
    for dirpath, _dirnames, filenames in os.walk(media_dir):
        for name in filenames:
            if name.endswith(".mp4"):
                found.append(os.path.join(dirpath, name))
    return sorted(found)


def assert_nonempty_video(test_case, path):
    test_case.assertTrue(os.path.isfile(path), f"expected a video at {path}")
    test_case.assertGreater(os.path.getsize(path), 0, f"video at {path} is empty")
