from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cronpot.config import AutomationConfig
from cronpot.llm import LlmError, _parse_alias_response, suggest_ingredient_alias_map, suggest_ingredient_aliases
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


if __name__ == "__main__":
    unittest.main()
