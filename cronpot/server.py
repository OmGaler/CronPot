from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from cronpot.analytics import analyse_vault, build_shopping_list
from cronpot.config import AutomationConfig
from cronpot.extraction import extract_recipe, fetch_html
from cronpot.models import Recipe
from cronpot.normalisation import normalise_recipe
from cronpot.vault import load_recipes, write_recipe_to_vault


class CronPotHandler(BaseHTTPRequestHandler):
    vault_path: Path = Path("docs")
    config: AutomationConfig = AutomationConfig()

    def do_GET(self) -> None:
        request = urlparse(self.path)
        path = request.path.rstrip("/") or "/"
        query = parse_qs(request.query)

        if path == "/healthz":
            self._send_json({"status": "ok"})
            return
        if path == "/readyz":
            if self.vault_path.exists() and self.vault_path.is_dir():
                self._send_json({"status": "ready"})
            else:
                self._send_json({"status": "vault unavailable"}, status=HTTPStatus.SERVICE_UNAVAILABLE)
            return
        if path == "/analytics":
            analytics = analyse_vault(self.vault_path)
            self._send_json(
                {
                    "recipe_count": analytics.recipe_count,
                    "recipes_missing_source": analytics.recipes_missing_source,
                    "tags": dict(analytics.tag_counts),
                    "categories": dict(analytics.category_counts),
                    "ingredients": dict(analytics.ingredient_counts),
                }
            )
            return
        if path == "/recipes":
            recipes = self._filtered_recipes(query)
            self._send_json(
                {
                    "count": len(recipes),
                    "recipes": [_recipe_summary(recipe_path, recipe) for recipe_path, recipe in recipes],
                }
            )
            return
        if path.startswith("/recipes/"):
            requested = unquote(path.removeprefix("/recipes/")).strip()
            match = self._find_recipe(requested)
            if match is None:
                self._send_json({"error": "recipe not found"}, status=HTTPStatus.NOT_FOUND)
                return
            recipe_path, recipe = match
            self._send_json(_recipe_detail(recipe_path, recipe))
            return
        if path == "/shopping-list":
            selected = self._selected_recipes(query)
            if selected is None:
                self._send_json(
                    {"error": "pass all=true or one or more recipe/recipes query parameters"},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            missing = [name for name, match in selected if match is None]
            if missing:
                self._send_json({"error": "recipe not found", "missing": missing}, status=HTTPStatus.NOT_FOUND)
                return

            recipes = [match for _name, match in selected if match is not None]
            items = build_shopping_list([recipe for _path, recipe in recipes])
            self._send_json(
                {
                    "count": len(items),
                    "recipes": [_recipe_summary(recipe_path, recipe) for recipe_path, recipe in recipes],
                    "items": items,
                }
            )
            return
        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        request = urlparse(self.path)
        path = request.path.rstrip("/") or "/"
        if path != "/ingest":
            self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return

        payload = self._read_json()
        url = str(payload.get("url") or "").strip()
        if not url:
            self._send_json({"error": "url is required"}, status=HTTPStatus.BAD_REQUEST)
            return

        try:
            recipe = normalise_recipe(extract_recipe(fetch_html(url), url), self.config)
            if not recipe.has_core_content():
                self._send_json({"error": "extraction incomplete"}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            target = write_recipe_to_vault(recipe, self.vault_path)
        except OSError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)
            return

        self._send_json({"path": str(target), "title": recipe.title}, status=HTTPStatus.CREATED)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _filtered_recipes(self, query: dict[str, list[str]]) -> list[tuple[Path, Recipe]]:
        recipes = load_recipes(self.vault_path)
        tags = {value.casefold() for value in _query_values(query, "tag")}
        categories = {value.casefold() for value in _query_values(query, "category")}

        if not tags and not categories:
            return recipes

        filtered: list[tuple[Path, Recipe]] = []
        for recipe_path, recipe in recipes:
            recipe_tags = {tag.casefold() for tag in recipe.tags}
            recipe_categories = {category.casefold() for category in recipe.categories}
            if tags and not tags.issubset(recipe_tags):
                continue
            if categories and not categories.intersection(recipe_categories):
                continue
            filtered.append((recipe_path, recipe))
        return filtered

    def _selected_recipes(self, query: dict[str, list[str]]) -> list[tuple[str, tuple[Path, Recipe] | None]] | None:
        if _truthy(query.get("all", [""])[0]):
            return [("", match) for match in load_recipes(self.vault_path)]

        names = _query_values(query, "recipe")
        names.extend(_query_values(query, "recipes"))
        if not names:
            return None
        return [(name, self._find_recipe(name)) for name in names]

    def _find_recipe(self, name: str) -> tuple[Path, Recipe] | None:
        requested = Path(name).stem.casefold()
        if not requested or "/" in name or "\\" in name:
            return None

        for recipe_path, recipe in load_recipes(self.vault_path):
            keys = {
                recipe_path.name.casefold(),
                recipe_path.stem.casefold(),
                recipe.title.casefold(),
            }
            if requested in keys or name.casefold() in keys:
                return recipe_path, recipe
        return None


def _recipe_summary(recipe_path: Path, recipe: Recipe) -> dict[str, Any]:
    return {
        "name": recipe_path.stem,
        "file": recipe_path.name,
        "title": recipe.title,
        "tags": recipe.tags,
        "categories": recipe.categories,
        "source": recipe.source,
        "prep_time": recipe.prep_time,
        "cook_time": recipe.cook_time,
        "total_time": recipe.total_time,
        "servings": recipe.servings,
        "yield": recipe.yield_amount,
        "ingredient_count": len(recipe.ingredients),
        "step_count": len(recipe.steps),
    }


def _recipe_detail(recipe_path: Path, recipe: Recipe) -> dict[str, Any]:
    detail = _recipe_summary(recipe_path, recipe)
    detail.update(
        {
            "ingredients": recipe.ingredients,
            "steps": recipe.steps,
            "source_hash": recipe.source_hash,
        }
    )
    return detail


def _query_values(query: dict[str, list[str]], key: str) -> list[str]:
    values: list[str] = []
    for raw_value in query.get(key, []):
        for value in raw_value.split(","):
            clean = value.strip()
            if clean:
                values.append(clean)
    return values


def _truthy(value: str) -> bool:
    return value.strip().casefold() in {"1", "true", "yes", "y", "on"}


def run_server(host: str, port: int, vault_path: Path | str, config: AutomationConfig) -> None:
    CronPotHandler.vault_path = Path(vault_path)
    CronPotHandler.config = config
    server = ThreadingHTTPServer((host, port), CronPotHandler)
    server.serve_forever()
