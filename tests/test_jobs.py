from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cronpot.config import AutomationConfig
from cronpot.jobs import enqueue_ingest_job, get_job, list_jobs, reset_stale_jobs, retry_job, run_pending_jobs


class JobTests(unittest.TestCase):
    def test_enqueues_and_processes_ingest_job(self) -> None:
        html = """
        <script type="application/ld+json">
        {"@type":"Recipe","name":"Soup","recipeIngredient":["1/2 tsp salt"],"recipeInstructions":["boil it"]}
        </script>
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            job = enqueue_ingest_job(vault, "https://example.com/soup")

            self.assertEqual(list_jobs(vault)[0].status, "pending")

            with patch("cronpot.jobs.fetch_html", return_value=html):
                processed = run_pending_jobs(vault, AutomationConfig(), workers=2)

            finished = get_job(vault, job.id)

        self.assertEqual(len(processed), 1)
        self.assertIsNotNone(finished)
        self.assertEqual(finished.status, "complete")
        self.assertEqual(finished.title, "Soup")

    def test_retry_failed_job_sets_it_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            job = enqueue_ingest_job(vault, "https://example.com/fail")
            with patch("cronpot.jobs.fetch_html", side_effect=OSError("network down")):
                run_pending_jobs(vault, AutomationConfig(), workers=1)

            failed = get_job(vault, job.id)
            retried = retry_job(vault, job.id)

        self.assertEqual(failed.status, "failed")
        self.assertEqual(retried.status, "pending")
        self.assertEqual(retried.error, "")

    def test_resets_stale_running_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            job = enqueue_ingest_job(vault, "https://example.com/stale")
            job_path = vault / ".cronpot" / "jobs" / f"{job.id}.json"
            payload = json.loads(job_path.read_text(encoding="utf-8"))
            payload["status"] = "running"
            payload["updated_at"] = 1
            job_path.write_text(json.dumps(payload), encoding="utf-8")

            reset = reset_stale_jobs(vault, stale_after_seconds=30)

        self.assertEqual(len(reset), 1)
        self.assertEqual(reset[0].status, "pending")


if __name__ == "__main__":
    unittest.main()
