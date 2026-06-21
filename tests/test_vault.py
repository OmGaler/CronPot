from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cronpot.models import Recipe
from cronpot.normalisation import normalise_recipe
from cronpot.config import AutomationConfig
from cronpot.vault import find_recipe_file, load_recipes, parse_markdown_recipe, render_markdown, source_hash, validate_vault, write_recipe_to_vault


class VaultTests(unittest.TestCase):
    def test_renders_and_parses_obsidian_recipe_markdown(self) -> None:
        recipe = Recipe(
            title="Test Soup",
            ingredients=["1 litre water"],
            steps=["Simmer."],
            tags=["soup", "parev"],
            categories=["Soups", "Starters"],
            source="https://example.com/test-soup",
        )

        markdown = render_markdown(recipe)
        parsed = parse_markdown_recipe(Path("Test Soup.md"), markdown)

        self.assertNotIn("title:", markdown)
        self.assertEqual(parsed.title, "Test Soup")
        self.assertEqual(parsed.ingredients, ["1 litre water"])
        self.assertEqual(parsed.steps, ["Simmer."])
        self.assertEqual(parsed.tags, ["soup", "parev"])
        self.assertEqual(parsed.categories, ["Soups", "Starters"])
        self.assertEqual(parsed.source_hash, source_hash(recipe.source))

    def test_renders_yield_only_when_servings_are_absent(self) -> None:
        recipe = Recipe(
            title="Jam",
            ingredients=["fruit"],
            steps=["Boil."],
            tags=["condiment", "parev"],
            categories=["Condiments"],
            yield_amount="3 jars",
        )

        markdown = render_markdown(recipe)
        parsed = parse_markdown_recipe(Path("Jam.md"), markdown)

        self.assertIn('yield: "3 jars"', markdown)
        self.assertNotIn("servings:", markdown)
        self.assertEqual(parsed.yield_amount, "3 jars")

    def test_renders_and_parses_custom_schema_headings_and_frontmatter(self) -> None:
        config = AutomationConfig(
            ingredient_heading="You Need",
            method_heading="You Do",
            frontmatter_fields=("tags", "source", "servings"),
        )
        recipe = Recipe(
            title="Soup",
            ingredients=["1 carrot"],
            steps=["Chop."],
            tags=["starter", "parev"],
            categories=["Soups"],
            source="https://example.com/soup",
            prep_time="5 mins",
            servings="2",
        )

        markdown = render_markdown(recipe, config)
        parsed = parse_markdown_recipe(Path("Soup.md"), markdown, config)

        self.assertIn("## You Need", markdown)
        self.assertIn("## You Do", markdown)
        self.assertNotIn("prep_time:", markdown)
        self.assertEqual(parsed.ingredients, ["1 carrot"])
        self.assertEqual(parsed.steps, ["Chop."])

    def test_parses_mandatory_marker_as_schema_annotation(self) -> None:
        markdown = """---
*tags:
  - parev
---
[[Mains]]

## Ingredients
- water

## Method
1. Simmer.
"""

        parsed = parse_markdown_recipe(Path("Soup.md"), markdown)

        self.assertEqual(parsed.tags, ["parev"])

    def test_validates_exactly_one_dietary_tag_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            (vault / "Invalid.md").write_text(
                """---
tags:
  - parev
  - milky
---
[[Mains]]

## Ingredients
- milk

## Method
1. Warm.
""",
                encoding="utf-8",
            )

            issues = validate_vault(vault)

            self.assertTrue(any("exactly one" in issue.message for issue in issues))

    def test_can_disable_dietary_tag_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            (vault / "Untyped.md").write_text(
                """---
tags:
  - main
---
[[Mains]]

## Ingredients
- water

## Method
1. Boil.
""",
                encoding="utf-8",
            )

            issues = validate_vault(vault, AutomationConfig(require_dietary_tag=False))

            self.assertFalse(any("parev" in issue.message for issue in issues))

    def test_loads_recipes_from_nested_vault_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            nested = vault / "Mains"
            nested.mkdir()
            runtime = vault / ".cronpot"
            runtime.mkdir()
            (nested / "Pie.md").write_text(
                """---
tags:
  - main
  - parev
---
[[Mains]]

## Ingredients
- 1 onion

## Method
- Bake.
""",
                encoding="utf-8",
            )
            (runtime / "Ignored.md").write_text(
                """## Ingredients
- queue data

## Method
- Ignore.
""",
                encoding="utf-8",
            )

            recipes = load_recipes(vault)

            self.assertEqual([recipe.title for _path, recipe in recipes], ["Pie"])
            self.assertEqual(find_recipe_file(vault, "Pie"), nested / "Pie.md")

    def test_writes_same_source_to_same_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = Path(temp_dir)
            first = normalise_recipe(
                Recipe(
                    title="Original Name",
                    ingredients=["1 chicken breast"],
                    steps=["Cook."],
                    source="https://example.com/chicken",
                )
            )
            second = normalise_recipe(
                Recipe(
                    title="Updated Name",
                    ingredients=["2 chicken breasts"],
                    steps=["Roast."],
                    source="https://example.com/chicken",
                )
            )

            first_path = write_recipe_to_vault(first, vault)
            second_path = write_recipe_to_vault(second, vault)

            self.assertEqual(first_path, second_path)
            parsed = parse_markdown_recipe(second_path)
            self.assertEqual(parsed.title, "Original Name")
            self.assertEqual(parsed.ingredients, ["2 chicken breasts"])


if __name__ == "__main__":
    unittest.main()
