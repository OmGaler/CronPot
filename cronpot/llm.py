from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from dataclasses import replace
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cronpot.analytics import _ingredient_keys
from cronpot.config import AutomationConfig
from cronpot.models import Recipe
from cronpot.vault import load_recipes, render_markdown


@dataclass(slots=True)
class IngredientAliasSuggestion:
    source: str
    canonical: str
    count: int


class LlmError(RuntimeError):
    pass


def suggest_ingredient_aliases(vault_path: str, config: AutomationConfig, limit: int = 80) -> list[IngredientAliasSuggestion]:
    aliases, ingredient_counts = _ingredient_alias_candidates(vault_path, config, limit)
    return _suggestions_from_aliases(aliases, ingredient_counts)


def suggest_ingredient_alias_map(vault_path: str, config: AutomationConfig, limit: int = 80) -> dict[str, str]:
    suggestions = suggest_ingredient_aliases(vault_path, config, limit)
    return {suggestion.source: suggestion.canonical for suggestion in suggestions}


def rewrite_recipe_to_vault_style(recipe: Recipe, vault_path: str, config: AutomationConfig) -> Recipe:
    if config.llm_provider != "ollama":
        raise LlmError(f"Unsupported LLM provider: {config.llm_provider}")

    _ensure_ollama_model_available(config)
    response = _call_ollama(config, _recipe_rewrite_prompt(recipe, vault_path, config))
    rewritten = _parse_recipe_response(response)
    return replace(
        recipe,
        title=rewritten.title or recipe.title,
        ingredients=rewritten.ingredients or recipe.ingredients,
        steps=rewritten.steps or recipe.steps,
        prep_time=rewritten.prep_time or recipe.prep_time,
        cook_time=rewritten.cook_time or recipe.cook_time,
        total_time=rewritten.total_time or recipe.total_time,
        servings=rewritten.servings or recipe.servings,
        yield_amount=rewritten.yield_amount or recipe.yield_amount,
        tags=rewritten.tags or recipe.tags,
        categories=rewritten.categories or recipe.categories,
        source=recipe.source,
        source_hash=recipe.source_hash,
    )


def _ingredient_alias_candidates(vault_path: str, config: AutomationConfig, limit: int) -> tuple[dict[str, str], Counter[str]]:
    if config.llm_provider != "ollama":
        raise LlmError(f"Unsupported LLM provider: {config.llm_provider}")

    _ensure_ollama_model_available(config)
    ingredient_counts = _ingredient_counts(vault_path, config)
    candidates = [name for name, _count in ingredient_counts.most_common(limit)]
    if not candidates:
        return {}, ingredient_counts

    response = _call_ollama(config, _ingredient_alias_prompt(candidates))
    aliases = _parse_alias_response(response)
    return aliases, ingredient_counts


def _ingredient_counts(vault_path: str, config: AutomationConfig | None = None) -> Counter[str]:
    counts: Counter[str] = Counter()
    for _path, recipe in load_recipes(vault_path, config):
        for ingredient in recipe.ingredients:
            counts.update(_ingredient_keys(ingredient, apply_aliases=False))
    return counts


def _call_ollama(config: AutomationConfig, prompt: str) -> str:
    payload = json.dumps(
        {
            "model": config.llm_model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        }
    ).encode("utf-8")
    request = Request(
        f"{config.llm_base_url}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=120) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise LlmError(f"Could not call Ollama at {config.llm_base_url}: {_ollama_http_error(exc)}") from exc
    except (OSError, URLError, json.JSONDecodeError) as exc:
        raise LlmError(f"Could not call Ollama at {config.llm_base_url}: {exc}") from exc

    text = raw.get("response", "")
    if not isinstance(text, str) or not text.strip():
        raise LlmError("Ollama returned an empty response.")
    return text


def _ensure_ollama_model_available(config: AutomationConfig) -> None:
    models = _ollama_models(config)
    if not models:
        return
    if config.llm_model not in models:
        available = ", ".join(models)
        raise LlmError(f"Ollama model {config.llm_model!r} is not installed. Available model(s): {available}.")


def _ollama_models(config: AutomationConfig) -> list[str]:
    request = Request(f"{config.llm_base_url}/api/tags", method="GET")
    try:
        with urlopen(request, timeout=10) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError):
        return []
    models = raw.get("models", [])
    if not isinstance(models, list):
        return []
    names: list[str] = []
    for model in models:
        if isinstance(model, dict) and isinstance(model.get("name"), str):
            names.append(model["name"])
    return names


def _ollama_http_error(error: HTTPError) -> str:
    try:
        body = error.read().decode("utf-8")
        payload = json.loads(body)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return f"HTTP Error {error.code}: {error.reason}"
    message = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(message, str) and message:
        return message
    return f"HTTP Error {error.code}: {error.reason}"


def _ingredient_alias_prompt(ingredients: list[str]) -> str:
    body = "\n".join(f"- {ingredient}" for ingredient in ingredients)
    return (
        "You are helping normalise recipe ingredient analytics for a personal cookbook.\n"
        "Group ingredient names only when they are clearly the same kitchen ingredient.\n"
        "Do not group broad categories such as oil with olive oil, or chocolate with cocoa.\n"
        "Return JSON only, in this exact shape: {\"aliases\":{\"observed ingredient\":\"canonical ingredient\"}}.\n"
        "Use lowercase canonical ingredient names.\n\n"
        f"Observed ingredient names:\n{body}\n"
    )


def _recipe_rewrite_prompt(recipe: Recipe, vault_path: str, config: AutomationConfig, sample_limit: int = 3) -> str:
    examples = _vault_style_examples(vault_path, config, sample_limit)
    examples_text = "\n\n---\n\n".join(examples) if examples else "No existing vault examples are available yet."
    payload = {
        "title": recipe.title,
        "ingredients": recipe.ingredients,
        "steps": recipe.steps,
        "prep_time": recipe.prep_time,
        "cook_time": recipe.cook_time,
        "total_time": recipe.total_time,
        "servings": recipe.servings,
        "yield": recipe.yield_amount,
        "tags": recipe.tags,
        "categories": recipe.categories,
        "source": recipe.source,
    }
    return (
        "You are rewriting an extracted web recipe for a personal Obsidian cookbook.\n"
        "Preserve the recipe facts: do not add ingredients, remove ingredients, invent timings, or change the source.\n"
        f"Use {config.english} English. Use {config.fraction_style} fractions. Write method steps in a {config.method_style} style.\n"
        "Clean messy wording, keep ingredient lines concise, and preserve the configured style conventions.\n"
        "Match the style of the existing vault examples where possible.\n"
        "Return JSON only, in this exact shape: "
        "{\"recipe\":{\"title\":\"\",\"ingredients\":[\"\"],\"steps\":[\"\"],\"prep_time\":\"\",\"cook_time\":\"\",\"total_time\":\"\",\"servings\":\"\",\"yield\":\"\",\"tags\":[\"\"],\"categories\":[\"\"]}}.\n\n"
        f"Existing vault examples:\n{examples_text}\n\n"
        f"Extracted recipe JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
    )


def _vault_style_examples(vault_path: str, config: AutomationConfig, limit: int) -> list[str]:
    examples: list[str] = []
    for path, recipe in load_recipes(vault_path, config)[:limit]:
        examples.append(f"Recipe title: {recipe.title or path.stem}\n{render_markdown(recipe).strip()}")
    return examples


def _parse_alias_response(text: str) -> dict[str, str]:
    parsed = _parse_json_object(text)

    aliases = parsed.get("aliases") if isinstance(parsed, dict) else None
    if not isinstance(aliases, dict):
        raise LlmError('Ollama JSON must contain an "aliases" object.')

    clean: dict[str, str] = {}
    for source, canonical in aliases.items():
        if isinstance(source, str) and isinstance(canonical, str):
            source_key = source.strip().casefold()
            canonical_key = canonical.strip().casefold()
            if source_key and canonical_key and source_key != canonical_key:
                clean[source_key] = canonical_key
    return clean


def _parse_recipe_response(text: str) -> Recipe:
    parsed = _parse_json_object(text)
    recipe = parsed.get("recipe")
    if not isinstance(recipe, dict):
        recipe = parsed if _looks_like_recipe_object(parsed) else None
    if not isinstance(recipe, dict):
        raise LlmError('Ollama JSON must contain a "recipe" object.')

    title = _json_string(recipe.get("title"))
    ingredients = _json_string_list(recipe.get("ingredients"))
    steps = _json_string_list(recipe.get("steps"))
    if not title and not ingredients and not steps:
        raise LlmError("Ollama recipe rewrite must include recipe content.")

    return Recipe(
        title=title,
        ingredients=ingredients,
        steps=steps,
        prep_time=_json_string(recipe.get("prep_time")),
        cook_time=_json_string(recipe.get("cook_time")),
        total_time=_json_string(recipe.get("total_time")),
        servings=_json_string(recipe.get("servings")),
        yield_amount=_json_string(recipe.get("yield")),
        tags=_json_string_list(recipe.get("tags")),
        categories=_json_string_list(recipe.get("categories")),
    )


def _looks_like_recipe_object(value: dict[str, Any]) -> bool:
    return any(
        key in value
        for key in (
            "title",
            "ingredients",
            "steps",
            "prep_time",
            "cook_time",
            "total_time",
            "servings",
            "yield",
            "tags",
            "categories",
        )
    )


def _parse_json_object(text: str) -> dict[str, Any]:
    clean = text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\s*```$", "", clean)
    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError as exc:
        raise LlmError(f"Ollama did not return valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LlmError("Ollama JSON response must be an object.")
    return parsed


def _json_string(value: Any) -> str:
    return str(value).strip() if isinstance(value, str) else ""


def _json_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _suggestions_from_aliases(aliases: dict[str, str], counts: Counter[str]) -> list[IngredientAliasSuggestion]:
    grouped: defaultdict[str, list[str]] = defaultdict(list)
    for source, canonical in aliases.items():
        if source not in counts:
            continue
        source_canonical = _single_canonical_key(source)
        proposed_canonical = _single_canonical_key(canonical)
        if not source_canonical or not proposed_canonical:
            continue
        if source_canonical == proposed_canonical:
            continue
        if source_canonical in {"oil"} or proposed_canonical in {"oil"}:
            continue
        grouped[proposed_canonical].append(source)

    suggestions: list[IngredientAliasSuggestion] = []
    for canonical, sources in grouped.items():
        observed = set(sources)
        if canonical in counts:
            observed.add(canonical)
        if len(observed) < 2:
            continue
        for source in sorted(sources):
            suggestions.append(IngredientAliasSuggestion(source, canonical, counts[source]))
    return sorted(suggestions, key=lambda item: (item.canonical, item.source))


def _single_canonical_key(value: str) -> str:
    keys = _ingredient_keys(value)
    return keys[0] if len(keys) == 1 else ""
