from __future__ import annotations

import os
import re
import subprocess
import shutil
import tempfile
from collections.abc import Iterable
from collections import Counter
from dataclasses import dataclass
from html import escape
from pathlib import Path

from cronpot.models import Recipe
from cronpot.config import AutomationConfig
from cronpot.normalisation import normalise_text
from cronpot.vault import load_recipes


@dataclass(slots=True)
class CookbookAnalytics:
    recipe_count: int
    tag_counts: Counter[str]
    category_counts: Counter[str]
    ingredient_counts: Counter[str]
    recipes_missing_source: int


INGREDIENT_ALIASES: dict[str, str] = {
    "caster sugar": "sugar",
    "granulated sugar": "sugar",
    "superfine sugar": "sugar",
    "white sugar": "sugar",
    "table salt": "salt",
    "sea salt": "salt",
    "kosher salt": "salt",
    "fine salt": "salt",
    "egg": "eggs",
    "flour": "plain flour",
    "plain white flour": "plain flour",
    "unsalted butter": "butter",
    "salted butter": "butter",
    "whole milk": "milk",
    "full fat milk": "milk",
    "black pepper": "pepper",
    "white pepper": "pepper",
}


def analyse_vault(
    vault_path: Path | str,
    ingredient_aliases: dict[str, str] | None = None,
    config: AutomationConfig | None = None,
) -> CookbookAnalytics:
    recipes = [recipe for _path, recipe in load_recipes(vault_path, config)]
    tag_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    ingredient_counts: Counter[str] = Counter()
    missing_source = 0

    for recipe in recipes:
        tag_counts.update(recipe.tags)
        category_counts.update(category.title() for category in recipe.categories)
        ingredient_counts.update(key for item in recipe.ingredients for key in _ingredient_keys(item, ingredient_aliases=ingredient_aliases))
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
            "    @page { margin: 18mm; }",
            "    @media print { body { margin: 0; max-width: none; padding: 0; } article { break-inside: avoid; } }",
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


def pdf_cookbook(recipes: list[tuple[Path, Recipe]], title: str = "CronPot Cookbook") -> bytes:
    browser = _pdf_browser_path()
    if browser is None:
        raise RuntimeError("PDF export requires Microsoft Edge or Chrome for HTML rendering.")

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        html_path = temp_path / "cookbook.html"
        pdf_path = temp_path / "cookbook.pdf"
        profile_path = temp_path / "browser-profile"
        html_path.write_text(html_cookbook(recipes, title), encoding="utf-8", newline="\n")
        try:
            result = subprocess.run(
                _pdf_browser_arguments(browser, profile_path, pdf_path, html_path),
                text=True,
                capture_output=True,
                check=False,
                timeout=60,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Could not render PDF: browser timed out while printing HTML.") from exc
        if result.returncode != 0 or not pdf_path.exists():
            output = (result.stderr or result.stdout or "browser PDF generation failed").strip()
            raise RuntimeError(f"Could not render PDF: {output}")
        return pdf_path.read_bytes()


def _pdf_browser_path() -> Path | None:
    candidates = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    for command in ("microsoft-edge", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(command)
        if found:
            return Path(found)
    return None


def _pdf_browser_arguments(browser: Path, profile_path: Path, pdf_path: Path, html_path: Path) -> list[str]:
    arguments = [
        str(browser),
        "--headless",
        "--disable-gpu",
        "--disable-background-networking",
        "--disable-extensions",
        "--no-first-run",
        "--no-default-browser-check",
        "--no-pdf-header-footer",
    ]
    if os.environ.get("CRONPOT_PDF_NO_SANDBOX") == "1":
        arguments.append("--no-sandbox")
    return [
        *arguments,
        f"--user-data-dir={profile_path}",
        f"--print-to-pdf={pdf_path}",
        html_path.as_uri(),
    ]


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
    keys = _ingredient_keys(value)
    return keys[0] if keys else ""


def _ingredient_keys(value: str, apply_aliases: bool = True, ingredient_aliases: dict[str, str] | None = None) -> list[str]:
    text = normalise_text(value).casefold()
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"^\d+[\d\s./-]*", "", text)
    text = re.sub(
        r"\b(cup|cups|g|kg|ml|l|tsp|tbsp|teaspoon|teaspoons|tablespoon|tablespoons|oz|lb|pinch|handful|small|medium|large)\b",
        " ",
        text,
    )
    text = re.sub(
        r"\b(of|a|an|the|fresh|freshly|ground|chopped|finely|thinly|sliced|diced|melted|softened|divided|optional|to|taste)\b",
        " ",
        text,
    )
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    parts = [part.strip() for part in re.split(r"\band\b", text) if part.strip()]
    if len(parts) > 1:
        return _unique_keys(_canonical_ingredient(part, apply_aliases, ingredient_aliases) for part in parts)
    return [_canonical_ingredient(text, apply_aliases, ingredient_aliases)]


def _canonical_ingredient(value: str, apply_aliases: bool, ingredient_aliases: dict[str, str] | None = None) -> str:
    if not apply_aliases:
        return value
    deterministic = INGREDIENT_ALIASES.get(value, value)
    if ingredient_aliases and deterministic == value:
        return ingredient_aliases.get(value, value)
    return deterministic


def _unique_keys(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if isinstance(value, str) and value and value not in seen:
            unique.append(value)
            seen.add(value)
    return unique
