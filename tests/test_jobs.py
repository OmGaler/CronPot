from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cronpot.config import AutomationConfig
from cronpot.jobs import enqueue_ingest_job, get_job, list_jobs, run_pending_jobs


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


if __name__ == "__main__":
    unittest.main()
