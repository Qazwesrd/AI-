import unittest

from app.routers.subtitle_tools import parse_srt, render_srt, format_time


class SubtitleToolsTests(unittest.TestCase):
    def test_parse_and_render_preserves_timing_and_order(self):
        source = """1
00:00:01,250 --> 00:00:02,500
你好

2
00:00:03,000 --> 00:00:04,250
世界
"""
        cues = parse_srt(source)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["start"], 1.25)
        self.assertEqual(cues[0]["end"], 2.5)
        self.assertEqual(cues[1]["text"], "世界")
        rendered = render_srt([
            {"start": item["start"], "end": item["end"], "text": "translated"}
            for item in cues
        ])
        reparsed = parse_srt(rendered)
        self.assertEqual([(x["start"], x["end"]) for x in reparsed],
                         [(x["start"], x["end"]) for x in cues])

    def test_parse_accepts_dot_milliseconds(self):
        cues = parse_srt("1\n00:00:00.500 --> 00:00:01.250\nHello")
        self.assertEqual(cues[0]["start"], 0.5)
        self.assertEqual(cues[0]["end"], 1.25)

    def test_invalid_or_empty_srt_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_srt("not a subtitle")
        with self.assertRaises(ValueError):
            parse_srt("1\n00:00:02,000 --> 00:00:01,000\nWrong")

    def test_time_formatter_keeps_milliseconds(self):
        self.assertEqual(format_time(65.125), "00:01:05,125")


if __name__ == "__main__":
    unittest.main()
