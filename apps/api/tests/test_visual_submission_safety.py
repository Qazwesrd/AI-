import unittest
from types import SimpleNamespace

from app.routers.visual import (
    mark_image_submission_unknown,
    mark_tripo_submission_failure,
    tripo_task_action,
)


class ImageSubmissionSafetyTests(unittest.TestCase):
    def test_ambiguous_http_failure_is_not_marked_as_retryable_failure(self):
        task = SimpleNamespace(status="running", error="")

        mark_image_submission_unknown(task, "图片服务 HTTP 502: upstream timeout")

        self.assertEqual(task.status, "interrupted_unknown")
        self.assertIn("核对图像服务商控制台", task.error)
        self.assertIn("不会重复扣费", task.error)
        self.assertIn("HTTP 502", task.error)

    def test_manual_review_error_is_bounded_to_database_field_limit(self):
        task = SimpleNamespace(status="running", error="")

        mark_image_submission_unknown(task, "x" * 5000)

        self.assertLessEqual(len(task.error), 2000)

    def test_tripo_submission_in_progress_is_never_resubmitted(self):
        task = SimpleNamespace(status="submitting", result_url="")
        self.assertEqual(tripo_task_action(task), "review")

    def test_tripo_ambiguous_submission_is_never_resubmitted(self):
        task = SimpleNamespace(status="interrupted_unknown", result_url="")
        self.assertEqual(tripo_task_action(task), "review")

    def test_existing_remote_tripo_task_is_reused(self):
        task = SimpleNamespace(status="submitted", result_url="tripo-task:remote-123")
        self.assertEqual(tripo_task_action(task), "reuse")

    def test_completed_tripo_task_can_be_replaced_by_a_new_explicit_submission(self):
        task = SimpleNamespace(status="completed", result_url="https://example.invalid/model.glb")
        self.assertEqual(tripo_task_action(task), "new")

    def test_3d_failure_before_generation_request_is_retryable_after_review(self):
        task = SimpleNamespace(id=31, status="submitting", error="")
        asset = SimpleNamespace(data={"model3d_task": {"status": "submitting"}})

        status = mark_tripo_submission_failure(task, asset, "reference upload failed", False)

        self.assertEqual(status, "failed")
        self.assertEqual(task.status, "failed")
        self.assertIn("远程生成请求发出前失败", task.error)
        self.assertEqual(asset.data["model3d_task"]["status"], "failed")

    def test_3d_failure_after_generation_request_requires_manual_reconciliation(self):
        task = SimpleNamespace(id=32, status="submitting", error="")
        asset = SimpleNamespace(data={"model3d_task": {"status": "submitting"}})

        status = mark_tripo_submission_failure(task, asset, "gateway timeout", True)

        self.assertEqual(status, "interrupted_unknown")
        self.assertEqual(task.status, "interrupted_unknown")
        self.assertIn("核对 Tripo 控制台", task.error)
        self.assertEqual(asset.data["model3d_task"]["status"], "interrupted_unknown")


if __name__ == "__main__":
    unittest.main()
