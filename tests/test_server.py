from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen

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


if __name__ == "__main__":
    unittest.main()
