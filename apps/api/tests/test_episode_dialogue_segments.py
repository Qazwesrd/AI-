import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import HTTPException

from app.database import Base
from app.models.story import EpisodePlan, StoryboardShot, EpisodeEditTimeline
from app.models.visual import VisualAsset
from app.routers.media import EpisodeTimelineInput, get_episode_edit_timeline, save_episode_edit_timeline


class EpisodeDialogueSegmentTimelineTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine, tables=[
            EpisodePlan.__table__, StoryboardShot.__table__,
            EpisodeEditTimeline.__table__, VisualAsset.__table__,
        ])
        self.Session = sessionmaker(bind=engine)
        self.db = self.Session()
        episode = EpisodePlan(project_id=1, episode_number=1, title="测试分集", duration_seconds=100)
        self.db.add(episode)
        self.db.flush()
        self.episode_id = episode.id
        self.hero = VisualAsset(category="characters", name="主角", confirmed=True)
        self.rival = VisualAsset(category="characters", name="对手", confirmed=True)
        self.db.add_all([self.hero, self.rival])
        self.db.flush()
        self.shot = StoryboardShot(
            project_id=1, episode_id=episode.id, shot_number=1,
            sort_order=1, duration_seconds=8,
            character_ids=[self.hero.id, self.rival.id],
            dialogue="你终于来了。",
        )
        self.other = StoryboardShot(
            project_id=1, episode_id=episode.id, shot_number=2,
            sort_order=2, duration_seconds=5, character_ids=[self.hero.id],
        )
        self.db.add_all([self.shot, self.other])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_save_and_reload_multiple_speaker_lines(self):
        payload = EpisodeTimelineInput(
            shot_order=[self.shot.id, self.other.id],
            dialogue_segments_by_shot_id={str(self.shot.id): [
                {"speaker_character_id": self.hero.id, "text": "你终于来了。"},
                {"speaker_character_id": self.rival.id, "text": "我等你很久了。"},
            ]},
        )
        save_episode_edit_timeline(self.episode_id, payload, self.db)
        saved = get_episode_edit_timeline(self.episode_id, self.db)
        lines = saved["timeline"]["dialogue_segments_by_shot_id"][str(self.shot.id)]
        self.assertEqual([line["speaker_character_id"] for line in lines], [self.hero.id, self.rival.id])
        self.assertEqual(lines[1]["text"], "我等你很久了。")

    def test_rejects_speaker_not_linked_to_shot(self):
        payload = EpisodeTimelineInput(
            shot_order=[self.shot.id, self.other.id],
            dialogue_segments_by_shot_id={str(self.shot.id): [
                {"speaker_character_id": 99999, "text": "不属于本镜头角色"},
            ]},
        )
        with self.assertRaises(HTTPException) as caught:
            save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(caught.exception.status_code, 422)

    def test_requires_speaker_for_multi_character_dialogue(self):
        payload = EpisodeTimelineInput(
            shot_order=[self.shot.id, self.other.id],
            dialogue_segments_by_shot_id={str(self.shot.id): [
                {"text": "没有指定说话人"},
            ]},
        )
        with self.assertRaises(HTTPException) as caught:
            save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(caught.exception.status_code, 422)

    def test_rejects_blank_line(self):
        payload = EpisodeTimelineInput(
            shot_order=[self.shot.id, self.other.id],
            dialogue_segments_by_shot_id={str(self.shot.id): [
                {"speaker_character_id": self.hero.id, "text": "   "},
            ]},
        )
        with self.assertRaises(HTTPException) as caught:
            save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(caught.exception.status_code, 422)

    def test_empty_segment_list_keeps_original_dialogue_fallback(self):
        payload = EpisodeTimelineInput(
            shot_order=[self.shot.id, self.other.id],
            dialogue_segments_by_shot_id={str(self.shot.id): []},
        )
        saved = save_episode_edit_timeline(self.episode_id, payload, self.db)
        self.assertEqual(saved["timeline"]["dialogue_segments_by_shot_id"][str(self.shot.id)], [])


if __name__ == "__main__":
    unittest.main()
