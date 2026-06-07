from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cronpot.analytics import analyse_vault, build_shopping_list, html_cookbook
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

    def test_builds_escaped_html_cookbook(self) -> None:
        output = html_cookbook(
            [
                (
                    Path("Fish & Chips.md"),
                    Recipe(
                        title="Fish & Chips",
                        ingredients=["1 <large> fish", "Oil & salt"],
                        steps=["Fry until crisp."],
                        tags=["parev"],
                        categories=["Mains"],
                        source='https://example.com/recipe?name=fish&note="hot"',
                    ),
                )
            ],
            title="Friday <Dinner>",
        )

        self.assertIn("<!doctype html>", output)
        self.assertIn("<title>Friday &lt;Dinner&gt;</title>", output)
        self.assertIn("<h2>Fish &amp; Chips</h2>", output)
        self.assertIn("<li>1 &lt;large&gt; fish</li>", output)
        self.assertIn("Oil &amp; salt", output)
        self.assertIn('href="https://example.com/recipe?name=fish&amp;note=&quot;hot&quot;"', output)
        self.assertNotIn("1 <large> fish", output)


if __name__ == "__main__":
    unittest.main()
