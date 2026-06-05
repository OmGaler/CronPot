from __future__ import annotations

import unittest

from cronpot.extraction import extract_recipe


class ExtractionTests(unittest.TestCase):
    def test_extracts_json_ld_recipe_with_durations(self) -> None:
        html = """
        <html><head>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "Recipe",
          "name": "Broiled Salmon",
          "recipeIngredient": ["2 scallions", "1 tsp cilantro"],
          "recipeInstructions": [{"@type": "HowToStep", "text": "Broil until cooked."}],
          "prepTime": "PT10M",
          "cookTime": "PT1H20M",
          "recipeYield": "4 servings"
        }
        </script>
        </head></html>
        """

        recipe = extract_recipe(html, "https://example.com/recipe")

        self.assertEqual(recipe.title, "Broiled Salmon")
        self.assertEqual(recipe.ingredients, ["2 scallions", "1 tsp cilantro"])
        self.assertEqual(recipe.steps, ["Broil until cooked."])
        self.assertEqual(recipe.prep_time, "10 mins")
        self.assertEqual(recipe.cook_time, "1 hour 20 mins")
        self.assertEqual(recipe.servings, "4 servings")

    def test_extracts_recipe_from_graph(self) -> None:
        html = """
        <script type="application/ld+json">
        {"@graph": [{"@type": "WebPage"}, {"@type": ["Recipe"], "name": "Soup",
        "recipeIngredient": ["water"], "recipeInstructions": ["Simmer."]}]}
        </script>
        """

        recipe = extract_recipe(html, "https://example.com/soup")

        self.assertEqual(recipe.title, "Soup")
        self.assertEqual(recipe.ingredients, ["water"])
        self.assertEqual(recipe.steps, ["Simmer."])

    def test_extracts_yield_when_distinct_from_servings(self) -> None:
        html = """
        <script type="application/ld+json">
        {"@type": "Recipe", "name": "Jam", "recipeIngredient": ["fruit"],
        "recipeInstructions": ["Boil."], "yield": "3 jars"}
        </script>
        """

        recipe = extract_recipe(html, "https://example.com/jam")

        self.assertEqual(recipe.servings, "")
        self.assertEqual(recipe.yield_amount, "3 jars")

    def test_uses_html_fallback_when_json_ld_is_missing(self) -> None:
        html = """
        <h1>Fallback Pasta</h1>
        <li class="recipe-ingredient">100g spaghetti</li>
        <li class="recipe-instruction">Boil the pasta.</li>
        """

        recipe = extract_recipe(html, "https://example.com/pasta")

        self.assertEqual(recipe.title, "Fallback Pasta")
        self.assertEqual(recipe.ingredients, ["100g spaghetti"])
        self.assertEqual(recipe.steps, ["Boil the pasta."])


if __name__ == "__main__":
    unittest.main()
