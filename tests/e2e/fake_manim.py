"""A fake `manim` for Tier A pure-pipeline e2e tests (automation harness Phase 1).

Invoked as the argv PREFIX through build_command's ``base_command`` (e.g.
``[sys.executable, fake_manim.py]``), so it receives exactly the tail
build_command appends: ``<file> <scene> <res_flag> --media_dir <out> [engine]``.
It mimics manim's on-disk layout -- ``<media_dir>/videos/<file_stem>/<quality>/
<Scene>.mp4`` -- so the real RenderRunner / output assertions exercise the true
Popen + reader-thread + cancel path without manim or ffmpeg installed.

Behavior is selected by env var FAKE_MANIM_MODE (default "success"); a partial
batch can map per-scene modes via FAKE_MANIM_MODE_MAP="A=success,B=fail".
No third-party imports -- pure stdlib so it runs on a bare python3.
"""

import os
import signal
import sys
import time

# Map a manim quality flag to the directory segment manim names the output with.
_QUALITY_DIRS = {
    "-ql": "480p15",
    "-qm": "720p30",
    "-qh": "1080p60",
    "-qp": "1440p60",
    "-qk": "2160p60",
}


def _parse_args(argv):
    file_path = argv[0]
    scene = argv[1]
    quality_flag = argv[2]
    media_dir = argv[argv.index("--media_dir") + 1]
    return file_path, scene, quality_flag, media_dir


def _mode_for(scene):
    mapping = os.environ.get("FAKE_MANIM_MODE_MAP")
    if mapping:
        for pair in mapping.split(","):
            name, _, mode = pair.partition("=")
            if name.strip() == scene:
                return mode.strip()
    return os.environ.get("FAKE_MANIM_MODE", "success")


def _output_path(media_dir, file_path, scene, quality_flag):
    stem = os.path.splitext(os.path.basename(file_path))[0]
    quality = _QUALITY_DIRS.get(quality_flag, "480p15")
    return os.path.join(media_dir, "videos", stem, quality, f"{scene}.mp4")


def main(argv):
    file_path, scene, quality_flag, media_dir = _parse_args(argv)
    mode = _mode_for(scene)

    if mode == "fail":
        sys.stderr.write(
            "Traceback (most recent call last):\n"
            f'  File "{file_path}", line 1, in construct\n'
            "NameError: name 'undefined_thing' is not defined\n"
        )
        return 1

    if mode in ("hang", "hang_ignore_sigterm"):
        if mode == "hang_ignore_sigterm":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        print("ready", flush=True)
        time.sleep(60)
        return 0

    if mode == "chatty":
        for i in range(int(os.environ.get("FAKE_MANIM_LINES", "200"))):
            print(f"INFO line {i}", flush=True)
    elif mode == "crlf":
        for pct in (0, 25, 50, 75, 100):
            sys.stdout.write(f"\rRendering {pct}%")
            sys.stdout.flush()
        sys.stdout.write("\n")

    # success (and chatty/crlf): produce the output file manim would.
    print(f"INFO Rendering {scene} from {file_path}", flush=True)
    out = _output_path(media_dir, file_path, scene, quality_flag)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as handle:
        handle.write(b"\x00\x00\x00\x18ftypmp42" + b"fake mp4 payload" * 64)
    print(f"INFO File ready at {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
