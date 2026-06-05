from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


DEFAULT_CONFIG_PATH = "cronpot.toml"


@dataclass(slots=True)
class AutomationConfig:
    default_vault: str = "docs"
    require_dietary_tag: bool = True


def load_config(path: str | Path | None = None) -> AutomationConfig:
    config_path = Path(path or DEFAULT_CONFIG_PATH)
    if not config_path.exists():
        return AutomationConfig()

    with config_path.open("rb") as file:
        raw = tomllib.load(file)

    recipe_config = raw.get("recipe", {})
    if not isinstance(recipe_config, dict):
        recipe_config = {}

    return AutomationConfig(
        default_vault=str(recipe_config.get("default_vault") or "docs"),
        require_dietary_tag=bool(recipe_config.get("require_dietary_tag", True)),
    )
