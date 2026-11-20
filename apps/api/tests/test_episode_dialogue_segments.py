import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.story import EpisodePlan, StoryboardShot, EpisodeEditTimeline
from app.models.visual import VisualAsset
from app.routers.media import (
    EpisodeDialogueSegmentInput, EpisodeTimelineInput,
    get_episode_edit_timeline, save_episode_edit_timeline,
)
from fastapi import HTTPException


class EpisodeEditingTimelineTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(
            engine,
            tables=[
                EpisodePlan.__table__,
                StoryboardShot.__table__,
                EpisodeEditTimeline.__table__,
                VisualAsset.__table__,
            ],
        )
        self.Session = sessionmaker(bind=engine)
        self.db = self.Session()
        episode = EpisodePlan(
            project_id=1, episode_number=1, title="测试分集",
            duration_seconds=100,
        )
        self.db.add(episode)
        self.db.flush()
        self.episode_id = episode.id
        self.shot_a = StoryboardShot(
            project_id=1, episode_id=episode.id, shot_number=1,
            sort_order=1, duration_seconds=5,
        )
        self.shot_b = StoryboardShot(
            project_id=1, episode_id=episode.id, shot_number=2,
            sort_order=2, duration_seconds=6,
        )
        self.character = VisualAsset(
            category="characters", name="主角", confirmed=True,
        )
        self.db.add(self.character)
        self.db.flush()
        self.shot_a.character_ids = [self.character.id]
        self.db.add_all([self.shot_a, self.shot_b])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_default_timeline_uses_all_existing_shots(self):
        result = get_episode_edit_timeline(self.episode_id, self.db)
        self.assertEqual(result["timeline"]["shot_order"], [self.shot_a.id, self.shot_b.id])
        self.assertEqual(result["version"], 0)

    def test_save_and_reload_timeline_increments_version(self):
        payload = EpisodeTimelineInput(
            shot_order=[self.shot_b.id, self.shot_a.id],
            transitions=[{
                "after_shot_id": self.shot_b.id,
                "transition": "dissolve",
                "duration_seconds": 0.6,
            }],
            intro_title="序章",
            outro_title="未完待续",
            music_fade_in_seconds=1.5,
            music_fade_out_seconds=2.5,
            music_volume=0.2,
        )
        saved = save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(saved["version"], 1)
        reloaded = get_episode_edit_timeline(self.episode_id, self.db)
        self.assertEqual(reloaded["timeline"]["shot_order"], [self.shot_b.id, self.shot_a.id])
        self.assertEqual(reloaded["timeline"]["intro_title"], "序章")
        saved_again = save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(saved_again["version"], 2)

    def test_character_voice_profiles_are_saved_and_character_roster_is_returned(self):
        payload = EpisodeTimelineInput(
            shot_order=[self.shot_a.id, self.shot_b.id],
            character_voice_profiles={str(self.character.id): "nova"},
        )
        save_episode_edit_timeline(self.episode_id, payload, self.db)
        reloaded = get_episode_edit_timeline(self.episode_id, self.db)
        self.assertEqual(
            reloaded["timeline"]["character_voice_profiles"],
            {str(self.character.id): "nova"},
        )
        self.assertEqual(
            reloaded["characters"],
            [{"id": self.character.id, "name": "主角"}],
        )
        self.assertEqual(reloaded["shots"][0]["character_ids"], [self.character.id])

    def test_rejects_voice_profile_for_character_outside_episode(self):
        payload = EpisodeTimelineInput(
            shot_order=[self.shot_a.id, self.shot_b.id],
            character_voice_profiles={"99999": "nova"},
        )
        with self.assertRaises(HTTPException) as caught:
            save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(caught.exception.status_code, 422)

    def test_dialogue_segments_are_saved_and_reloaded_per_shot(self):
        second = VisualAsset(
            category="characters", name="对手", confirmed=True,
        )
        self.db.add(second)
        self.db.flush()
        self.shot_a.character_ids = [self.character.id, second.id]
        self.db.commit()
        payload = EpisodeTimelineInput(
            shot_order=[self.shot_a.id, self.shot_b.id],
            dialogue_segments_by_shot_id={
                str(self.shot_a.id): [
                    {"speaker_character_id": self.character.id, "text": "你是谁？"},
                    {"speaker_character_id": second.id, "text": "我是你的对手。"},
                ],
            },
        )
        save_episode_edit_timeline(self.episode_id, payload, self.db)
        reloaded = get_episode_edit_timeline(self.episode_id, self.db)
        segments = reloaded["timeline"]["dialogue_segments_by_shot_id"][str(self.shot_a.id)]
        self.assertEqual(segments[0]["speaker_character_id"], self.character.id)
        self.assertEqual(segments[1]["text"], "我是你的对手。")

    def test_rejects_dialogue_speaker_not_in_shot(self):
        payload = EpisodeTimelineInput(
            shot_order=[self.shot_a.id, self.shot_b.id],
            dialogue_segments_by_shot_id={
                str(self.shot_a.id): [
                    {"speaker_character_id": 99999, "text": "越权角色对白"},
                ],
            },
        )
        with self.assertRaises(HTTPException) as caught:
            save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(caught.exception.status_code, 422)

    def test_requires_speaker_for_each_line_in_multi_character_shot(self):
        second = VisualAsset(category="characters", name="对手", confirmed=True)
        self.db.add(second)
        self.db.flush()
        self.shot_a.character_ids = [self.character.id, second.id]
        self.db.commit()
        payload = EpisodeTimelineInput(
            shot_order=[self.shot_a.id, self.shot_b.id],
            dialogue_segments_by_shot_id={
                str(self.shot_a.id): [{"text": "没有指定说话人"}],
            },
        )
        with self.assertRaises(HTTPException) as caught:
            save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(caught.exception.status_code, 422)

    def test_rejects_blank_dialogue_segment_text(self):
        payload = EpisodeTimelineInput(
            shot_order=[self.shot_a.id, self.shot_b.id],
            dialogue_segments_by_shot_id={
                str(self.shot_a.id): [{"text": "   "}],
            },
        )
        with self.assertRaises(HTTPException) as caught:
            save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(caught.exception.status_code, 422)

    def test_empty_segment_list_falls_back_to_original_dialogue(self):
        payload = EpisodeTimelineInput(
            shot_order=[self.shot_a.id, self.shot_b.id],
            dialogue_segments_by_shot_id={str(self.shot_a.id): []},
        )
        saved = save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(saved["timeline"]["dialogue_segments_by_shot_id"][str(self.shot_a.id)], [])

    def test_rejects_missing_or_foreign_shots(self):
        payload = EpisodeTimelineInput(shot_order=[self.shot_a.id])
        with self.assertRaises(HTTPException) as caught:
            save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(caught.exception.status_code, 422)

    def test_rejects_duplicate_shot_ids(self):
        payload = EpisodeTimelineInput(shot_order=[self.shot_a.id, self.shot_a.id])
        with self.assertRaises(HTTPException) as caught:
            save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(caught.exception.status_code, 422)

    def test_rejects_cut_with_nonzero_duration(self):
        payload = EpisodeTimelineInput(
            shot_order=[self.shot_a.id, self.shot_b.id],
            transitions=[{
                "after_shot_id": self.shot_a.id,
                "transition": "cut",
                "duration_seconds": 0.5,
            }],
        )
        with self.assertRaises(HTTPException) as caught:
            save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(caught.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
