import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.routers.local_image_generation import (
    LocalImageBatchStatusInput,
    get_local_image_batch_status,
)


class LocalStoryboardImageBatchStatusTests(unittest.TestCase):
    def test_rejects_invalid_prompt_ids(self):
        with self.assertRaises(ValidationError):
            LocalImageBatchStatusInput(tasks=[
                {"shot_id": 1, "shot_number": 1, "prompt_id": "not-a-comfyui-id"}
            ])

    def test_aggregates_completed_running_and_failed_tasks(self):
        first = "a" * 32
        second = "b" * 32
        third = "c" * 32
        data = LocalImageBatchStatusInput(tasks=[
            {"shot_id": 1, "shot_number": 1, "prompt_id": first},
            {"shot_id": 2, "shot_number": 2, "prompt_id": second},
            {"shot_id": 3, "shot_number": 3, "prompt_id": third},
        ])

        def fake_request(path, **kwargs):
            prompt_id = path.rsplit("/", 1)[-1]
            if prompt_id == first:
                return {first: {"status": {"completed": True, "status_str": "success"}, "outputs": {}}}
            if prompt_id == second:
                return {}
            return {third: {"status": {"completed": True, "status_str": "error"}, "outputs": {}}}

        with patch("app.routers.local_image_generation._local_request", side_effect=fake_request):
            result = get_local_image_batch_status(data)

        self.assertEqual([item["status"] for item in result["tasks"]], ["completed", "queued_or_running", "failed"])
        self.assertEqual(result["completed_count"], 1)
        self.assertEqual(result["running_count"], 1)
        self.assertEqual(result["failed_count"], 1)


if __name__ == "__main__":
    unittest.main()
