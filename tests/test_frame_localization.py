import unittest
from unittest.mock import patch

from scene_production.frame_localization import Localization, _summarize


def records(n):
    return [(str(i), "1", "3", "pixel") for i in range(n)]


def add(builder, name="s", n=7, changes=(0, 3, 6), timing=False, before=0, after=0):
    a, b = ["raw"] * n, ["raw"] * n
    x, y = records(n), records(n)
    for i in changes:
        if timing:
            y[i] = ("changed", *y[i][1:])
        else:
            b[i] = "different"
    builder.add(
        {"id": name, "start_frame": before, "end_frame": before + n},
        {"id": name, "start_frame": after, "end_frame": after + n},
        a,
        b,
        x,
        y,
    )


class LocalizationTests(unittest.TestCase):
    def test_sparse_ranges_and_shifted_indices(self):
        b = Localization()
        add(b, before=60, after=75)
        item = b.finish()["sections"][0]
        self.assertEqual(item["layers"]["raw_rgba_hash"]["offset_ranges"], [[0, 1], [3, 4], [6, 7]])
        self.assertEqual([s["after_frame"] for s in item["samples"]], [75, 78, 81])
        self.assertEqual([s["before_frame"] for s in item["samples"]], [60, 63, 66])

    def test_empty_and_all_and_single(self):
        for indices, expected in [((), []), (range(7), [[0, 7]]), ((6,), [[6, 7]])]:
            b = Localization()
            add(b, changes=indices)
            self.assertEqual(
                b.finish()["sections"][0]["layers"]["raw_rgba_hash"]["offset_ranges"], expected
            )

    def test_timing_only_has_no_pixel_samples(self):
        b = Localization()
        add(b, timing=True)
        s = b.finish()["sections"][0]
        self.assertTrue(s["non_pixel_record_changes"])
        self.assertEqual(s["samples"], [])
        self.assertEqual(s["layers"]["decoded_rgb_md5"]["changed_count"], 0)
        self.assertEqual(s["layers"]["decoded_relative_pts"]["changed_count"], 3)

    def test_fairness_only_pixel_eligible_sections(self):
        b = Localization()
        for i in range(13):
            add(b, name=f"timing{i}", timing=True)
        for i in range(13):
            add(b, name=f"pixel{i}")
        sections = b.finish()["sections"]
        self.assertTrue(all(not x["samples"] for x in sections[:13]))
        self.assertTrue(all(len(x["samples"]) == 1 for x in sections[13:25]))
        self.assertEqual(sections[25]["omitted_sample_candidates"], 3)

    def test_layers_are_separate(self):
        b = Localization()
        x, y = records(4), records(4)
        y[0] = ("0", "2", "3", "pixel")
        y[1] = ("1", "1", "6", "pixel")
        y[2] = ("2", "1", "3", "other")
        section = {"id": "s", "start_frame": 0, "end_frame": 4}
        b.add(section, section, ["a"] * 4, ["a"] * 4, x, y)
        s = b.finish()["sections"][0]
        self.assertEqual(s["layers"]["decoded_duration"]["offset_ranges"], [[0, 1]])
        self.assertEqual(s["layers"]["decoded_byte_size"]["offset_ranges"], [[1, 2]])
        self.assertEqual([x["section_offset"] for x in s["samples"]], [2])
        self.assertEqual(s["samples"][0]["pixel_layers"], ["decoded_rgb_md5"])

    def test_count_mismatch_not_zipped(self):
        b = Localization()
        s = {"id": "s", "start_frame": 0, "end_frame": 4}
        b.add(s, s, ["a"] * 4, ["a"] * 3, records(4), records(3))
        self.assertEqual(b.finish()["sections"][0]["alignment"], "unavailable_count_changed")

    def test_bounds_and_determinism(self):
        b = Localization()
        for i in range(5):
            add(b, name=str(i), n=300, changes=range(0, 300, 2))
        x = b.finish()
        import copy

        frozen = copy.deepcopy(x)
        self.assertEqual(frozen, b.finish())
        # finish should also be repeatable without accumulating samples.
        self.assertEqual(
            sum(s["layers"]["raw_rgba_hash"]["returned_range_count"] for s in x["sections"]), 256
        )
        self.assertTrue(
            all(s["layers"]["raw_rgba_hash"]["total_range_count"] == 150 for s in x["sections"])
        )
        with patch("scene_production.frame_localization.MAX_BYTES", 1):
            self.assertEqual(b.finish()["status"], "unavailable_resource_limit")

    def test_maximum_alternation(self):
        result, samples = _summarize(144000, lambda i: i % 2 == 0, 64)
        self.assertEqual(result["total_range_count"], 72000)
        self.assertEqual(result["returned_range_count"], 64)
        self.assertEqual(samples, [0, 71998, 143998])

    def test_section_resource_limit(self):
        b = Localization()
        with patch("scene_production.frame_localization.MAX_SECTIONS", 2):
            for i in range(3):
                add(b, name=str(i))
        self.assertEqual(b.finish()["status"], "unavailable_resource_limit")

    def test_decoded_count_mismatch(self):
        b = Localization()
        s = {"id": "s", "start_frame": 0, "end_frame": 4}
        b.add(s, s, ["a"] * 4, ["a"] * 4, records(3), records(4))
        self.assertEqual(b.finish()["sections"][0]["alignment"], "unavailable_decoded_count")
