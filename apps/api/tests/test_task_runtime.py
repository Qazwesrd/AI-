import unittest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import HTTPException

from app.database import Base
from app.models.task_runtime import TaskRuntimeControl, TaskExecutionLease, TaskExecutionLog, TaskQueueEntry
from app.services.task_runtime import acquire_task_lease, release_task_lease, runtime_snapshot, enqueue_task, queue_snapshot


class TaskRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.Session()
        self.db.add(TaskRuntimeControl(id=1, max_concurrency=1, active_count=0))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()


    def test_queue_persists_and_orders_by_priority(self):
        low = enqueue_task(self.db, "media", 101, priority=2)
        high = enqueue_task(self.db, "analysis", 202, priority=10)
        items = queue_snapshot(self.db)["items"]
        self.assertEqual([item["task_id"] for item in items], [202, 101])
        self.assertEqual(items[0]["priority"], 10)

    def test_releasing_lease_promotes_highest_priority_queue_item(self):
        acquire_task_lease(self.db, "image", 1)
        enqueue_task(self.db, "media", 2, priority=3)
        enqueue_task(self.db, "analysis", 3, priority=15)
        release_task_lease(self.db, "image", 1, True)
        items = queue_snapshot(self.db)["items"]
        self.assertEqual(items[0]["task_id"], 3)
        self.assertEqual(items[0]["status"], "ready_to_run")
        self.assertEqual(items[1]["status"], "waiting")

    def test_global_concurrency_limit_is_enforced(self):
        acquire_task_lease(self.db, "image", 1)
        with self.assertRaises(HTTPException) as raised:
            acquire_task_lease(self.db, "image", 2)
        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(runtime_snapshot(self.db)["active_count"], 1)


    def test_media_and_analysis_share_global_capacity(self):
        acquire_task_lease(self.db, "media", 20, priority=5)
        with self.assertRaises(HTTPException) as raised:
            acquire_task_lease(self.db, "analysis", 30, priority=10)
        self.assertEqual(raised.exception.status_code, 429)
        snapshot = runtime_snapshot(self.db)
        self.assertEqual(snapshot["active_count"], 1)
        self.assertEqual(snapshot["leases"][0]["source"], "media")

    def test_duplicate_execution_is_rejected(self):
        acquire_task_lease(self.db, "image", 3)
        with self.assertRaises(HTTPException) as raised:
            acquire_task_lease(self.db, "image", 3)
        self.assertEqual(raised.exception.status_code, 409)

    def test_release_records_duration_and_allows_retry(self):
        acquire_task_lease(self.db, "image", 4)
        release_task_lease(self.db, "image", 4, True)
        snapshot = runtime_snapshot(self.db)
        self.assertEqual(snapshot["active_count"], 0)
        self.assertEqual(snapshot["available_slots"], 1)
        acquire_task_lease(self.db, "image", 4)
        self.assertEqual(runtime_snapshot(self.db)["active_count"], 1)

    def test_failure_is_logged_without_auto_retry(self):
        acquire_task_lease(self.db, "image", 5)
        release_task_lease(self.db, "image", 5, False, "provider timeout")
        rows = self.db.query(TaskExecutionLog).filter(TaskExecutionLog.task_id == 5).all()
        self.assertEqual([row.event for row in rows], ["started", "failed"])
        self.assertEqual(rows[-1].message, "provider timeout")
        lease = self.db.query(TaskExecutionLease).filter(TaskExecutionLease.task_id == 5).first()
        self.assertEqual(lease.status, "failed")


if __name__ == "__main__":
    unittest.main()
