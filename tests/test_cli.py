from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from cronpot.cli import main
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

    def test_start_command_runs_server(self) -> None:
        with patch("cronpot.cli.run_server") as run_server:
            exit_code = main(["start", "--vault", "docs", "--host", "127.0.0.1", "--port", "9090"])

        self.assertEqual(exit_code, 0)
        run_server.assert_called_once()
        self.assertEqual(run_server.call_args.args[0], "127.0.0.1")
        self.assertEqual(run_server.call_args.args[1], 9090)
        self.assertEqual(run_server.call_args.args[2], "docs")


if __name__ == "__main__":
    unittest.main()
