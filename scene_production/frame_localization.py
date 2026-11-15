"""Bounded navigation diagnostics; never change an audit verdict."""

import json

LAYERS = (
    "raw_rgba_hash",
    "decoded_rgb_md5",
    "decoded_relative_pts",
    "decoded_duration",
    "decoded_byte_size",
)
MAX_SECTIONS = 512
MAX_RANGES_PER_LAYER = 64
MAX_RANGES = 256
MAX_SAMPLES = 12
MAX_BYTES = 256 * 1024


def _summarize(count, differs, allowance):
    ranges = []
    changed = total = 0
    start = first = last = None
    for i in range(count):
        if differs(i):
            changed += 1
            first = i if first is None else first
            last = i
            if start is None:
                start = i
        elif start is not None:
            total += 1
            if len(ranges) < allowance:
                ranges.append([start, i])
            start = None
    if start is not None:
        total += 1
        if len(ranges) < allowance:
            ranges.append([start, count])
    samples = []
    if changed:
        wanted = {0, (changed - 1) // 2, changed - 1}
        ordinal = 0
        for i in range(count):
            if differs(i):
                if ordinal in wanted:
                    samples.append(i)
                ordinal += 1
    return {
        "compared_count": count,
        "changed_count": changed,
        "first_changed_offset": first,
        "last_changed_offset": last,
        "offset_ranges": ranges,
        "total_range_count": total,
        "returned_range_count": len(ranges),
        "truncated": len(ranges) < total,
    }, samples


class Localization:
    """Consume already-validated vectors without rendering or decoding again."""

    def __init__(self):
        self.sections = []
        self.remaining = MAX_RANGES
        self.unavailable = False
        self._candidates = []

    def add(self, before, after, raw_before, raw_after, decoded_before, decoded_after):
        if self.unavailable:
            return
        if len(self.sections) >= MAX_SECTIONS:
            self.unavailable = True
            self.sections.clear()
            self._candidates.clear()
            return
        item = {
            "id": before["id"],
            "before_frames": [before["start_frame"], before["end_frame"]],
            "after_frames": [after["start_frame"], after["end_frame"]],
            "samples": [],
        }
        self.sections.append(item)
        candidates = []
        self._candidates.append(candidates)
        count = len(raw_before)
        if count != len(raw_after):
            item["alignment"] = "unavailable_count_changed"
            return
        if len(decoded_before) != count or len(decoded_after) != count:
            item["alignment"] = "unavailable_decoded_count"
            return
        if any(len(row) != 4 for rows in (decoded_before, decoded_after) for row in rows):
            item["alignment"] = "unavailable_decoded_record"
            return
        item["alignment"] = "section_local_ordinal"
        pairs = [(raw_before, raw_after, None)] + [
            (decoded_before, decoded_after, field) for field in (3, 0, 1, 2)
        ]
        item["layers"] = {}
        for name, (left, right, field) in zip(LAYERS, pairs):

            def differs(i):
                return left[i] != right[i] if field is None else left[i][field] != right[i][field]

            summary, hints = _summarize(count, differs, min(MAX_RANGES_PER_LAYER, self.remaining))
            self.remaining -= summary["returned_range_count"]
            item["layers"][name] = summary
            if name in LAYERS[:2]:
                for offset in hints:
                    if offset not in candidates:
                        candidates.append(offset)
        item["non_pixel_record_changes"] = any(
            item["layers"][name]["changed_count"] for name in LAYERS[2:]
        )
        # Label each chosen offset by actual inequality, not by the hint's origin.
        self._candidates[-1] = [
            {
                "section_offset": i,
                "before_frame": before["start_frame"] + i,
                "after_frame": after["start_frame"] + i,
                "pixel_layers": [
                    name
                    for name, changed in (
                        (LAYERS[0], raw_before[i] != raw_after[i]),
                        (LAYERS[1], decoded_before[i][3] != decoded_after[i][3]),
                    )
                    if changed
                ],
            }
            for i in candidates
        ]

    def finish(self, *, frame_rate=None, inspection_alignment=None):
        if self.unavailable:
            return {
                "status": "unavailable_resource_limit",
                "scope": "non_target_sections",
                "frame_rate": frame_rate,
                "inspection_alignment": inspection_alignment,
            }
        for item in self.sections:
            item["samples"] = []
        remaining = MAX_SAMPLES
        # Round-robin gives each pixel-eligible section one hint before extras.
        for position in range(6):
            for item, candidates in zip(self.sections, self._candidates):
                if remaining and position < len(candidates):
                    item["samples"].append(candidates[position])
                    remaining -= 1
        for item, candidates in zip(self.sections, self._candidates):
            item["sample_candidates"] = len(candidates)
            item["omitted_sample_candidates"] = len(candidates) - len(item["samples"])
        result = {
            "frame_rate": frame_rate,
            "inspection_alignment": inspection_alignment,
            "version": 1,
            "status": "available",
            "scope": "non_target_sections",
            "selection": "deterministic_navigation",
            "representative": False,
            "visual_acceptance": False,
            "sections": self.sections,
            "notice": "Pixel hashes are not visual severity. Decoded timing/size are separate. "
            "Samples are incomplete navigation hints, not review approval; indices are "
            "section-local ordinal mappings, not packet PTS. Existing audit verdict is unchanged.",
        }
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_BYTES:
            return {
                "status": "unavailable_resource_limit",
                "scope": "non_target_sections",
                "frame_rate": frame_rate,
                "inspection_alignment": inspection_alignment,
            }
        return result
