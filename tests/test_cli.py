from __future__ import annotations

import tempfile
import unittest
import os
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from cronpot.cli import _ingest_title, main
from cronpot.config import AutomationConfig
from cronpot.llm import IngredientAliasSuggestion
from cronpot.models import Recipe
from cronpot.vault import write_recipe_to_vault


class CliTests(unittest.TestCase):
    def test_html_command_writes_selected_recipe_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            output = vault / "cookbook.html"
            write_recipe_to_vault(
                Recipe(
                    title="Aglio e Olio",
                    ingredients=["100g spaghetti"],
                    steps=["Boil the pasta."],
                    tags=["parev"],
                    categories=["Mains"],
                ),
                vault,
            )

            with redirect_stdout(StringIO()):
                exit_code = main(["html", "Aglio e Olio", "--vault", str(vault), "--output", str(output)])

            self.assertEqual(exit_code, 0)
            text = output.read_text(encoding="utf-8")
            self.assertIn("<h2>Aglio e Olio</h2>", text)
            self.assertIn("<li>100g spaghetti</li>", text)

    def test_export_command_defaults_to_html(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            write_recipe_to_vault(
                Recipe(
                    title="Aglio e Olio",
                    ingredients=["100g spaghetti"],
                    steps=["Boil the pasta."],
                    tags=["parev"],
                    categories=["Mains"],
                ),
                vault,
            )

            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["export", "Aglio e Olio", "--vault", str(vault)])

            self.assertEqual(exit_code, 0)
            self.assertIn("<!doctype html>", stdout.getvalue())
            self.assertIn("<h2>Aglio e Olio</h2>", stdout.getvalue())

    def test_export_command_supports_markdown_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            write_recipe_to_vault(
                Recipe(
                    title="Aglio e Olio",
                    ingredients=["100g spaghetti"],
                    steps=["Boil the pasta."],
                    tags=["parev"],
                    categories=["Mains"],
                ),
                vault,
            )

            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["export", "Aglio e Olio", "--vault", str(vault), "--format", "markdown"])

            self.assertEqual(exit_code, 0)
            self.assertIn("# Aglio e Olio", stdout.getvalue())
            self.assertIn("- 100g spaghetti", stdout.getvalue())

    def test_export_command_supports_pdf_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            output = vault / "cookbook.pdf"
            write_recipe_to_vault(
                Recipe(
                    title="Aglio e Olio",
                    ingredients=["100g spaghetti"],
                    steps=["Boil the pasta."],
                    tags=["parev"],
                    categories=["Mains"],
                ),
                vault,
            )

            with redirect_stdout(StringIO()):
                exit_code = main(["export", "Aglio e Olio", "--vault", str(vault), "--format", "pdf", "--output", str(output)])

            self.assertEqual(exit_code, 0)
            self.assertTrue(output.read_bytes().startswith(b"%PDF-1.4"))

    def test_export_pdf_requires_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            workdir = Path(temp_dir) / "out"
            workdir.mkdir()
            write_recipe_to_vault(
                Recipe(
                    title="Aglio e Olio",
                    ingredients=["100g spaghetti"],
                    steps=["Boil the pasta."],
                    tags=["parev"],
                    categories=["Mains"],
                ),
                vault,
            )

            previous_cwd = Path.cwd()
            try:
                os.chdir(workdir)
                with redirect_stdout(StringIO()):
                    exit_code = main(["export", "Aglio e Olio", "--vault", str(vault), "--format", "pdf"])
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(exit_code, 0)
            self.assertTrue((workdir / "Aglio e Olio.pdf").exists())

    def test_start_command_runs_server(self) -> None:
        with patch("cronpot.cli.run_server") as run_server, redirect_stdout(StringIO()):
            exit_code = main(["start", "--vault", "docs", "--host", "127.0.0.1", "--port", "9090"])

        self.assertEqual(exit_code, 0)
        run_server.assert_called_once()
        self.assertEqual(run_server.call_args.args[0], "127.0.0.1")
        self.assertEqual(run_server.call_args.args[1], 9090)
        self.assertEqual(run_server.call_args.args[2], "docs")

    def test_start_command_prints_default_port(self) -> None:
        stdout = StringIO()
        with patch("cronpot.cli.run_server"), redirect_stdout(stdout):
            exit_code = main(["start"])

        self.assertEqual(exit_code, 0)
        self.assertIn("CronPot serving on http://0.0.0.0:8080", stdout.getvalue())

    def test_normalise_ingredients_suggest_prints_aliases(self) -> None:
        stdout = StringIO()
        with patch(
            "cronpot.cli.suggest_ingredient_aliases",
            return_value=[IngredientAliasSuggestion("granulated sugar", "sugar", 3)],
        ) as suggest, redirect_stdout(stdout):
            exit_code = main(["normalise", "ingredients", "--vault", "docs", "--suggest", "--model", "qwen2.5:3b"])

        self.assertEqual(exit_code, 0)
        self.assertIn("granulated sugar -> sugar (3)", stdout.getvalue())
        self.assertEqual(suggest.call_args.args[1].llm_model, "qwen2.5:3b")

    def test_analytics_uses_configured_llm_aliases(self) -> None:
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

            stdout = StringIO()
            with patch("cronpot.cli.load_config") as load_config, patch(
                "cronpot.cli.suggest_ingredient_alias_map",
                return_value={"extra fine sugar": "sugar"},
            ):
                load_config.return_value.default_vault = str(vault)
                load_config.return_value.llm_auto_normalise_ingredients = True
                load_config.return_value.llm_ingredient_limit = 120
                with redirect_stdout(stdout):
                    exit_code = main(["analytics", "--vault", str(vault)])

            self.assertEqual(exit_code, 0)
            self.assertIn("- sugar: 2", stdout.getvalue())

    def test_ingest_rewrites_recipe_when_configured(self) -> None:
        html = """
        <script type="application/ld+json">
        {"@type":"Recipe","name":"messy soup","recipeIngredient":["one carrot"],"recipeInstructions":["you should chop it"]}
        </script>
        """
        rewritten = Recipe(
            title="Carrot Soup",
            ingredients=["1 carrot"],
            steps=["Chop the carrot."],
            tags=["starter", "parev"],
            categories=["Soups"],
            source="https://example.com/soup",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            stdout = StringIO()
            with patch("cronpot.cli.load_config", return_value=AutomationConfig(llm_rewrite_ingested_recipes=True)), patch(
                "cronpot.cli.fetch_html",
                return_value=html,
            ), patch("cronpot.ingest.rewrite_recipe_to_vault_style", return_value=rewritten) as rewrite, redirect_stdout(stdout):
                exit_code = main(["ingest", "https://example.com/soup", "--vault", str(vault), "--dry-run"])

            self.assertEqual(exit_code, 0)
            self.assertIn("#", stdout.getvalue())
            self.assertIn("- 1 carrot", stdout.getvalue())
            self.assertIn("1. Chop the carrot.", stdout.getvalue())
            rewrite.assert_called_once()

    def test_ingest_title_accepts_extracted_suggestion_when_prompt_response_is_empty(self) -> None:
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value=""):
            title = _ingest_title("Carrot Soup", None)

        self.assertEqual(title, "Carrot Soup")

    def test_ingest_title_uses_prompt_response(self) -> None:
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="Friday Night Soup"):
            title = _ingest_title("Carrot Soup", None)

        self.assertEqual(title, "Friday Night Soup")

    def test_ingest_title_uses_cli_override_without_prompting(self) -> None:
        with patch("builtins.input") as prompt:
            title = _ingest_title("Carrot Soup", "Shabbat Soup")

        self.assertEqual(title, "Shabbat Soup")
        prompt.assert_not_called()

    def test_ingest_command_title_override_changes_written_file(self) -> None:
        html = """
        <script type="application/ld+json">
        {"@type":"Recipe","name":"messy soup","recipeIngredient":["one carrot"],"recipeInstructions":["chop it"]}
        </script>
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            with patch("cronpot.cli.fetch_html", return_value=html), redirect_stdout(StringIO()):
                exit_code = main(["ingest", "https://example.com/soup", "--vault", str(vault), "--title", "Carrot Soup"])

            self.assertEqual(exit_code, 0)
            self.assertTrue((vault / "Carrot Soup.md").exists())


if __name__ == "__main__":
    unittest.main()
