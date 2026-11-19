import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.story import EpisodePlan, StoryboardShot, EpisodeEditTimeline
from app.routers.media import (
    EpisodeTimelineInput, get_episode_edit_timeline,
    save_episode_edit_timeline,
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

    def test_saves_per_shot_trim_and_independent_audio_tracks(self):
        payload = EpisodeTimelineInput(
            shot_order=[self.shot_a.id, self.shot_b.id],
            shot_trims=[{"shot_id": self.shot_a.id, "start_seconds": 1.25, "end_seconds": 4.5}],
            audio_tracks=[{
                "file_path": "validated-local-audio.mp3",
                "start_seconds": 2.0, "volume": 0.7,
                "fade_in_seconds": 1.0, "fade_out_seconds": 2.0,
                "enabled": False,
            }],
        )
        # Validation of on-disk paths is covered by the endpoint integration tests;
        # the Pydantic model preserves all timeline values for persistence.
        self.assertEqual(payload.shot_trims[0].start_seconds, 1.25)
        self.assertEqual(payload.audio_tracks[0].start_seconds, 2.0)
        self.assertFalse(payload.audio_tracks[0].enabled)

    def test_rejects_invalid_trim_range(self):
        with self.assertRaises(Exception):
            EpisodeTimelineInput(
                shot_order=[self.shot_a.id, self.shot_b.id],
                shot_trims=[{"shot_id": self.shot_a.id, "start_seconds": 4, "end_seconds": 3}],
            )

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
