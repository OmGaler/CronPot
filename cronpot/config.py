from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


DEFAULT_CONFIG_PATH = "cronpot.toml"


@dataclass(slots=True)
class AutomationConfig:
    default_vault: str = "docs"
    require_dietary_tag: bool = True
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

    return AutomationConfig(
        default_vault=str(recipe_config.get("default_vault") or "docs"),
        require_dietary_tag=bool(recipe_config.get("require_dietary_tag", True)),
        llm_provider=str(llm_config.get("provider") or "ollama"),
        llm_base_url=str(llm_config.get("base_url") or "http://127.0.0.1:11434").rstrip("/"),
        llm_model=str(llm_config.get("model") or "gemma4:latest"),
        llm_auto_normalise_ingredients=bool(llm_config.get("auto_normalise_ingredients", False)),
        llm_rewrite_ingested_recipes=bool(llm_config.get("rewrite_ingested_recipes", False)),
        llm_ingredient_limit=int(llm_config.get("ingredient_limit") or 120),
    )
