from __future__ import annotations

import json
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
        self.assertIn("Service online", text)
        self.assertIn("Aglio e Olio", text)
        self.assertIn("Top ingredients", text)
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
        self.assertIn("<h2>Aglio e Olio</h2>", text)
        self.assertIn("<li>100g spaghetti</li>", text)
        self.assertIn('href="/dashboard"', text)

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

    def get_json(self, path: str) -> dict[str, object]:
        with urlopen(f"{self.base_url}{path}", timeout=5) as response:
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


if __name__ == "__main__":
    unittest.main()
