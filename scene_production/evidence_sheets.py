"""Small raster previews. Pillow is loaded only for caption-free exports."""

import hashlib


def pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont, __version__
    except ImportError as exc:
        raise RuntimeError(
            "caption-free export requires Pillow; install the [review] extra"
        ) from exc
    return Image, ImageDraw, ImageFont, __version__


def png_limit(width, height):
    # Fixed PNG compression level 1: RGB + scanline bytes, deflate/PNG chunks
    # and a deliberately generous 1 MiB allowance per file.
    return width * height * 3 + height + 1024 * 1024


def verify_native(path, frame, budget):
    Image = pillow()[0]
    budget.check()
    with Image.open(path) as native:
        if (
            native.format != "PNG"
            or native.mode != "RGB"
            or native.size != tuple(frame["dimensions"])
        ):
            raise ValueError("native PNG geometry/mode mismatch")
        native.load()
        if hashlib.sha256(native.tobytes()).hexdigest() != frame["decoded_rgb24_sha256"]:
            raise ValueError("native PNG RGB mismatch")
    budget.check()


def build_sheets(root, frames, coverage, video_sha, export_id, store):
    Image, ImageDraw, ImageFont, _ = pillow()
    bitmap = False
    try:
        font = ImageFont.load_default(size=16)
    except (ImportError, OSError):
        font = ImageFont.load_default_imagefont()
        bitmap = True

    def label(draw, destination, xy, text):
        if not bitmap:
            draw.text(xy, text, fill="black", font=font)
            return
        # Pillow's embedded bitmap font needs explicit scaling for readable
        # 16px labels when FreeType is unavailable. Native tiles are untouched.
        box = font.getbbox(text)
        glyphs = Image.new("RGB", (max(1, box[2] - box[0]), max(1, box[3] - box[1])), "#f5f5f5")
        ImageDraw.Draw(glyphs).text((-box[0], -box[1]), text, fill="black", font=font)
        enlarged = glyphs.resize(
            (round(glyphs.width * 16 / glyphs.height), 16), Image.Resampling.NEAREST
        )
        destination.paste(enlarged, xy)
        enlarged.close()
        glyphs.close()

    sheets = []
    pages = (len(frames) + 11) // 12
    for start in range(0, len(frames), 12):
        store.budget.check()
        page = frames[start : start + 12]
        height = 100 + ((len(page) + 2) // 3) * 242
        sheet = Image.new("RGB", (1020, height), "#f5f5f5")
        draw = ImageDraw.Draw(sheet)
        lines = [
            f"Export {export_id} | video SHA256 prefix {video_sha[:20]}",
            f"Page {start // 12 + 1}/{pages}: {len(page)} displayed here; "
            f"package {len(frames)}/{coverage['total_frame_count']} frames exported",
            "Reduced previews; no review recorded. "
            + ("Gaps remain." if coverage["omitted_intervals"] else "All ordinals exported."),
            "Native files: native/frame-NNN.png | Grid time is nominal, not PTS",
        ]
        for line, text in enumerate(lines):
            label(draw, sheet, (10, 8 + line * 20), text)
        for cell, frame in enumerate(page):
            store.budget.check()
            x, y = 10 + cell % 3 * 340, 100 + cell // 3 * 242
            with Image.open(root / frame["native"]["relative_path"]) as native:
                native.thumbnail((320, 180), Image.Resampling.LANCZOS)
                sheet.paste(native, (x + (320 - native.width) // 2, y + (180 - native.height) // 2))
                tile_size = list(native.size)
            labels = [
                f"Ordinal {frame['frame_index']} | "
                f"{frame['dimensions'][0]}x{frame['dimensions'][1]}",
                f"Nominal grid {frame['grid_time_seconds']} s",
                frame["native"]["relative_path"],
            ]
            for line, text in enumerate(labels):
                label(draw, sheet, (x, y + 184 + line * 17), text)
            frame["sheet"] = {
                "relative_path": f"sheets/sheet-{start // 12 + 1:03}.png",
                "page": start // 12 + 1,
                "cell": cell,
                "tile_dimensions": tile_size,
            }
        relative = f"sheets/sheet-{start // 12 + 1:03}.png"
        entry = store.write(
            relative,
            lambda f: sheet.save(f, format="PNG", compress_level=1),
            png_limit(*sheet.size),
        )
        entry.update(relative_path=relative, dimensions=list(sheet.size), displayed_count=len(page))
        sheets.append(entry)
        sheet.close()
    return sheets
