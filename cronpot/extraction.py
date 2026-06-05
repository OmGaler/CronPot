from __future__ import annotations

import html
import json
import re
import urllib.request
from html.parser import HTMLParser
from typing import Any

from cronpot.models import Recipe


class JsonLdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.scripts: list[str] = []
        self._capturing = False
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {name.lower(): value or "" for name, value in attrs}
        script_type = attrs_map.get("type", "").lower()
        if tag.lower() == "script" and "ld+json" in script_type:
            self._capturing = True
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self._buffer.append(data)

    def handle_entityref(self, name: str) -> None:
        if self._capturing:
            self._buffer.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._capturing:
            self._buffer.append(f"&#{name};")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._capturing:
            self.scripts.append("".join(self._buffer))
            self._capturing = False
            self._buffer = []


class HtmlFallbackParser(HTMLParser):
    ingredient_markers = ("ingredient", "ingredients")
    step_markers = ("instruction", "instructions", "direction", "directions", "method", "preparation")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta_title = ""
        self.ingredients: list[str] = []
        self.steps: list[str] = []
        self._captures: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_map = {name.lower(): value or "" for name, value in attrs}
        marker_text = " ".join(
            value.lower() for name, value in attrs_map.items() if name in {"class", "id", "itemprop"}
        )

        if tag == "meta":
            key = attrs_map.get("property") or attrs_map.get("name")
            if key and key.lower() in {"og:title", "twitter:title"}:
                self.meta_title = clean_text(attrs_map.get("content", ""))
            return

        if tag in {"title", "h1"}:
            self._captures.append({"kind": "title", "tag": tag, "parts": []})
            return

        if tag in {"li", "p", "span", "div"} and _contains_marker(marker_text, self.ingredient_markers):
            self._captures.append({"kind": "ingredient", "tag": tag, "parts": []})
            return

        if tag in {"li", "p", "span", "div"} and _contains_marker(marker_text, self.step_markers):
            self._captures.append({"kind": "step", "tag": tag, "parts": []})

    def handle_data(self, data: str) -> None:
        for capture in self._captures:
            capture["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        finished = [capture for capture in self._captures if capture["tag"] == tag]
        self._captures = [capture for capture in self._captures if capture["tag"] != tag]

        for capture in finished:
            text = clean_text(" ".join(capture["parts"]))
            if not text:
                continue
            if capture["kind"] == "title" and not self.title:
                self.title = text
            elif capture["kind"] == "ingredient":
                self.ingredients.append(text)
            elif capture["kind"] == "step":
                self.steps.append(text)


def fetch_html(url: str, timeout: int = 20) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "CronPot/0.1 (+https://cookbook.omergaler.me/)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def extract_recipe(html_text: str, source: str = "") -> Recipe:
    json_recipe = extract_jsonld_recipe(html_text, source)
    fallback_recipe = extract_html_recipe(html_text, source)

    if json_recipe:
        return _merge_recipes(json_recipe, fallback_recipe)
    if fallback_recipe:
        return fallback_recipe
    return Recipe(title="", source=source)


def extract_jsonld_recipe(html_text: str, source: str = "") -> Recipe | None:
    parser = JsonLdParser()
    parser.feed(html_text)

    for script in parser.scripts:
        data = _load_jsonld(script)
        if data is None:
            continue
        for node in _iter_json_nodes(data):
            if _is_recipe_node(node):
                return _recipe_from_jsonld(node, source)
    return None


def extract_html_recipe(html_text: str, source: str = "") -> Recipe | None:
    parser = HtmlFallbackParser()
    parser.feed(html_text)

    title = parser.title or parser.meta_title
    ingredients = _dedupe(parser.ingredients)
    steps = _dedupe(parser.steps)

    if not title and not ingredients and not steps:
        return None

    return Recipe(title=title, ingredients=ingredients, steps=steps, source=source)


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _load_jsonld(script: str) -> Any:
    script = html.unescape(script.strip())
    try:
        return json.loads(script)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", script, flags=re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None


def _iter_json_nodes(value: Any) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []

    def visit(item: Any) -> None:
        if isinstance(item, list):
            for child in item:
                visit(child)
            return
        if not isinstance(item, dict):
            return
        nodes.append(item)
        for key in ("@graph", "graph", "mainEntity", "mainEntityOfPage"):
            if key in item:
                visit(item[key])

    visit(value)
    return nodes


def _is_recipe_node(node: dict[str, Any]) -> bool:
    node_type = node.get("@type") or node.get("type")
    if isinstance(node_type, list):
        return any(str(item).lower() == "recipe" for item in node_type)
    return str(node_type).lower() == "recipe"


def _recipe_from_jsonld(node: dict[str, Any], source: str) -> Recipe:
    ingredients = _as_text_list(node.get("recipeIngredient") or node.get("ingredients"))
    steps = _extract_instruction_steps(node.get("recipeInstructions") or node.get("instructions"))
    servings = node.get("recipeYield") or ""
    yield_amount = node.get("yield") or ""
    if isinstance(servings, list):
        servings = ", ".join(clean_text(item) for item in servings if clean_text(item))
    if isinstance(yield_amount, list):
        yield_amount = ", ".join(clean_text(item) for item in yield_amount if clean_text(item))

    return Recipe(
        title=clean_text(node.get("name") or node.get("headline")),
        ingredients=ingredients,
        steps=steps,
        prep_time=_format_duration(node.get("prepTime")),
        cook_time=_format_duration(node.get("cookTime")),
        total_time=_format_duration(node.get("totalTime")),
        servings=clean_text(servings),
        yield_amount=clean_text(yield_amount),
        source=source,
    )


def _extract_instruction_steps(value: Any) -> list[str]:
    steps: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, list):
            for child in item:
                visit(child)
            return
        if isinstance(item, str):
            text = clean_text(item)
            if text:
                steps.append(text)
            return
        if not isinstance(item, dict):
            return
        for key in ("itemListElement", "steps"):
            if key in item:
                visit(item[key])
                return
        text = clean_text(item.get("text") or item.get("name"))
        if text:
            steps.append(text)

    visit(value)
    return _dedupe(steps)


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [text for text in (clean_text(item) for item in value) if text]
    if isinstance(value, str):
        return [text for text in (clean_text(part) for part in re.split(r"[\n\r]+", value)) if text]
    return [clean_text(value)] if clean_text(value) else []


def _format_duration(value: Any) -> str:
    raw = clean_text(value)
    if not raw:
        return ""

    match = re.fullmatch(r"P(?:\d+D)?T(?:(\d+)H)?(?:(\d+)M)?", raw, flags=re.IGNORECASE)
    if not match:
        return raw

    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} hour" if hours == 1 else f"{hours} hours")
    if minutes:
        parts.append(f"{minutes} min" if minutes == 1 else f"{minutes} mins")
    return " ".join(parts)


def _contains_marker(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        key = value.casefold()
        if key and key not in seen:
            unique.append(value)
            seen.add(key)
    return unique


def _merge_recipes(primary: Recipe, fallback: Recipe | None) -> Recipe:
    if fallback is None:
        return primary
    return Recipe(
        title=primary.title or fallback.title,
        ingredients=primary.ingredients or fallback.ingredients,
        steps=primary.steps or fallback.steps,
        prep_time=primary.prep_time,
        cook_time=primary.cook_time,
        total_time=primary.total_time,
        servings=primary.servings,
        yield_amount=primary.yield_amount,
        source=primary.source or fallback.source,
        tags=primary.tags,
        categories=primary.categories,
        source_hash=primary.source_hash,
    )
