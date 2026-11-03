"""Tests for scheduled_research models and store (SQLite)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.scheduled_research.models import JobStatus, ScheduledResearchJob
from strategy_research.kernel.scheduled_research.store import ScheduledResearchStore


class TestJobStatus(unittest.TestCase):

    def test_values(self) -> None:
        self.assertEqual(JobStatus.PENDING.value, "pending")
        self.assertEqual(JobStatus.RUNNING.value, "running")
        self.assertEqual(JobStatus.COMPLETED.value, "completed")
        self.assertEqual(JobStatus.FAILED.value, "failed")
        self.assertEqual(JobStatus.CANCELLED.value, "cancelled")

    def test_enum_membership(self) -> None:
        self.assertIn(JobStatus.PENDING, JobStatus)


class TestScheduledResearchJob(unittest.TestCase):

    def test_default_id_generated(self) -> None:
        job = ScheduledResearchJob()
        self.assertTrue(job.id.startswith("job_"))

    def test_default_created_at(self) -> None:
        job = ScheduledResearchJob()
        self.assertGreater(job.created_at, 0)

    def test_default_target_is_study(self) -> None:
        self.assertEqual(ScheduledResearchJob().target, "study")

    def test_to_dict(self) -> None:
        job = ScheduledResearchJob(
            id="job_001",
            workspace="/ws",
            strategy_name="test_strat",
            cron="0 2 * * *",
            status=JobStatus.PENDING,
            target="study",
            owner_session_id="sess_1",
        )
        d = job.to_dict()
        self.assertEqual(d["id"], "job_001")
        self.assertEqual(d["status"], "pending")
        self.assertEqual(d["cron"], "0 2 * * *")
        self.assertEqual(d["target"], "study")
        self.assertEqual(d["owner_session_id"], "sess_1")

    def test_from_dict(self) -> None:
        data = {
            "id": "job_002",
            "workspace": "/ws",
            "strategy_name": "s2",
            "cron": "*/30 * * * *",
            "status": "running",
            "config": {"key": "val"},
            "target": "study",
            "owner_session_id": "sess_2",
        }
        job = ScheduledResearchJob.from_dict(data)
        self.assertEqual(job.id, "job_002")
        self.assertEqual(job.status, JobStatus.RUNNING)
        self.assertEqual(job.config["key"], "val")
        self.assertEqual(job.target, "study")
        self.assertEqual(job.owner_session_id, "sess_2")

    def test_from_dict_missing_fields(self) -> None:
        job = ScheduledResearchJob.from_dict({})
        self.assertIsInstance(job.id, str)
        self.assertEqual(job.status, JobStatus.PENDING)
        self.assertEqual(job.target, "study")
        self.assertIsNone(job.owner_session_id)

    def test_is_due_pending(self) -> None:
        job = ScheduledResearchJob(next_run_at=100.0, status=JobStatus.PENDING)
        self.assertTrue(job.is_due(now=200.0))

    def test_is_due_not_due(self) -> None:
        job = ScheduledResearchJob(next_run_at=300.0, status=JobStatus.PENDING)
        self.assertFalse(job.is_due(now=200.0))

    def test_is_due_running_not_due(self) -> None:
        job = ScheduledResearchJob(next_run_at=100.0, status=JobStatus.RUNNING)
        self.assertFalse(job.is_due(now=200.0))

    def test_is_due_completed(self) -> None:
        job = ScheduledResearchJob(
            next_run_at=100.0, status=JobStatus.COMPLETED
        )
        self.assertTrue(job.is_due(now=200.0))

    def test_is_due_failed(self) -> None:
        job = ScheduledResearchJob(next_run_at=100.0, status=JobStatus.FAILED)
        self.assertFalse(job.is_due(now=200.0))

    def test_is_due_cancelled(self) -> None:
        job = ScheduledResearchJob(next_run_at=100.0, status=JobStatus.CANCELLED)
        self.assertFalse(job.is_due(now=200.0))

    def test_is_due_no_next_run(self) -> None:
        job = ScheduledResearchJob(next_run_at=0.0, status=JobStatus.PENDING)
        self.assertFalse(job.is_due(now=200.0))

    def test_is_recurring_cron(self) -> None:
        job = ScheduledResearchJob(cron="0 * * * *")
        self.assertTrue(job.is_recurring())

    def test_is_recurring_interval(self) -> None:
        job = ScheduledResearchJob(interval_ms=3600000)
        self.assertTrue(job.is_recurring())

    def test_is_recurring_one_shot(self) -> None:
        job = ScheduledResearchJob()
        self.assertFalse(job.is_recurring())

    def test_study_params_defaults(self) -> None:
        job = ScheduledResearchJob(max_rounds=5)
        p = job.study_params()
        self.assertEqual(p["max_rounds"], 5)
        self.assertIsNone(p["metric_targets"])
        self.assertEqual(p["cooldown_base"], 30.0)

    def test_study_params_from_config(self) -> None:
        job = ScheduledResearchJob(
            max_rounds=3,
            config={
                "metric_targets": [{"name": "calmar", "op": ">=", "value": 0.5}],
                "budget_turn": 20,
                "guidance_md": "# gate\n",
                "monitor_interval_seconds": 3600,
                "behavior": "stub",
            },
        )
        p = job.study_params()
        self.assertEqual(p["metric_targets"][0]["name"], "calmar")
        self.assertEqual(p["budget_turn"], 20)
        self.assertEqual(p["guidance_md"], "# gate\n")
        self.assertEqual(p["monitor_interval_seconds"], 3600)
        self.assertEqual(p["behavior"], "stub")
        # config 显式值优先于 max_rounds 字段
        self.assertEqual(p["max_rounds"], 3)


class TestScheduledResearchStore(unittest.TestCase):

    def setUp(self) -> None:
        self.tmpdir = TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "goals.db"
        self.store = ScheduledResearchStore(path=self.db_path)

    def tearDown(self) -> None:
        self.store.close()
        self.tmpdir.cleanup()

    def test_load_empty(self) -> None:
        self.assertEqual(self.store.load(), [])

    def test_add_and_load(self) -> None:
        job = ScheduledResearchJob(workspace="/ws", strategy_name="s1")
        self.store.add(job)
        jobs = self.store.load()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].workspace, "/ws")

    def test_get_found(self) -> None:
        job = ScheduledResearchJob(id="find_me", workspace="/ws")
        self.store.add(job)
        found = self.store.get("find_me")
        self.assertIsNotNone(found)
        self.assertEqual(found.workspace, "/ws")

    def test_get_not_found(self) -> None:
        self.assertIsNone(self.store.get("nonexistent"))

    def test_update(self) -> None:
        job = ScheduledResearchJob(id="upd", workspace="/ws")
        self.store.add(job)
        job.workspace = "/new_ws"
        self.store.update(job)
        found = self.store.get("upd")
        self.assertEqual(found.workspace, "/new_ws")

    def test_update_not_found_raises(self) -> None:
        job = ScheduledResearchJob(id="ghost", workspace="/ws")
        with self.assertRaises(KeyError):
            self.store.update(job)

    def test_delete(self) -> None:
        job = ScheduledResearchJob(id="del_me", workspace="/ws")
        self.store.add(job)
        self.assertTrue(self.store.delete("del_me"))
        self.assertIsNone(self.store.get("del_me"))

    def test_delete_not_found(self) -> None:
        self.assertFalse(self.store.delete("nonexistent"))

    def test_list_jobs_no_filter(self) -> None:
        self.store.add(ScheduledResearchJob(workspace="/ws1"))
        self.store.add(ScheduledResearchJob(workspace="/ws2"))
        self.assertEqual(len(self.store.list_jobs()), 2)

    def test_list_jobs_filter_workspace(self) -> None:
        self.store.add(ScheduledResearchJob(workspace="/ws1"))
        self.store.add(ScheduledResearchJob(workspace="/ws2"))
        filtered = self.store.list_jobs(workspace="/ws1")
        self.assertEqual(len(filtered), 1)

    def test_list_jobs_filter_status(self) -> None:
        j1 = ScheduledResearchJob(workspace="/ws", status=JobStatus.PENDING)
        j2 = ScheduledResearchJob(workspace="/ws", status=JobStatus.COMPLETED)
        self.store.add(j1)
        self.store.add(j2)
        filtered = self.store.list_jobs(status=JobStatus.PENDING)
        self.assertEqual(len(filtered), 1)

    def test_list_jobs_filter_owner(self) -> None:
        j1 = ScheduledResearchJob(workspace="/ws", owner_session_id="me")
        j2 = ScheduledResearchJob(workspace="/ws", owner_session_id="other")
        self.store.add(j1)
        self.store.add(j2)
        filtered = self.store.list_jobs(owner_session_id="me")
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].id, j1.id)

    def test_roundtrip_config_json(self) -> None:
        job = ScheduledResearchJob(
            id="cfg", workspace="/ws",
            config={"guidance_md": "# gate\n", "nested": {"a": [1, 2]}},
            target="study",
        )
        self.store.add(job)
        found = self.store.get("cfg")
        self.assertEqual(found.config, job.config)
        self.assertEqual(found.target, "study")

    def test_recover_stale_running(self) -> None:
        job = ScheduledResearchJob(workspace="/ws", status=JobStatus.RUNNING)
        self.store.add(job)
        count = self.store.recover_stale_running()
        self.assertEqual(count, 1)
        recovered = self.store.get(job.id)
        self.assertEqual(recovered.status, JobStatus.PENDING)

    def test_recover_no_stale(self) -> None:
        job = ScheduledResearchJob(workspace="/ws", status=JobStatus.PENDING)
        self.store.add(job)
        count = self.store.recover_stale_running()
        self.assertEqual(count, 0)


class TestJsonMigration(unittest.TestCase):
    """Legacy JSON → SQLite migration."""

    def setUp(self) -> None:
        self.tmpdir = TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "goals.db"
        self.json_path = Path(self.tmpdir.name) / "scheduled_jobs.json"
        self.store = ScheduledResearchStore(path=self.db_path)

    def tearDown(self) -> None:
        self.store.close()
        self.tmpdir.cleanup()

    def _write_legacy(self, jobs: list[dict]) -> None:
        self.json_path.write_text(
            json.dumps({"schema_version": 1, "jobs": jobs}, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_migrate_imports_and_renames(self) -> None:
        self._write_legacy([
            {"id": "legacy_1", "workspace": "/ws", "strategy_name": "s1",
             "prompt": "研究动量", "cron": "0 2 * * *", "next_run_at": 100.0,
             "created_at": 50.0, "status": "pending", "max_rounds": 1},
        ])
        count = self.store.migrate_from_json(self.json_path)
        self.assertEqual(count, 1)
        self.assertFalse(self.json_path.exists())
        self.assertTrue(Path(f"{self.json_path}.migrated").exists())
        job = self.store.get("legacy_1")
        self.assertIsNotNone(job)
        self.assertEqual(job.prompt, "研究动量")
        self.assertEqual(job.target, "study")  # 存量统一到 study

    def test_migrate_legacy_autoresearch_target_unified(self) -> None:
        self._write_legacy([
            {"id": "legacy_2", "workspace": "/ws", "strategy_name": "s2",
             "cron": "0 2 * * *", "next_run_at": 100.0, "created_at": 50.0,
             "status": "pending", "max_rounds": 1, "target": "autoresearch"},
        ])
        self.store.migrate_from_json(self.json_path)
        job = self.store.get("legacy_2")
        self.assertEqual(job.target, "study")

    def test_migrate_idempotent_missing_file(self) -> None:
        count = self.store.migrate_from_json(self.json_path)
        self.assertEqual(count, 0)

    def test_migrate_renamed_file_idempotent(self) -> None:
        self._write_legacy([
            {"id": "l3", "workspace": "/ws", "strategy_name": "s3",
             "next_run_at": 100.0, "created_at": 50.0, "status": "pending",
             "max_rounds": 1},
        ])
        self.assertEqual(self.store.migrate_from_json(self.json_path), 1)
        # 第二次调用：文件已不存在 → 0，不重放
        self.assertEqual(self.store.migrate_from_json(self.json_path), 0)
        self.assertEqual(len(self.store.load()), 1)

    def test_migrate_corrupt_file_renamed(self) -> None:
        self.json_path.write_text("not json", encoding="utf-8")
        count = self.store.migrate_from_json(self.json_path)
        self.assertEqual(count, 0)
        self.assertFalse(self.json_path.exists())
        self.assertTrue(
            any(".corrupt-" in p.name
                for p in self.json_path.parent.iterdir())
        )

    def test_migrate_missing_optional_fields(self) -> None:
        self._write_legacy([
            {"id": "l4", "workspace": "/ws", "strategy_name": "s4",
             "next_run_at": 100.0, "created_at": 50.0, "status": "pending",
             "max_rounds": 1, "owner_session_id": "sess_x"},
        ])
        self.store.migrate_from_json(self.json_path)
        job = self.store.get("l4")
        self.assertIsNotNone(job)
        self.assertEqual(job.owner_session_id, "sess_x")


if __name__ == "__main__":
    unittest.main()
