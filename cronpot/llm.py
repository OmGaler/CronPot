from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cronpot.analytics import _ingredient_keys
from cronpot.config import AutomationConfig
from cronpot.vault import load_recipes


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


def _ingredient_alias_candidates(vault_path: str, config: AutomationConfig, limit: int) -> tuple[dict[str, str], Counter[str]]:
    if config.llm_provider != "ollama":
        raise LlmError(f"Unsupported LLM provider: {config.llm_provider}")

    _ensure_ollama_model_available(config)
    ingredient_counts = _ingredient_counts(vault_path)
    candidates = [name for name, _count in ingredient_counts.most_common(limit)]
    if not candidates:
        return {}, ingredient_counts

    response = _call_ollama(config, _ingredient_alias_prompt(candidates))
    aliases = _parse_alias_response(response)
    return aliases, ingredient_counts


def _ingredient_counts(vault_path: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for _path, recipe in load_recipes(vault_path):
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


def _parse_alias_response(text: str) -> dict[str, str]:
    try:
        parsed: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LlmError(f"Ollama did not return valid JSON: {exc}") from exc

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
