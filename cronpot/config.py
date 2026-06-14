from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


DEFAULT_CONFIG_PATH = "cronpot.toml"


@dataclass(slots=True)
class AutomationConfig:
    default_vault: str = "docs"
    require_dietary_tag: bool = True
    ingredient_heading: str = "Ingredients"
    method_heading: str = "Method"
    frontmatter_fields: tuple[str, ...] = (
        "tags",
        "source",
        "source_hash",
        "prep_time",
        "cook_time",
        "total_time",
        "servings",
        "yield",
    )
    english: str = "british"
    fraction_style: str = "unicode"
    method_style: str = "imperative"
    worker_count: int = 2
    worker_max_attempts: int = 3
    worker_stale_after_seconds: int = 900
    llm_provider: str = "ollama"
    llm_base_url: str = "http://127.0.0.1:11434"
    llm_model: str = "gemma4:latest"
    llm_auto_normalise_ingredients: bool = False
    llm_rewrite_ingested_recipes: bool = False
    llm_ingredient_limit: int = 120


def load_config(path: str | Path | None = None) -> AutomationConfig:
    config_path = Path(path or DEFAULT_CONFIG_PATH)
    if not config_path.exists():
        return AutomationConfig()

    with config_path.open("rb") as file:
        raw = tomllib.load(file)

    recipe_config = raw.get("recipe", {})
    if not isinstance(recipe_config, dict):
        recipe_config = {}
    llm_config = raw.get("llm", {})
    if not isinstance(llm_config, dict):
        llm_config = {}
    schema_config = raw.get("schema", {})
    if not isinstance(schema_config, dict):
        schema_config = {}
    style_config = raw.get("style", {})
    if not isinstance(style_config, dict):
        style_config = {}
    worker_config = raw.get("worker", {})
    if not isinstance(worker_config, dict):
        worker_config = {}

    return AutomationConfig(
        default_vault=str(recipe_config.get("default_vault") or "docs"),
        require_dietary_tag=bool(recipe_config.get("require_dietary_tag", True)),
        ingredient_heading=str(schema_config.get("ingredient_heading") or "Ingredients"),
        method_heading=str(schema_config.get("method_heading") or "Method"),
        frontmatter_fields=_string_tuple(
            schema_config.get("frontmatter_fields"),
            AutomationConfig.frontmatter_fields,
        ),
        english=str(style_config.get("english") or "british").casefold(),
        fraction_style=str(style_config.get("fraction_style") or "unicode").casefold(),
        method_style=str(style_config.get("method_style") or "imperative").casefold(),
        worker_count=max(int(worker_config.get("count") or 2), 1),
        worker_max_attempts=max(int(worker_config.get("max_attempts") or 3), 1),
        worker_stale_after_seconds=max(int(worker_config.get("stale_after_seconds") or 900), 30),
        llm_provider=str(llm_config.get("provider") or "ollama"),
        llm_base_url=str(llm_config.get("base_url") or "http://127.0.0.1:11434").rstrip("/"),
        llm_model=str(llm_config.get("model") or "gemma4:latest"),
        llm_auto_normalise_ingredients=bool(llm_config.get("auto_normalise_ingredients", False)),
        llm_rewrite_ingested_recipes=bool(llm_config.get("rewrite_ingested_recipes", False)),
        llm_ingredient_limit=int(llm_config.get("ingredient_limit") or 120),
    )


def _string_tuple(value: object, default: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(value, list):
        return default
    items = tuple(str(item).strip() for item in value if str(item).strip())
    return items or default
