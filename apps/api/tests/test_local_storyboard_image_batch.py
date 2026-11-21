import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from pydantic import ValidationError

from app.routers.local_image_generation import (
    LocalImageBatchInput,
    _prepare_batch_shots,
)


class LocalStoryboardImageBatchTests(unittest.TestCase):
    def test_batch_limits_and_rejects_duplicate_shots(self):
        with self.assertRaises(ValidationError):
            LocalImageBatchInput(
                project_id=1, episode_id=2, shot_ids=[3, 3],
                checkpoint="local.safetensors",
            )
        with self.assertRaises(ValidationError):
            LocalImageBatchInput(
                project_id=1, episode_id=2, shot_ids=list(range(1, 22)),
                checkpoint="local.safetensors",
            )

    def test_preflight_separates_ready_missing_and_unowned_shots(self):
        data = LocalImageBatchInput(
            project_id=1, episode_id=2, shot_ids=[10, 11, 12],
            checkpoint="local.safetensors",
        )
        ready = SimpleNamespace(
            id=10, shot_number=1, scene_name="神殿", image_prompt="少年站在神殿前"
        )
        missing_prompt = SimpleNamespace(
            id=11, shot_number=2, scene_name="走廊", image_prompt=""
        )
        query = MagicMock()
        query.filter.return_value.all.return_value = [ready, missing_prompt]
        db = MagicMock()
        db.query.return_value = query

        rows, missing = _prepare_batch_shots(data, db)

        self.assertEqual([row["shot_id"] for row in rows], [10])
        self.assertEqual([item["shot_id"] for item in missing], [11, 12])
        self.assertIn("尚未填写", missing[0]["reason"])
        self.assertIn("不属于", missing[1]["reason"])

    def test_preflight_does_not_start_generation(self):
        data = LocalImageBatchInput(
            project_id=1, episode_id=2, shot_ids=[10],
            checkpoint="local.safetensors",
        )
        query = MagicMock()
        query.filter.return_value.all.return_value = [
            SimpleNamespace(id=10, shot_number=1, scene_name="", image_prompt="一座雪山")
        ]
        db = MagicMock()
        db.query.return_value = query

        rows, missing = _prepare_batch_shots(data, db)

        self.assertEqual(len(rows), 1)
        self.assertEqual(missing, [])
        self.assertFalse(data.confirm_local_generation)


if __name__ == "__main__":
    unittest.main()
