import unittest

from unittest.mock import patch

from app.services.media_service import analyze_audio_video_sync, analyze_subtitle_timing, analyze_shot_media_consistency


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


    @patch("app.services.media_service.probe_media")
    def test_shot_media_consistency_accepts_matching_clips(self, probe):
        probe.side_effect = [{"streams":[{"codec_type":"video","width":1920,"height":1080,"avg_frame_rate":"25/1","codec_name":"h264","pix_fmt":"yuv420p"}]}, {"streams":[{"codec_type":"video","width":1920,"height":1080,"avg_frame_rate":"25/1","codec_name":"h264","pix_fmt":"yuv420p"}]}]
        result = analyze_shot_media_consistency([{"shot_id":1,"shot_number":1,"path":"one.mp4"},{"shot_id":2,"shot_number":2,"path":"two.mp4"}])
        self.assertEqual(result["anomalies"], [])
        self.assertEqual(result["scanned_shots"], 2)

    @patch("app.services.media_service.probe_media")
    def test_shot_media_consistency_reports_resolution_and_frame_rate_mismatch(self, probe):
        probe.side_effect = [{"streams":[{"codec_type":"video","width":1920,"height":1080,"avg_frame_rate":"25/1","codec_name":"h264","pix_fmt":"yuv420p"}]}, {"streams":[{"codec_type":"video","width":1280,"height":720,"avg_frame_rate":"30/1","codec_name":"h264","pix_fmt":"yuv420p"}]}]
        result = analyze_shot_media_consistency([{"shot_id":1,"shot_number":1,"path":"one.mp4"},{"shot_id":2,"shot_number":2,"path":"two.mp4"}])
        self.assertEqual(len(result["anomalies"]), 1)
        labels = {x["label"] for x in result["anomalies"][0]["differences"]}
        self.assertIn("画面宽度", labels)
        self.assertIn("帧率", labels)

    @patch("app.services.media_service.probe_media")
    def test_shot_media_consistency_reports_unreadable_clip(self, probe):
        probe.side_effect = [ValueError("probe failed")]
        result = analyze_shot_media_consistency([{"shot_id":8,"shot_number":8,"path":"broken.mp4"}])
        self.assertEqual(result["scanned_shots"], 0)
        self.assertEqual(result["skipped_shots"][0]["shot_id"], 8)
if __name__ == "__main__":
    unittest.main()
