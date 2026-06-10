from __future__ import annotations

import json
import time
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from cronpot.analytics import analyse_vault, build_shopping_list, html_cookbook
from cronpot.config import AutomationConfig
from cronpot.extraction import fetch_html
from cronpot.ingest import prepare_ingested_recipe
from cronpot.llm import LlmError, suggest_ingredient_alias_map
from cronpot.models import Recipe
from cronpot.vault import load_recipes, write_recipe_to_vault


BAR_COLOURS = ["#2f6f4f", "#b86b3d", "#3d6fb8", "#8a6f2f", "#7a4f9e", "#a64f65", "#4f7f83", "#6f7840"]
LLM_ALIAS_CACHE_SECONDS = 900
_llm_alias_cache: dict[tuple[str, str, str, int], tuple[float, dict[str, str]]] = {}


class CronPotHandler(BaseHTTPRequestHandler):
    vault_path: Path = Path("docs")
    config: AutomationConfig = AutomationConfig()

    def do_GET(self) -> None:
        request = urlparse(self.path)
        path = request.path.rstrip("/") or "/"
        query = parse_qs(request.query)

        if path in {"/", "/dashboard"}:
            self._send_html(_dashboard_html(self.vault_path, self.config))
            return
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
            analytics = analyse_vault(self.vault_path, ingredient_aliases=_cached_llm_ingredient_aliases(self.vault_path, self.config))
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
            if self._wants_html():
                self._send_html(_recipe_page_html(recipe_path, recipe))
                return
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
            recipe = prepare_ingested_recipe(fetch_html(url), url, self.vault_path, self.config)
            if not recipe.has_core_content():
                self._send_json({"error": "extraction incomplete"}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            target = write_recipe_to_vault(recipe, self.vault_path)
        except LlmError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)
            return
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

    def _send_html(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _wants_html(self) -> bool:
        accept = self.headers.get("Accept", "")
        return "text/html" in accept.casefold()

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


def _recipe_page_html(recipe_path: Path, recipe: Recipe) -> str:
    content = html_cookbook([(recipe_path, recipe)], title=recipe.title or recipe_path.stem)
    return content.replace(
        "<body>",
        '<body><p style="max-width: 72rem; margin: 1rem auto 0; padding: 0 1rem;"><a href="/dashboard">Dashboard</a></p>',
        1,
    )


def _dashboard_html(vault_path: Path, config: AutomationConfig) -> str:
    analytics = analyse_vault(vault_path, ingredient_aliases=_cached_llm_ingredient_aliases(vault_path, config))
    recipes = load_recipes(vault_path)
    recipe_rows = "\n".join(_dashboard_recipe_row(path, recipe) for path, recipe in recipes[:20])
    recipe_table_body = recipe_rows or '<tr><td colspan="4">No recipes found.</td></tr>'
    missing_source = analytics.recipes_missing_source
    sourced_count = max(analytics.recipe_count - missing_source, 0)

    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en-GB">',
            "<head>",
            '  <meta charset="utf-8">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1">',
            "  <title>CronPot Dashboard</title>",
            "  <style>",
            "    :root { color-scheme: light; --ink: #20241f; --muted: #667064; --line: #d9ded4; --surface: #f7f6ef; --panel: #ffffff; --accent: #38704c; }",
            "    * { box-sizing: border-box; }",
            "    body { margin: 0; background: var(--surface); color: var(--ink); font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; line-height: 1.45; }",
            "    main { max-width: 1180px; margin: 0 auto; padding: 32px 24px 48px; }",
            "    header { display: flex; align-items: end; justify-content: space-between; gap: 24px; border-bottom: 1px solid var(--line); padding-bottom: 18px; }",
            "    h1 { font-size: 32px; margin: 0 0 6px; font-weight: 720; }",
            "    h2 { font-size: 16px; margin: 0 0 14px; font-weight: 700; }",
            "    p { margin: 0; }",
            "    a { color: var(--accent); text-decoration: none; }",
            "    .muted { color: var(--muted); }",
            "    .status { color: var(--accent); font-weight: 700; white-space: nowrap; }",
            "    .metrics { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; margin: 28px 0; }",
            "    .metric { border-bottom: 2px solid var(--line); padding-bottom: 14px; }",
            "    .metric strong { display: block; font-size: 34px; line-height: 1; margin-bottom: 8px; }",
            "    .workspace { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 30px; align-items: start; }",
            "    .section { background: var(--panel); border: 1px solid var(--line); padding: 18px; }",
            "    .section + .section { margin-top: 18px; }",
            "    .bars { display: grid; gap: 12px; }",
            "    .bar { display: grid; grid-template-columns: 120px 1fr 36px; gap: 10px; align-items: center; font-size: 14px; }",
            "    .track { height: 10px; background: #e7eadf; overflow: hidden; }",
            "    .fill { display: block; height: 100%; transition: width .2s ease; }",
            "    table { width: 100%; border-collapse: collapse; font-size: 14px; }",
            "    th { color: var(--muted); font-weight: 650; text-align: left; border-bottom: 1px solid var(--line); padding: 0 0 9px; }",
            "    td { border-bottom: 1px solid var(--line); padding: 11px 8px 11px 0; vertical-align: top; }",
            "    tr { transition: background-color .15s ease; }",
            "    tbody tr:hover { background: #f8faf4; }",
            "    .tagline { display: flex; flex-wrap: wrap; gap: 6px; }",
            "    .tag { background: #edf2e8; color: #38513f; padding: 2px 7px; font-size: 12px; }",
            "    @media (max-width: 820px) { main { padding: 22px 16px 34px; } header, .workspace { display: block; } .metrics { grid-template-columns: 1fr; } .status { display: block; margin-top: 12px; } .bar { grid-template-columns: 96px 1fr 30px; } }",
            "  </style>",
            "</head>",
            "<body>",
            "  <main>",
            "    <header>",
            "      <div>",
            "        <h1>CronPot Dashboard</h1>",
            f"        <p class=\"muted\">Vault: {escape(str(vault_path))}</p>",
            "      </div>",
            "      <p class=\"status\">Service online</p>",
            "    </header>",
            "    <section class=\"metrics\" aria-label=\"Selected KPIs\">",
            f"      {_metric('Recipes', analytics.recipe_count)}",
            f"      {_metric('With source', sourced_count)}",
            f"      {_metric('Missing source', missing_source)}",
            "    </section>",
            "    <div class=\"workspace\">",
            "      <section class=\"section\">",
            "        <h2>Recipes</h2>",
            "        <table>",
            "          <thead><tr><th>Name</th><th>Category</th><th>Tags</th><th>Content</th></tr></thead>",
            f"          <tbody>{recipe_table_body}</tbody>",
            "        </table>",
            "      </section>",
            "      <aside>",
            f"        {_dashboard_bars('Top tags', analytics.tag_counts.most_common(8))}",
            f"        {_dashboard_bars('Top categories', analytics.category_counts.most_common(8))}",
            f"        {_dashboard_bars('Top ingredients', analytics.ingredient_counts.most_common(8))}",
            "      </aside>",
            "    </div>",
            "  </main>",
            "</body>",
            "</html>",
            "",
        ]
    )


def _cached_llm_ingredient_aliases(vault_path: Path, config: AutomationConfig) -> dict[str, str]:
    if not config.llm_auto_normalise_ingredients:
        return {}
    key = (str(vault_path.resolve()), config.llm_base_url, config.llm_model, config.llm_ingredient_limit)
    now = time.monotonic()
    cached = _llm_alias_cache.get(key)
    if cached and now - cached[0] < LLM_ALIAS_CACHE_SECONDS:
        return cached[1]
    try:
        aliases = suggest_ingredient_alias_map(str(vault_path), config, limit=config.llm_ingredient_limit)
    except LlmError:
        aliases = {}
    _llm_alias_cache[key] = (now, aliases)
    return aliases


def _metric(label: str, value: int) -> str:
    return f'<div class="metric"><strong>{value}</strong><span class="muted">{escape(label)}</span></div>'


def _dashboard_bars(title: str, values: list[tuple[str, int]]) -> str:
    if not values:
        body = '<p class="muted">No data yet.</p>'
    else:
        maximum = max(count for _name, count in values) or 1
        rows = []
        for index, (name, count) in enumerate(values):
            width = max(round((count / maximum) * 100), 4)
            colour = BAR_COLOURS[index % len(BAR_COLOURS)]
            rows.append(
                f'<div class="bar"><span>{escape(name)}</span><span class="track"><span class="fill" style="width: {width}%; background: {colour}"></span></span><strong>{count}</strong></div>'
            )
        body = '<div class="bars">' + "".join(rows) + "</div>"
    return f'<section class="section"><h2>{escape(title)}</h2>{body}</section>'


def _dashboard_recipe_row(path: Path, recipe: Recipe) -> str:
    categories = ", ".join(recipe.categories) or "-"
    tags = "".join(f'<span class="tag">{escape(tag)}</span>' for tag in recipe.tags)
    content = f"{len(recipe.ingredients)} ingredients, {len(recipe.steps)} steps"
    return (
        "<tr>"
        f'<td><a href="/recipes/{quote(path.stem)}">{escape(recipe.title or path.stem)}</a></td>'
        f"<td>{escape(categories)}</td>"
        f'<td><span class="tagline">{tags or "-"}</span></td>'
        f"<td>{escape(content)}</td>"
        "</tr>"
    )


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
