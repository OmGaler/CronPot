from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from cronpot.config import AutomationConfig
from cronpot.models import Recipe
from cronpot.server import CronPotHandler
from cronpot.vault import write_recipe_to_vault


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp_dir.name)
        write_recipe_to_vault(
            Recipe(
                title="Aglio e Olio",
                ingredients=["100g spaghetti", "1 tbsp olive oil"],
                steps=["Boil the pasta.", "Toss with oil."],
                tags=["main", "parev"],
                categories=["Mains"],
                source="https://example.com/aglio",
            ),
            self.vault,
        )
        write_recipe_to_vault(
            Recipe(
                title="Roast Chicken",
                ingredients=["1 chicken", "salt"],
                steps=["Roast until cooked."],
                tags=["main", "meaty"],
                categories=["Mains"],
            ),
            self.vault,
        )

        CronPotHandler.vault_path = self.vault
        CronPotHandler.config = AutomationConfig()
        CronPotHandler.pairing_code = ""
        CronPotHandler.session_tokens = set()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), CronPotHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    def test_lists_recipes(self) -> None:
        payload = self.get_json("/recipes")

        self.assertEqual(payload["count"], 2)
        self.assertEqual({recipe["name"] for recipe in payload["recipes"]}, {"Aglio e Olio", "Roast Chicken"})

    def test_serves_dashboard(self) -> None:
        text = self.get_text("/")

        self.assertIn("<title>CronPot Dashboard</title>", text)
        self.assertIn('href="/favicon.svg"', text)
        self.assertIn('src="/assets/cronpot-logo.svg"', text)
        self.assertIn("Service online", text)
        self.assertIn("Aglio e Olio", text)
        self.assertIn("Top ingredients", text)
        self.assertIn("Ingest jobs", text)
        self.assertIn('class="fill" style="width:', text)
        self.assertIn("background: #", text)

    def test_dashboard_uses_configured_llm_aliases_with_cache(self) -> None:
        write_recipe_to_vault(
            Recipe(
                title="Cake",
                ingredients=["100g extra fine sugar", "50g sugar"],
                steps=["Bake."],
                tags=["dessert", "parev"],
                categories=["Desserts"],
            ),
            self.vault,
        )
        CronPotHandler.config = AutomationConfig(llm_auto_normalise_ingredients=True)

        with patch("cronpot.server.suggest_ingredient_alias_map", return_value={"extra fine sugar": "sugar"}) as suggest:
            first = self.get_text("/dashboard")
            second = self.get_text("/dashboard")

        self.assertIn("sugar", first)
        self.assertIn("sugar", second)
        suggest.assert_called_once()

    def test_filters_recipes_by_tag_and_category(self) -> None:
        payload = self.get_json("/recipes?tag=meaty&category=Mains")

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["recipes"][0]["name"], "Roast Chicken")

    def test_returns_recipe_detail_by_name(self) -> None:
        payload = self.get_json(f"/recipes/{quote('Aglio e Olio')}")

        self.assertEqual(payload["title"], "Aglio e Olio")
        self.assertEqual(payload["ingredients"], ["100g spaghetti", "1 tbsp olive oil"])
        self.assertEqual(payload["steps"], ["Boil the pasta.", "Toss with oil."])
        self.assertEqual(payload["source"], "https://example.com/aglio")

    def test_renders_recipe_detail_for_browser_requests(self) -> None:
        text = self.get_html(f"/recipes/{quote('Aglio e Olio')}")

        self.assertIn("<title>Aglio e Olio</title>", text)
        self.assertIn('alt="CronPot logo"', text)
        self.assertIn("<h2>Aglio e Olio</h2>", text)
        self.assertIn("<li>100g spaghetti</li>", text)
        self.assertIn('href="/dashboard"', text)

    def test_serves_logo_asset_for_favicon_and_ui(self) -> None:
        request = Request(f"{self.base_url}/assets/cronpot-logo.svg")
        with urlopen(request, timeout=5) as response:
            body = response.read().decode("utf-8")

        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Content-Type"], "image/svg+xml")
        self.assertIn("<svg", body)

    def test_rejects_missing_recipe_detail(self) -> None:
        with self.assertRaises(HTTPError) as error:
            self.get_json("/recipes/Nope")

        self.assertEqual(error.exception.code, 404)

    def test_builds_shopping_list_for_selected_recipes(self) -> None:
        payload = self.get_json(f"/shopping-list?recipe={quote('Aglio e Olio')}&recipe={quote('Roast Chicken')}")

        self.assertEqual(payload["count"], 4)
        self.assertEqual(payload["items"], ["100g spaghetti", "1 tbsp olive oil", "1 chicken", "salt"])
        self.assertEqual([recipe["name"] for recipe in payload["recipes"]], ["Aglio e Olio", "Roast Chicken"])

    def test_shopping_list_requires_selection(self) -> None:
        with self.assertRaises(HTTPError) as error:
            self.get_json("/shopping-list")

        self.assertEqual(error.exception.code, 400)

    def test_post_ingest_rewrites_recipe_when_configured(self) -> None:
        html = """
        <script type="application/ld+json">
        {"@type":"Recipe","name":"messy soup","recipeIngredient":["one carrot"],"recipeInstructions":["you should chop it"]}
        </script>
        """
        CronPotHandler.config = AutomationConfig(llm_rewrite_ingested_recipes=True)
        rewritten = Recipe(
            title="Carrot Soup",
            ingredients=["1 carrot"],
            steps=["Chop the carrot."],
            tags=["starter", "parev"],
            categories=["Soups"],
            source="https://example.com/soup",
        )

        with patch("cronpot.server.fetch_html", return_value=html), patch(
            "cronpot.ingest.rewrite_recipe_to_vault_style",
            return_value=rewritten,
        ) as rewrite:
            payload = self.post_json("/ingest", {"url": "https://example.com/soup"})

        self.assertEqual(payload["title"], "Carrot Soup")
        self.assertTrue((self.vault / "Carrot Soup.md").exists())
        rewrite.assert_called_once()

    def test_post_background_ingest_queues_job_and_run_endpoint_processes_it(self) -> None:
        html = """
        <script type="application/ld+json">
        {"@type":"Recipe","name":"Queued Soup","recipeIngredient":["1 carrot"],"recipeInstructions":["chop it"]}
        </script>
        """

        queued = self.post_json("/jobs/ingest", {"url": "https://example.com/queued-soup"})
        self.assertEqual(queued["status"], "pending")

        with patch("cronpot.jobs.fetch_html", return_value=html):
            run = self.post_json("/jobs/run", {})

        self.assertEqual(run["jobs"][0]["status"], "complete")
        detail = self.get_json(f"/jobs/{queued['id']}")
        self.assertEqual(detail["title"], "Queued Soup")

    def test_retry_job_endpoint_sets_failed_job_pending(self) -> None:
        queued = self.post_json("/jobs/ingest", {"url": "https://example.com/failing"})
        with patch("cronpot.jobs.fetch_html", side_effect=OSError("network down")):
            self.post_json("/jobs/run", {})

        retried = self.post_json(f"/jobs/{queued['id']}/retry", {})

        self.assertEqual(retried["status"], "pending")
        self.assertEqual(retried["error"], "")

    def test_clear_jobs_endpoint_removes_all_jobs(self) -> None:
        self.post_json("/jobs/ingest", {"url": "https://example.com/one"})
        self.post_json("/jobs/ingest", {"url": "https://example.com/two"})

        cleared = self.post_json("/jobs/clear", {})
        jobs = self.get_json("/jobs")

        self.assertEqual(cleared["cleared"], 2)
        self.assertEqual(jobs["jobs"], [])

    def test_pairing_code_protects_api_until_mobile_authenticates(self) -> None:
        CronPotHandler.pairing_code = "123456"

        with self.assertRaises(HTTPError) as error:
            self.get_json("/recipes")

        self.assertEqual(error.exception.code, 401)

        auth, cookie = self.post_json_with_cookie("/auth", {"code": "123456"})
        self.assertEqual(auth["authenticated"], True)

        payload = self.get_json("/recipes", headers={"Cookie": cookie})
        self.assertEqual(payload["count"], 2)

    def test_pairing_code_allows_bearer_code_for_api_clients(self) -> None:
        CronPotHandler.pairing_code = "654321"

        payload = self.get_json("/recipes", headers={"Authorization": "Bearer 654321"})

        self.assertEqual(payload["count"], 2)

    def test_serves_mobile_pairing_and_app_ui(self) -> None:
        CronPotHandler.pairing_code = "123456"

        text = self.get_text("/mobile")

        self.assertIn("Pair this device", text)
        self.assertIn("Queue recipe ingest", text)
        self.assertIn("Vault sync", text)
        self.assertIn("Run jobs", text)
        self.assertIn("Clear jobs", text)
        self.assertIn("retryJob", text)
        self.assertIn('aria-label="Retry job"', text)
        self.assertIn("icon-button retry-job", text)
        self.assertIn("Command tips", text)
        self.assertIn("cronpot k8s github pull", text)
        self.assertIn("Shopping list", text)

    def test_mobile_k8s_pull_runs_cli_command(self) -> None:
        with patch(
            "cronpot.server.subprocess.run",
            return_value=subprocess.CompletedProcess(["python"], 0, "Pulled vault", ""),
        ) as run:
            payload = self.post_json("/k8s/github/pull", {})

        self.assertEqual(payload["status"], "complete")
        self.assertEqual(payload["direction"], "pull")
        self.assertEqual(payload["namespace"], "cronpot-local")
        self.assertIn("Pulled vault", payload["output"])
        self.assertEqual(run.call_args.args[0][2:], ["cronpot", "k8s", "github", "pull", "--namespace", "cronpot-local"])

    def test_mobile_k8s_sync_reports_cluster_errors(self) -> None:
        with patch(
            "cronpot.server.subprocess.run",
            return_value=subprocess.CompletedProcess(["python"], 1, "", "connection refused"),
        ) as run:
            with self.assertRaises(HTTPError) as error:
                self.post_json("/k8s/github/push", {})

        self.assertEqual(error.exception.code, 502)
        self.assertEqual(run.call_args.args[0][2:], ["cronpot", "k8s", "github", "push", "--namespace", "cronpot-local", "--seed-from", str(self.vault)])
        body = json.loads(error.exception.read().decode("utf-8"))
        self.assertIn("Could not push the GitHub vault", body["error"])
        self.assertIn("cluster is running", body["error"])
        self.assertIn("connection refused", body["output"])

    def get_json(self, path: str, headers: dict[str, str] | None = None) -> dict[str, object]:
        request = Request(f"{self.base_url}{path}", headers=headers or {})
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def get_text(self, path: str) -> str:
        with urlopen(f"{self.base_url}{path}", timeout=5) as response:
            self.assertEqual(response.headers["Content-Type"], "text/html; charset=utf-8")
            return response.read().decode("utf-8")

    def get_html(self, path: str) -> str:
        request = Request(f"{self.base_url}{path}", headers={"Accept": "text/html"})
        with urlopen(request, timeout=5) as response:
            self.assertEqual(response.headers["Content-Type"], "text/html; charset=utf-8")
            return response.read().decode("utf-8")

    def post_json(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        result, _cookie = self.post_json_with_cookie(path, payload)
        return result

    def post_json_with_cookie(self, path: str, payload: dict[str, object]) -> tuple[dict[str, object], str]:
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            cookie = response.headers.get("Set-Cookie", "").split(";", 1)[0]
            return json.loads(response.read().decode("utf-8")), cookie


if __name__ == "__main__":
    unittest.main()
