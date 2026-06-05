from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cronpot.analytics import analyse_vault, build_shopping_list
from cronpot.models import Recipe
from cronpot.vault import write_recipe_to_vault


class AnalyticsTests(unittest.TestCase):
    def test_analyses_tags_categories_and_ingredients(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            write_recipe_to_vault(
                Recipe(
                    title="Apple Cake",
                    ingredients=["2 apples", "100g flour"],
                    steps=["Bake."],
                    tags=["dessert", "cake", "parev"],
                    categories=["Desserts", "Cakes"],
                ),
                vault,
            )
            write_recipe_to_vault(
                Recipe(
                    title="Apple Sauce",
                    ingredients=["2 apples"],
                    steps=["Cook."],
                    tags=["condiment", "parev"],
                    categories=["Condiments"],
                ),
                vault,
            )

            analytics = analyse_vault(vault)

            self.assertEqual(analytics.recipe_count, 2)
            self.assertEqual(analytics.tag_counts["parev"], 2)
            self.assertEqual(analytics.category_counts["Desserts"], 1)
            self.assertEqual(analytics.ingredient_counts["apples"], 2)

    def test_builds_deduplicated_shopping_list(self) -> None:
        items = build_shopping_list(
            [
                Recipe(title="One", ingredients=["2 apples", "salt"]),
                Recipe(title="Two", ingredients=["2 apples", "pepper"]),
            ]
        )

        self.assertEqual(items, ["2 apples", "salt", "pepper"])


if __name__ == "__main__":
    unittest.main()
