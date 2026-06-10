from __future__ import annotations

from pathlib import Path

from cronpot.config import AutomationConfig
from cronpot.extraction import extract_recipe
from cronpot.llm import rewrite_recipe_to_vault_style
from cronpot.models import Recipe
from cronpot.normalisation import normalise_recipe


def prepare_ingested_recipe(html_text: str, source_url: str, vault_path: str | Path, config: AutomationConfig) -> Recipe:
    recipe = normalise_recipe(extract_recipe(html_text, source_url), config)
    if config.llm_rewrite_ingested_recipes:
        recipe = rewrite_recipe_to_vault_style(recipe, str(vault_path), config)
        recipe = normalise_recipe(recipe, config)
    return recipe
