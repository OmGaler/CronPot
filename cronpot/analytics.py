from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from html import escape
from pathlib import Path

from cronpot.models import Recipe
from cronpot.normalisation import normalise_text
from cronpot.vault import load_recipes


@dataclass(slots=True)
class CookbookAnalytics:
    recipe_count: int
    tag_counts: Counter[str]
    category_counts: Counter[str]
    ingredient_counts: Counter[str]
    recipes_missing_source: int


def analyse_vault(vault_path: Path | str) -> CookbookAnalytics:
    recipes = [recipe for _path, recipe in load_recipes(vault_path)]
    tag_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    ingredient_counts: Counter[str] = Counter()
    missing_source = 0

    for recipe in recipes:
        tag_counts.update(recipe.tags)
        category_counts.update(category.title() for category in recipe.categories)
        ingredient_counts.update(_ingredient_key(item) for item in recipe.ingredients if _ingredient_key(item))
        if not recipe.source:
            missing_source += 1

    return CookbookAnalytics(
        recipe_count=len(recipes),
        tag_counts=tag_counts,
        category_counts=category_counts,
        ingredient_counts=ingredient_counts,
        recipes_missing_source=missing_source,
    )


def build_shopping_list(recipes: list[Recipe]) -> list[str]:
    seen: set[str] = set()
    items: list[str] = []
    for recipe in recipes:
        for ingredient in recipe.ingredients:
            key = normalise_text(ingredient).casefold()
            if key and key not in seen:
                items.append(ingredient)
                seen.add(key)
    return items


def bundle_markdown(recipes: list[tuple[Path, Recipe]]) -> str:
    parts: list[str] = []
    for path, recipe in recipes:
        parts.append(f"# {recipe.title or path.stem}")
        if recipe.source:
            parts.append(f"Source: {recipe.source}")
        parts.append("")
        parts.append("## Ingredients")
        parts.extend(f"- {ingredient}" for ingredient in recipe.ingredients)
        parts.append("")
        parts.append("## Method")
        parts.extend(f"{index}. {step}" for index, step in enumerate(recipe.steps, start=1))
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def html_cookbook(recipes: list[tuple[Path, Recipe]], title: str = "CronPot Cookbook") -> str:
    recipe_blocks = [_html_recipe(path, recipe) for path, recipe in recipes]
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en-GB">',
            "<head>",
            '  <meta charset="utf-8">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1">',
            f"  <title>{escape(title)}</title>",
            "  <style>",
            "    body { font-family: system-ui, sans-serif; line-height: 1.55; margin: 2rem auto; max-width: 72rem; padding: 0 1rem; }",
            "    h1, h2 { line-height: 1.2; }",
            "    article { border-top: 1px solid #ddd; padding: 1.5rem 0; }",
            "    .meta { color: #555; display: flex; flex-wrap: wrap; gap: .5rem 1rem; }",
            "    .tags { color: #555; font-size: .95rem; }",
            "  </style>",
            "</head>",
            "<body>",
            f"  <h1>{escape(title)}</h1>",
            f"  <p>{len(recipes)} recipe{'s' if len(recipes) != 1 else ''}</p>",
            *recipe_blocks,
            "</body>",
            "</html>",
            "",
        ]
    )


def _html_recipe(path: Path, recipe: Recipe) -> str:
    title = recipe.title or path.stem
    meta_items = [
        ("Prep", recipe.prep_time),
        ("Cook", recipe.cook_time),
        ("Total", recipe.total_time),
        ("Servings", recipe.servings),
        ("Yield", recipe.yield_amount),
    ]
    meta = "".join(f"<span>{escape(label)}: {escape(value)}</span>" for label, value in meta_items if value)
    source = f'    <p><a href="{escape(recipe.source, quote=True)}">Source</a></p>\n' if recipe.source else ""
    tags = ", ".join([*recipe.categories, *recipe.tags])
    tag_block = f'    <p class="tags">{escape(tags)}</p>\n' if tags else ""
    ingredients = "\n".join(f"      <li>{escape(ingredient)}</li>" for ingredient in recipe.ingredients)
    steps = "\n".join(f"      <li>{escape(step)}</li>" for step in recipe.steps)
    meta_block = f'    <p class="meta">{meta}</p>\n' if meta else ""

    return "\n".join(
        [
            "  <article>",
            f"    <h2>{escape(title)}</h2>",
            meta_block.rstrip(),
            source.rstrip(),
            tag_block.rstrip(),
            "    <h3>Ingredients</h3>",
            "    <ul>",
            ingredients,
            "    </ul>",
            "    <h3>Method</h3>",
            "    <ol>",
            steps,
            "    </ol>",
            "  </article>",
        ]
    )


def _ingredient_key(value: str) -> str:
    text = normalise_text(value).casefold()
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"^\d+[\d\s./-]*", "", text)
    text = re.sub(
        r"\b(cup|cups|g|kg|ml|l|tsp|tbsp|teaspoon|tablespoon|oz|lb|pinch|handful|small|medium|large)\b",
        " ",
        text,
    )
    text = re.sub(r"\b(of|a|an|the|fresh|chopped|finely|thinly|sliced|diced|to|taste)\b", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
