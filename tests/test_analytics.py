from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cronpot.analytics import _pdf_browser_path, analyse_vault, build_shopping_list, html_cookbook, pdf_cookbook
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

    def test_groups_common_ingredient_aliases_for_analytics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            write_recipe_to_vault(
                Recipe(
                    title="Cake",
                    ingredients=[
                        "100g granulated sugar",
                        "2 tablespoons white sugar, divided",
                        "1 cup sugar",
                        "1 tsp sea salt",
                        "fine salt to taste",
                        "salt and pepper",
                        "freshly ground black pepper",
                        "1 egg",
                        "2 eggs",
                        "100g flour",
                        "100g plain flour",
                    ],
                    steps=["Bake."],
                    tags=["dessert", "parev"],
                    categories=["Desserts"],
                ),
                vault,
            )

            analytics = analyse_vault(vault)

            self.assertEqual(analytics.ingredient_counts["sugar"], 3)
            self.assertEqual(analytics.ingredient_counts["salt"], 3)
            self.assertEqual(analytics.ingredient_counts["pepper"], 2)
            self.assertEqual(analytics.ingredient_counts["eggs"], 2)
            self.assertEqual(analytics.ingredient_counts["plain flour"], 2)
            self.assertNotIn("granulated sugar", analytics.ingredient_counts)
            self.assertNotIn("white sugar divided", analytics.ingredient_counts)
            self.assertNotIn("egg", analytics.ingredient_counts)
            self.assertNotIn("flour", analytics.ingredient_counts)

    def test_applies_external_ingredient_aliases_for_analytics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            write_recipe_to_vault(
                Recipe(
                    title="Cake",
                    ingredients=["100g extra fine sugar", "50g sugar"],
                    steps=["Bake."],
                    tags=["dessert", "parev"],
                    categories=["Desserts"],
                ),
                vault,
            )

            analytics = analyse_vault(vault, ingredient_aliases={"extra fine sugar": "sugar"})

            self.assertEqual(analytics.ingredient_counts["sugar"], 2)
            self.assertNotIn("extra fine sugar", analytics.ingredient_counts)

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

    def test_builds_pdf_cookbook(self) -> None:
        output = pdf_cookbook(
            [
                (
                    Path("Aglio e Olio.md"),
                    Recipe(
                        title="Aglio e Olio",
                        ingredients=["100g spaghetti"],
                        steps=["Boil the pasta."],
                        tags=["parev"],
                        categories=["Mains"],
                    ),
                )
            ]
        )

        self.assertTrue(output.startswith(b"%PDF-"))
        self.assertTrue(output.rstrip().endswith(b"%%EOF"))

    def test_finds_linux_browser_for_pdf_export(self) -> None:
        with patch("cronpot.analytics.Path.exists", return_value=False), patch(
            "cronpot.analytics.shutil.which", side_effect=lambda command: "/usr/bin/google-chrome" if command == "google-chrome" else None
        ):
            browser = _pdf_browser_path()

        self.assertEqual(browser, Path("/usr/bin/google-chrome"))


if __name__ == "__main__":
    unittest.main()
