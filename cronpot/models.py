from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Recipe:
    title: str
    ingredients: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    prep_time: str = ""
    cook_time: str = ""
    total_time: str = ""
    servings: str = ""
    yield_amount: str = ""
    source: str = ""
    tags: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    source_hash: str = ""

    def has_core_content(self) -> bool:
        return bool(self.title.strip() and self.ingredients and self.steps)
