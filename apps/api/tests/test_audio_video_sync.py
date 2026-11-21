import unittest

from app.services.media_service import analyze_audio_video_sync, analyze_subtitle_timing


class AudioVideoSyncDiagnosticsTests(unittest.TestCase):
    def test_aligned_stream_metadata_returns_ok(self):
        result = analyze_audio_video_sync({
            "format": {"duration": "12.000"},
            "streams": [
                {"codec_type": "video", "start_time": "0.000", "duration": "12.000"},
                {"codec_type": "audio", "start_time": "0.000", "duration": "12.000"},
            ],
        })
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["audio_video_start_offset_seconds"], 0.0)
        self.assertEqual(result["audio_video_duration_delta_seconds"], 0.0)
        self.assertTrue(result["metadata_only"])

    def test_audio_start_offset_is_reported(self):
        result = analyze_audio_video_sync({
            "format": {"duration": "10"},
            "streams": [
                {"codec_type": "video", "start_time": "0", "duration": "10"},
                {"codec_type": "audio", "start_time": "0.4", "duration": "10"},
            ],
        })
        self.assertEqual(result["status"], "warning")
        self.assertAlmostEqual(result["audio_video_start_offset_seconds"], 0.4)
        self.assertTrue(any("起始时间" in item for item in result["warnings"]))

    def test_audio_duration_mismatch_is_reported(self):
        result = analyze_audio_video_sync({
            "format": {"duration": "10"},
            "streams": [
                {"codec_type": "video", "start_time": "0", "duration": "10"},
                {"codec_type": "audio", "start_time": "0", "duration": "11.2"},
            ],
        })
        self.assertEqual(result["status"], "warning")
        self.assertAlmostEqual(result["audio_video_duration_delta_seconds"], 1.2)
        self.assertTrue(any("时长" in item for item in result["warnings"]))

    def test_missing_audio_is_reported_without_claiming_sync(self):
        result = analyze_audio_video_sync({
            "format": {"duration": "5"},
            "streams": [{"codec_type": "video", "start_time": "0", "duration": "5"}],
        })
        self.assertEqual(result["status"], "warning")
        self.assertIsNone(result["audio"])
        self.assertIsNone(result["audio_video_start_offset_seconds"])

    def test_missing_video_stream_is_rejected(self):
        with self.assertRaises(ValueError):
            analyze_audio_video_sync({
                "format": {"duration": "5"},
                "streams": [{"codec_type": "audio", "start_time": "0", "duration": "5"}],
            })

    def test_subtitle_cues_within_video_duration_are_valid(self):
        result = analyze_subtitle_timing([
            {"start": 0, "end": 2, "text": "开场"},
            {"start": 2.1, "end": 4, "text": "对白"},
        ], 5)
        self.assertEqual(result["invalid_ranges"], [])
        self.assertEqual(result["overlaps"], [])
        self.assertEqual(result["warnings"], [])

    def test_subtitle_cue_after_video_duration_is_reported(self):
        result = analyze_subtitle_timing([{"start": 4, "end": 6, "text": "超出时长"}], 5)
        self.assertEqual(len(result["invalid_ranges"]), 1)
        self.assertIn("超出成片时长", result["invalid_ranges"][0]["reason"])

    def test_overlapping_subtitle_cues_are_reported(self):
        result = analyze_subtitle_timing([
            {"start": 0, "end": 2, "text": "第一句"},
            {"start": 1.5, "end": 3, "text": "第二句"},
        ], 5)
        self.assertEqual(len(result["overlaps"]), 1)
        self.assertAlmostEqual(result["overlaps"][0]["overlap_seconds"], 0.5)

    def test_invalid_negative_or_reversed_subtitle_cue_is_reported(self):
        result = analyze_subtitle_timing([
            {"start": -1, "end": 1, "text": "负数"},
            {"start": 3, "end": 2, "text": "倒序"},
        ], 5)
        self.assertEqual(len(result["invalid_ranges"]), 2)

    def test_subtitle_report_rejects_unknown_video_duration(self):
        with self.assertRaises(ValueError):
            analyze_subtitle_timing([], 0)


if __name__ == "__main__":
    unittest.main()
