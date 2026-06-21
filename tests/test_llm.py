from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cronpot.config import AutomationConfig
from cronpot.llm import (
    LlmError,
    _parse_alias_response,
    _parse_recipe_response,
    rewrite_recipe_to_vault_style,
    suggest_ingredient_alias_map,
    suggest_ingredient_aliases,
)
from cronpot.models import Recipe
from cronpot.vault import write_recipe_to_vault


class LlmTests(unittest.TestCase):
    def test_parses_alias_response(self) -> None:
        aliases = _parse_alias_response('{"aliases":{"granulated sugar":"sugar","sugar":"sugar"}}')

        self.assertEqual(aliases, {"granulated sugar": "sugar"})

    def test_suggests_aliases_from_ollama_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            write_recipe_to_vault(
                Recipe(
                    title="Cake",
                    ingredients=["100g extra fine sugar", "50g sugar", "1 tsp sea salt"],
                    steps=["Bake."],
                    tags=["dessert", "parev"],
                    categories=["Desserts"],
                ),
                vault,
            )

            with patch("cronpot.llm._ollama_models", return_value=["gemma4:latest"]), patch(
                "cronpot.llm._call_ollama",
                return_value='{"aliases":{"extra fine sugar":"sugar","sea salt":"salt","olive oil":"oil"}}',
            ):
                suggestions = suggest_ingredient_aliases(str(vault), AutomationConfig(), limit=10)

            self.assertEqual(len(suggestions), 1)
            self.assertEqual(suggestions[0].source, "extra fine sugar")
            self.assertEqual(suggestions[0].canonical, "sugar")

    def test_builds_alias_map_from_suggestions(self) -> None:
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

            with patch("cronpot.llm._ollama_models", return_value=["gemma4:latest"]), patch(
                "cronpot.llm._call_ollama",
                return_value='{"aliases":{"extra fine sugar":"sugar"}}',
            ):
                aliases = suggest_ingredient_alias_map(str(vault), AutomationConfig(), limit=10)

            self.assertEqual(aliases, {"extra fine sugar": "sugar"})

    def test_reports_missing_ollama_model_before_generation(self) -> None:
        with self.assertRaisesRegex(LlmError, "Available model"):
            with patch("cronpot.llm._ollama_models", return_value=["gemma4:latest"]):
                suggest_ingredient_aliases("docs", AutomationConfig(llm_model="qwen2.5:3b"), limit=10)

    def test_parses_recipe_rewrite_response(self) -> None:
        recipe = _parse_recipe_response(
            '{"recipe":{"title":"Soup","ingredients":["1 carrot"],"steps":["Chop the carrot."],"prep_time":"5 mins","cook_time":"","total_time":"","servings":"2","yield":"","tags":["starter","parev"],"categories":["Soups"]}}'
        )

        self.assertEqual(recipe.title, "Soup")
        self.assertEqual(recipe.ingredients, ["1 carrot"])
        self.assertEqual(recipe.steps, ["Chop the carrot."])
        self.assertEqual(recipe.categories, ["Soups"])

    def test_parses_bare_recipe_rewrite_response(self) -> None:
        recipe = _parse_recipe_response(
            '{"title":"Soup","ingredients":[],"steps":["Chop the carrot."],"prep_time":"","cook_time":"","total_time":"","servings":"","yield":"","tags":["starter"],"categories":["Soups"]}'
        )

        self.assertEqual(recipe.title, "Soup")
        self.assertEqual(recipe.ingredients, [])
        self.assertEqual(recipe.steps, ["Chop the carrot."])
        self.assertEqual(recipe.tags, ["starter"])

    def test_rewrites_recipe_to_vault_style(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            write_recipe_to_vault(
                Recipe(
                    title="Existing Soup",
                    ingredients=["1 onion"],
                    steps=["Slice the onion."],
                    tags=["starter", "parev"],
                    categories=["Soups"],
                ),
                vault,
            )
            source = Recipe(
                title="messy soup",
                ingredients=["one carrot"],
                steps=["you should chop it"],
                source="https://example.com/soup",
            )

            with patch("cronpot.llm._ollama_models", return_value=["gemma4:latest"]), patch(
                "cronpot.llm._call_ollama",
                return_value='{"recipe":{"title":"Carrot Soup","ingredients":["1 carrot"],"steps":["Chop the carrot."],"prep_time":"","cook_time":"","total_time":"","servings":"","yield":"","tags":["starter","parev"],"categories":["Soups"]}}',
            ) as call:
                rewritten = rewrite_recipe_to_vault_style(source, str(vault), AutomationConfig())

            self.assertEqual(rewritten.title, "Carrot Soup")
            self.assertEqual(rewritten.ingredients, ["1 carrot"])
            self.assertEqual(rewritten.steps, ["Chop the carrot."])
            self.assertEqual(rewritten.source, "https://example.com/soup")
            self.assertIn("Existing Soup", call.call_args.args[1])

    def test_rewrite_preserves_original_fields_when_model_omits_them(self) -> None:
        source = Recipe(
            title="messy soup",
            ingredients=["one carrot"],
            steps=["you should chop it"],
            source="https://example.com/soup",
        )

        with patch("cronpot.llm._ollama_models", return_value=["gemma4:latest"]), patch(
            "cronpot.llm._call_ollama",
            return_value='{"title":"Carrot Soup","ingredients":[],"steps":["Chop the carrot."],"prep_time":"","cook_time":"","total_time":"","servings":"","yield":"","tags":["starter"],"categories":["Soups"]}',
        ):
            rewritten = rewrite_recipe_to_vault_style(source, "docs", AutomationConfig())

        self.assertEqual(rewritten.title, "Carrot Soup")
        self.assertEqual(rewritten.ingredients, ["one carrot"])
        self.assertEqual(rewritten.steps, ["Chop the carrot."])


if __name__ == "__main__":
    unittest.main()
