import unittest
from unittest.mock import patch, Mock

from fastapi import HTTPException

from app.routers.tasks import _status_group, control_unified_task, is_known_remote_3d_task


class UnifiedTaskStatusTests(unittest.TestCase):
    def test_known_remote_3d_task_is_not_treated_as_lost_submission(self):
        task = Mock(task_type="model3d_multiview", result_url="tripo-task:remote-123")
        self.assertTrue(is_known_remote_3d_task(task))

    def test_3d_task_without_remote_id_requires_reconciliation(self):
        task = Mock(task_type="model3d_multiview", result_url="")
        self.assertFalse(is_known_remote_3d_task(task))

    def test_active_provider_statuses_are_normalized(self):
        for value in ("pending", "queued", "submitted", "processing", "running"):
            with self.subTest(status=value):
                self.assertEqual(_status_group(value), "active")

    def test_completed_status_variants_are_normalized(self):
        for value in ("completed", "success", "succeeded", "finished", "done"):
            with self.subTest(status=value):
                self.assertEqual(_status_group(value), "completed")

    def test_uncertain_submission_needs_attention(self):
        for value in ("submission_unknown", "awaiting_video_url", "submitting", "interrupted_unknown"):
            with self.subTest(status=value):
                self.assertEqual(_status_group(value), "attention")

    def test_failure_pause_cancel_and_partial_are_distinct(self):
        self.assertEqual(_status_group("failed"), "failed")
        self.assertEqual(_status_group("paused"), "paused")
        self.assertEqual(_status_group("cancelled"), "cancelled")
        self.assertEqual(_status_group("partial"), "partial")

    def test_unknown_status_is_preserved_as_other(self):
        self.assertEqual(_status_group("provider_custom_state"), "other")
        self.assertEqual(_status_group(None), "other")

    def test_unified_image_pause_delegates_to_existing_safe_handler(self):
        with patch("app.routers.tasks.pause_image_task", return_value={"success": True}) as handler:
            result = control_unified_task("image", 17, "pause", db=object())
        self.assertTrue(result["success"])
        handler.assert_called_once()

    def test_unified_image_retry_delegates_to_paid_safe_handler(self):
        db = Mock()
        db.query.return_value.filter.return_value.first.return_value = None
        with patch("app.routers.tasks.retry_image_task", return_value={"status": "pending"}) as handler:
            result = control_unified_task("image", 18, "retry", db=db)
        self.assertEqual(result["status"], "pending")
        handler.assert_called_once()

    def test_unified_analysis_control_does_not_resume_normal_partial_job(self):
        job = Mock(status="partial", chunk_results={"1": [{"name": "saved"}]})
        db = Mock()
        db.query.return_value.filter.return_value.first.return_value = job
        with self.assertRaises(HTTPException) as raised:
            control_unified_task("analysis", 2, "retry", db=db)
        self.assertEqual(raised.exception.status_code, 409)
        db.commit.assert_not_called()

    def test_unified_media_does_not_allow_local_force_cancel(self):
        with self.assertRaises(HTTPException) as raised:
            control_unified_task("media", 4, "cancel", db=object())
        self.assertEqual(raised.exception.status_code, 409)

    def test_analysis_retry_requires_explicit_provider_reconciliation(self):
        job = Mock(
            status="interrupted_unknown",
            chunk_results={"1": [{"name": "saved"}], "__chunk_states__": {"2": "submission_unknown"}},
        )
        db = Mock()
        db.query.return_value.filter.return_value.first.return_value = job
        with self.assertRaises(HTTPException) as raised:
            control_unified_task("analysis", 51, "retry", db=db)
        self.assertEqual(raised.exception.status_code, 409)
        db.commit.assert_not_called()

    def test_analysis_retry_resets_only_unknown_chunk_after_confirmation(self):
        job = Mock(
            status="interrupted_unknown",
            chunk_results={
                "1": [{"name": "saved"}],
                "__chunk_states__": {"1": "completed", "2": "submission_unknown"},
            },
        )
        db = Mock()
        db.query.return_value.filter.return_value.first.side_effect = [job, None]
        result = control_unified_task(
            "analysis", 52, "retry", confirm_no_remote_job=True, db=db
        )
        self.assertEqual(job.status, "partial")
        self.assertEqual(job.chunk_results["__chunk_states__"]["1"], "completed")
        self.assertEqual(job.chunk_results["__chunk_states__"]["2"], "pending")
        self.assertEqual(job.chunk_results["1"], [{"name": "saved"}])
        self.assertTrue(result["success"])
        db.commit.assert_called_once()

    def test_speech_retry_requires_explicit_provider_reconciliation(self):
        task = Mock(task_type="episode_speech", status="submission_unknown", remote_task_id=None)
        db = Mock()
        db.query.return_value.filter.return_value.first.return_value = task
        with self.assertRaises(HTTPException) as raised:
            control_unified_task("media", 41, "retry", db=db)
        self.assertEqual(raised.exception.status_code, 409)
        db.commit.assert_not_called()

    def test_speech_retry_unblocks_only_after_explicit_confirmation(self):
        task = Mock(task_type="episode_speech", status="submission_unknown", remote_task_id=None)
        db = Mock()
        db.query.return_value.filter.return_value.first.side_effect = [task, None]
        result = control_unified_task(
            "media", 42, "retry", confirm_no_remote_job=True, db=db
        )
        self.assertEqual(task.status, "failed")
        self.assertTrue(result["success"])
        db.commit.assert_called_once()

    def test_video_task_cannot_use_speech_retry_reconciliation(self):
        task = Mock(task_type="video", status="submission_unknown", remote_task_id=None)
        db = Mock()
        db.query.return_value.filter.return_value.first.return_value = task
        with self.assertRaises(HTTPException) as raised:
            control_unified_task(
                "media", 43, "retry", confirm_no_remote_job=True, db=db
            )
        self.assertEqual(raised.exception.status_code, 409)

    def test_interrupted_image_retry_requires_explicit_provider_verification(self):
        task = Mock(status="interrupted_unknown", task_type="character")
        db = Mock()
        db.query.return_value.filter.return_value.first.return_value = task
        with self.assertRaises(HTTPException) as raised:
            control_unified_task("image", 23, "retry", db=db)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(task.status, "interrupted_unknown")

    def test_interrupted_image_retry_only_requeues_after_explicit_confirmation(self):
        task = Mock(status="interrupted_unknown", task_type="character")
        db = Mock()
        db.query.return_value.filter.return_value.first.return_value = task
        result = control_unified_task(
            "image", 24, "retry", confirm_no_remote_job=True, db=db
        )
        self.assertEqual(task.status, "pending")
        self.assertEqual(result["status"], "pending")
        db.commit.assert_called_once()

    def test_interrupted_3d_task_cannot_use_generic_retry(self):
        task = Mock(status="interrupted_unknown", task_type="model3d_multiview")
        db = Mock()
        db.query.return_value.filter.return_value.first.return_value = task
        with self.assertRaises(HTTPException) as raised:
            control_unified_task(
                "image", 25, "retry", confirm_no_remote_job=True, db=db
            )
        self.assertEqual(raised.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
