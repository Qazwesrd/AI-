import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.models.media import MediaTask
from app.routers.local_image_generation import (
    LocalImageBatchInput,
    generate_local_image_batch,
)


class LocalImageBatchPersistenceTests(unittest.TestCase):
    def test_batch_submission_persists_each_accepted_comfyui_task(self):
        db = MagicMock()
        data = LocalImageBatchInput(
            project_id=7,
            episode_id=3,
            shot_ids=[21],
            checkpoint="local-checkpoint.safetensors",
            confirm_local_generation=True,
        )
        object_info = {
            "CheckpointLoaderSimple": {
                "input": {"required": {"ckpt_name": [["local-checkpoint.safetensors"]]}}
            }
        }
        prompt_id = "a" * 32
        with patch(
            "app.routers.local_image_generation._prepare_batch_shots",
            return_value=([{
                "shot_id": 21,
                "shot_number": 4,
                "scene_name": "夜景",
                "prompt": "主角站在街道中央",
            }], []),
        ), patch(
            "app.routers.local_image_generation._local_request",
            side_effect=[object_info, {"prompt_id": prompt_id}],
        ):
            result = generate_local_image_batch(data, db=db)

        self.assertEqual(result["submitted_count"], 1)
        self.assertEqual(result["tasks"][0]["prompt_id"], prompt_id)
        self.assertTrue(result["history_persisted"])
        db.add.assert_called_once()
        db.commit.assert_called_once()
        saved = db.add.call_args.args[0]
        self.assertIsInstance(saved, MediaTask)
        self.assertEqual(saved.task_type, "local_image_batch")
        self.assertEqual(saved.provider, "comfyui-local")
        self.assertEqual(saved.remote_task_id, prompt_id)
        self.assertEqual(saved.request_data["project_id"], 7)
        self.assertEqual(saved.request_data["episode_id"], 3)
        self.assertEqual(saved.request_data["shot_id"], 21)
        self.assertEqual(saved.request_data["shot_number"], 4)
        self.assertEqual(saved.result_data["prompt_id"], prompt_id)

    def test_batch_submission_still_requires_explicit_confirmation(self):
        db = MagicMock()
        data = LocalImageBatchInput(
            project_id=7,
            episode_id=3,
            shot_ids=[21],
            checkpoint="local-checkpoint.safetensors",
        )
        with self.assertRaises(Exception):
            generate_local_image_batch(data, db=db)
        db.add.assert_not_called()


if __name__ == "__main__":
    unittest.main()
