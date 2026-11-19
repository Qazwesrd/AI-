import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.routers.media import (
    latest_completed_episode_shot_tasks,
    refresh_episode_video_tasks,
)
from app.services.media_service import (
    download_validated_video,
    finalize_rendered_video,
)


class AtomicVideoArtifactTests(unittest.TestCase):
    @patch("app.services.media_service.validate_rendered_video")
    @patch("app.services.media_service.http_download")
    def test_download_promotes_only_after_validation(self, download, validate):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "episode.mp4"
            destination.write_bytes(b"previous-valid-output")

            def write_download(_url, temporary):
                Path(temporary).write_bytes(b"x" * 2048)

            download.side_effect = write_download
            validate.side_effect = ValueError("FFprobe 无法确认视频时长")

            with self.assertRaisesRegex(ValueError, "无法确认"):
                download_validated_video("https://cdn.invalid/video.mp4", destination)

            self.assertEqual(destination.read_bytes(), b"previous-valid-output")
            self.assertEqual(list(Path(directory).iterdir()), [destination])

    @patch("app.services.media_service.validate_rendered_video")
    @patch("app.services.media_service.http_download")
    def test_successful_download_is_atomically_promoted(self, download, validate):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "episode.mp4"
            def write_download(_url, temporary):
                Path(temporary).write_bytes(b"v" * 4096)
            download.side_effect = write_download
            validate.return_value = {"valid": True, "duration_seconds": 120.0}

            report = download_validated_video(
                "https://cdn.invalid/video.mp4", destination, require_audio=True
            )

            self.assertTrue(destination.is_file())
            self.assertEqual(destination.stat().st_size, 4096)
            self.assertEqual(report["duration_seconds"], 120.0)
            self.assertEqual(list(Path(directory).iterdir()), [destination])
            validate.assert_called_once()

    @patch("app.services.media_service.validate_rendered_video", side_effect=ValueError("损坏文件"))
    def test_invalid_staged_render_does_not_replace_previous_output(self, _validate):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "staged.mp4"
            destination = Path(directory) / "final.mp4"
            source.write_bytes(b"x" * 2048)
            destination.write_bytes(b"previous-good-output")

            with self.assertRaisesRegex(ValueError, "损坏文件"):
                finalize_rendered_video(source, destination)

            self.assertTrue(source.exists())
            self.assertEqual(destination.read_bytes(), b"previous-good-output")


class EpisodeShotArtifactSelectionTests(unittest.TestCase):
    def test_failed_newer_retry_does_not_hide_older_successful_clip(self):
        with tempfile.TemporaryDirectory() as directory:
            successful_path = Path(directory) / "shot.mp4"
            successful_path.write_bytes(b"x" * 2048)
            old_success = SimpleNamespace(
                id=10, status="completed", result_path=str(successful_path),
                request_data={"episode_id": 7, "shot_id": 33},
            )
            newer_failure = SimpleNamespace(
                id=11, status="failed", result_path=None,
                request_data={"episode_id": 7, "shot_id": 33},
            )

            selected = latest_completed_episode_shot_tasks(
                [old_success, newer_failure], 7
            )

            self.assertIs(selected[33], old_success)

    def test_other_episode_and_missing_files_are_not_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = SimpleNamespace(
                id=12, status="completed",
                result_path=str(Path(directory) / "missing.mp4"),
                request_data={"episode_id": 7, "shot_id": 33},
            )
            other_episode = SimpleNamespace(
                id=13, status="completed", result_path=str(Path(directory) / "missing.mp4"),
                request_data={"episode_id": 8, "shot_id": 34},
            )
            self.assertEqual(
                latest_completed_episode_shot_tasks([missing, other_episode], 7), {}
            )


class EpisodeDownloadRecoveryTests(unittest.TestCase):
    def test_invalid_episode_download_stays_retryable_without_registering_media(self):
        episode = SimpleNamespace(id=7)
        task = SimpleNamespace(
            id=91, status="submitted", provider="provider-x", remote_task_id="remote-91",
            request_data={
                "episode_id": 7, "shot_id": 33, "shot_number": 2,
                "status_path_template": "/videos/{task_id}",
                "status_field": "status", "video_url_field": "video_url",
                "confirm_paid": False,
            },
            result_data=None, result_path=None, error=None,
        )
        episode_query = Mock()
        episode_query.filter.return_value.first.return_value = episode
        task_query = Mock()
        task_query.filter.return_value.all.return_value = [task]
        db = Mock()
        db.query.side_effect = [episode_query, task_query]

        with patch(
            "app.routers.media.get_provider",
            return_value=(SimpleNamespace(), "key", "https://provider.invalid"),
        ), patch(
            "app.routers.media.http_json",
            return_value={
                "status": "completed",
                "video_url": "https://cdn.invalid/episode-shot.mp4",
            },
        ), patch(
            "app.routers.media.download_validated_video",
            side_effect=ValueError("视频文件损坏"),
        ) as download:
            result = refresh_episode_video_tasks(7, db)

        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["tasks"][0]["status"], "download_failed")
        self.assertTrue(result["tasks"][0]["retry_download_only"])
        self.assertIsNone(task.result_path)
        self.assertIn("不会重新提交视频生成请求", task.error)
        download.assert_called_once()
        db.add.assert_not_called()
        db.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
