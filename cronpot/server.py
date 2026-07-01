from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
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
from cronpot.jobs import clear_jobs, enqueue_ingest_job, get_job, job_to_dict, list_jobs, retry_job, run_pending_jobs
from cronpot.llm import LlmError, suggest_ingredient_alias_map
from cronpot.models import Recipe
from cronpot.vault import load_recipes, write_recipe_to_vault


BAR_COLOURS = ["#2f6f4f", "#b86b3d", "#3d6fb8", "#8a6f2f", "#7a4f9e", "#a64f65", "#4f7f83", "#6f7840"]
LLM_ALIAS_CACHE_SECONDS = 900
ASSET_DIR = Path(__file__).resolve().parent.parent / "assets"
LOGO_PATH = ASSET_DIR / "cronpot-logo.svg"
_llm_alias_cache: dict[tuple[str, str, str, int], tuple[float, dict[str, str]]] = {}


class CronPotHandler(BaseHTTPRequestHandler):
    vault_path: Path = Path("docs")
    config: AutomationConfig = AutomationConfig()
    pairing_code: str = ""
    session_tokens: set[str] = set()

    def do_GET(self) -> None:
        request = urlparse(self.path)
        path = request.path.rstrip("/") or "/"
        query = parse_qs(request.query)

        if path in {"/assets/cronpot-logo.svg", "/favicon.svg", "/favicon.ico"}:
            self._send_static_asset(LOGO_PATH, "image/svg+xml")
            return
        if path == "/mobile":
            self._send_html(_mobile_html(self._is_authorised()))
            return
        if path == "/auth/status":
            self._send_json({"authenticated": self._is_authorised(), "required": bool(self.pairing_code)})
            return
        if path == "/status":
            self._send_json(_app_status(self.vault_path))
            return
        if path in {"/", "/dashboard"}:
            if not self._require_authorised(path):
                return
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
        if not self._require_authorised(path):
            return
        if path == "/analytics":
            analytics = analyse_vault(
                self.vault_path,
                ingredient_aliases=_cached_llm_ingredient_aliases(self.vault_path, self.config),
                config=self.config,
            )
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
        if path == "/jobs":
            self._send_json({"jobs": [job_to_dict(job) for job in list_jobs(self.vault_path)]})
            return
        if path.startswith("/jobs/"):
            job_id = unquote(path.removeprefix("/jobs/")).strip()
            job = get_job(self.vault_path, job_id)
            if job is None:
                self._send_json({"error": "job not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json(job_to_dict(job))
            return
        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        request = urlparse(self.path)
        path = request.path.rstrip("/") or "/"
        if path == "/auth":
            payload = self._read_json()
            code = str(payload.get("code") or "").strip()
            if not self.pairing_code or secrets.compare_digest(code, self.pairing_code):
                token = secrets.token_urlsafe(24)
                self.session_tokens.add(token)
                self._send_json(
                    {"authenticated": True},
                    headers={"Set-Cookie": f"cronpot_session={token}; Path=/; SameSite=Strict; HttpOnly"},
                )
            else:
                self._send_json({"error": "invalid code"}, status=HTTPStatus.UNAUTHORIZED)
            return
        if not self._require_authorised(path):
            return
        if path == "/jobs/run":
            processed = run_pending_jobs(self.vault_path, self.config, workers=self.config.worker_count)
            self._send_json({"jobs": [job_to_dict(job) for job in processed]})
            return
        if path == "/jobs/clear":
            cleared = clear_jobs(self.vault_path)
            self._send_json({"cleared": cleared, "jobs": []})
            return
        if path in {"/k8s/github/pull", "/k8s/github/push"}:
            self._run_mobile_k8s_sync(path.rsplit("/", 1)[-1])
            return
        if path.startswith("/jobs/") and path.endswith("/retry"):
            job_id = unquote(path.removeprefix("/jobs/").removesuffix("/retry")).strip()
            try:
                job = retry_job(self.vault_path, job_id)
            except FileNotFoundError:
                self._send_json({"error": "job not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json(job_to_dict(job))
            return

        if path not in {"/ingest", "/jobs/ingest"}:
            self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return

        payload = self._read_json()
        url = str(payload.get("url") or "").strip()
        if not url:
            self._send_json({"error": "url is required"}, status=HTTPStatus.BAD_REQUEST)
            return
        if path == "/jobs/ingest" or _truthy(str(payload.get("background") or "")):
            job = enqueue_ingest_job(self.vault_path, url)
            self._send_json(job_to_dict(job), status=HTTPStatus.ACCEPTED)
            return

        try:
            recipe = prepare_ingested_recipe(fetch_html(url), url, self.vault_path, self.config)
            if not recipe.has_core_content():
                self._send_json({"error": "extraction incomplete"}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            target = write_recipe_to_vault(recipe, self.vault_path, config=self.config)
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

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK, headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, body: str, status: HTTPStatus = HTTPStatus.OK, headers: dict[str, str] | None = None) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(encoded)

    def _send_static_asset(self, path: Path, content_type: str) -> None:
        if not path.exists() or not path.is_file():
            self._send_json({"error": "asset not found"}, status=HTTPStatus.NOT_FOUND)
            return
        payload = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(payload)

    def _run_mobile_k8s_sync(self, direction: str) -> None:
        namespace = os.environ.get("CRONPOT_K8S_NAMESPACE", "cronpot-local")
        command = [sys.executable, "-m", "cronpot", "k8s", "github", direction, "--namespace", namespace]
        if direction == "push":
            command.extend(["--seed-from", str(self.vault_path)])
        try:
            result = subprocess.run(command, text=True, capture_output=True, check=False, timeout=240)
        except subprocess.TimeoutExpired:
            self._send_json(
                {
                    "error": f"Kubernetes GitHub {direction} timed out. Check that the local cluster is running and the namespace exists.",
                    "namespace": namespace,
                },
                status=HTTPStatus.GATEWAY_TIMEOUT,
            )
            return
        except OSError as exc:
            self._send_json(
                {
                    "error": f"Could not run Kubernetes GitHub {direction}: {exc}",
                    "namespace": namespace,
                },
                status=HTTPStatus.BAD_GATEWAY,
            )
            return
        output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        if result.returncode != 0:
            self._send_json(
                {
                    "error": _mobile_k8s_error(direction, namespace, output),
                    "namespace": namespace,
                    "output": output,
                },
                status=HTTPStatus.BAD_GATEWAY,
            )
            return
        self._send_json({"status": "complete", "direction": direction, "namespace": namespace, "output": output})

    def _wants_html(self) -> bool:
        accept = self.headers.get("Accept", "")
        return "text/html" in accept.casefold()

    def _filtered_recipes(self, query: dict[str, list[str]]) -> list[tuple[Path, Recipe]]:
        recipes = load_recipes(self.vault_path, self.config)
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
            return [("", match) for match in load_recipes(self.vault_path, self.config)]

        names = _query_values(query, "recipe")
        names.extend(_query_values(query, "recipes"))
        if not names:
            return None
        return [(name, self._find_recipe(name)) for name in names]

    def _find_recipe(self, name: str) -> tuple[Path, Recipe] | None:
        requested = Path(name).stem.casefold()
        if not requested or "/" in name or "\\" in name:
            return None

        for recipe_path, recipe in load_recipes(self.vault_path, self.config):
            keys = {
                recipe_path.name.casefold(),
                recipe_path.stem.casefold(),
                recipe.title.casefold(),
            }
            if requested in keys or name.casefold() in keys:
                return recipe_path, recipe
        return None

    def _is_authorised(self) -> bool:
        if not self.pairing_code:
            return True
        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer ") and secrets.compare_digest(auth_header.removeprefix("Bearer ").strip(), self.pairing_code):
            return True
        if secrets.compare_digest(self.headers.get("X-CronPot-Code", "").strip(), self.pairing_code):
            return True
        cookies = self.headers.get("Cookie", "")
        for cookie in cookies.split(";"):
            name, _, value = cookie.strip().partition("=")
            if name == "cronpot_session" and value in self.session_tokens:
                return True
        return False

    def _require_authorised(self, path: str) -> bool:
        if self._is_authorised():
            return True
        if self._wants_html():
            self._send_html(_mobile_html(False), status=HTTPStatus.UNAUTHORIZED)
        else:
            self._send_json(
                {"error": "pairing code required", "mobile": "/mobile"},
                status=HTTPStatus.UNAUTHORIZED,
            )
        return False


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


def _mobile_html(authorised: bool) -> str:
    app_display = "block" if authorised else "none"
    auth_display = "none" if authorised else "block"
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en-GB">',
            "<head>",
            '  <meta charset="utf-8">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1">',
            '  <link rel="icon" href="/favicon.svg" type="image/svg+xml">',
            "  <title>CronPot Mobile</title>",
            "  <style>",
            "    :root { color-scheme: light; --ink: #20241f; --muted: #626b60; --line: #d8ddd2; --surface: #f7f6ef; --panel: #ffffff; --accent: #2f6f4f; --danger: #9e3f3f; }",
            "    * { box-sizing: border-box; }",
            "    body { margin: 0; background: var(--surface); color: var(--ink); font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; line-height: 1.45; }",
            "    main { max-width: 780px; margin: 0 auto; padding: 22px 16px 42px; }",
            "    header { display: flex; align-items: center; gap: 14px; border-bottom: 1px solid var(--line); padding-bottom: 16px; margin-bottom: 18px; }",
            "    .logo { width: 52px; height: 52px; object-fit: contain; flex: 0 0 auto; }",
            "    h1 { font-size: 30px; line-height: 1.05; margin: 0 0 8px; }",
            "    h2 { font-size: 18px; margin: 0 0 10px; }",
            "    p { margin: 0; }",
            "    a { color: var(--accent); text-decoration: none; }",
            "    label { display: block; font-size: 13px; color: var(--muted); margin-bottom: 6px; }",
            "    input, select, button { width: 100%; min-height: 46px; border: 1px solid var(--line); background: var(--panel); color: var(--ink); font: inherit; padding: 10px 12px; border-radius: 0; }",
            "    button { border-color: var(--accent); background: var(--accent); color: white; font-weight: 700; }",
            "    button.secondary { background: transparent; color: var(--accent); }",
            "    section { border-top: 1px solid var(--line); padding-top: 18px; margin-top: 18px; }",
            "    .row { display: grid; gap: 10px; }",
            "    .status { min-height: 22px; color: var(--muted); margin-top: 8px; }",
            "    .status-strip { display: flex; flex-wrap: wrap; gap: 8px; margin: 14px 0 0; }",
            "    .status-pill { display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--line); background: var(--panel); color: var(--muted); padding: 5px 8px; font-size: 12px; }",
            "    .dot { width: 9px; height: 9px; border-radius: 999px; background: #9a9a9a; flex: 0 0 auto; }",
            "    .dot.green { background: #2f7d46; }",
            "    .dot.amber { background: #b97822; }",
            "    .dot.red { background: #b33f3f; }",
            "    .error { color: var(--danger); }",
            "    .success { color: var(--accent); }",
            "    .jobs, .items, .recipes, .tips { display: grid; gap: 8px; margin-top: 10px; }",
            "    .job, .item, .recipe, details { background: var(--panel); border: 1px solid var(--line); padding: 10px 12px; }",
            "    .job { display: grid; grid-template-columns: minmax(0, 1fr) 42px; gap: 10px; align-items: center; }",
            "    .job.no-action { grid-template-columns: 1fr; }",
            "    .job-title { overflow-wrap: anywhere; }",
            "    .icon-button { width: 40px; min-height: 40px; padding: 8px; display: inline-flex; align-items: center; justify-content: center; }",
            "    .icon-button svg { width: 18px; height: 18px; display: block; }",
            "    .recipe { display: grid; grid-template-columns: 24px 1fr; gap: 8px; align-items: start; }",
            "    .recipe input { min-height: 22px; width: 22px; padding: 0; margin-top: 2px; }",
            "    summary { cursor: pointer; font-weight: 700; }",
            "    code { background: #eef1e7; padding: 1px 4px; }",
            "    .muted { color: var(--muted); }",
            "    .toolbar { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }",
            "    @media (min-width: 680px) { .row.two { grid-template-columns: 1fr auto; align-items: end; } .row.two button { width: auto; min-width: 140px; } }",
            "  </style>",
            "</head>",
            "<body>",
            "  <main>",
            "    <header>",
            "      <img class=\"logo\" src=\"/assets/cronpot-logo.svg\" alt=\"CronPot logo\">",
            "      <div>",
            "        <h1>CronPot</h1>",
            "        <p class=\"muted\">Mobile tools for ingest jobs and shopping lists.</p>",
            "        <div id=\"statusStrip\" class=\"status-strip\"><span class=\"status-pill\"><span class=\"dot amber\"></span>Checking status</span></div>",
            "      </div>",
            "    </header>",
            f"    <section id=\"auth\" style=\"display: {auth_display};\">",
            "      <h2>Pair this device</h2>",
            "      <div class=\"row two\">",
            "        <div><label for=\"code\">Six digit code</label><input id=\"code\" inputmode=\"numeric\" autocomplete=\"one-time-code\" maxlength=\"6\" placeholder=\"123456\"></div>",
            "        <button id=\"pair\">Connect</button>",
            "      </div>",
            "      <p id=\"authStatus\" class=\"status\">Enter the code shown in the CronPot terminal.</p>",
            "    </section>",
            f"    <div id=\"app\" style=\"display: {app_display};\">",
            "      <section>",
            "        <h2>Queue recipe ingest</h2>",
            "        <div class=\"row two\">",
            "          <div><label for=\"url\">Recipe URL</label><input id=\"url\" type=\"url\" placeholder=\"https://...\"></div>",
            "          <button id=\"queue\">Queue</button>",
            "        </div>",
            "        <p id=\"ingestStatus\" class=\"status\"></p>",
            "      </section>",
            "      <section>",
            "        <h2>Vault sync</h2>",
            "        <div class=\"toolbar\"><button id=\"pullVault\">Pull vault</button><button id=\"pushVault\" class=\"secondary\">Push vault</button></div>",
            "        <p id=\"syncStatus\" class=\"status\">Uses Kubernetes namespace cronpot-local unless CRONPOT_K8S_NAMESPACE is set.</p>",
            "      </section>",
            "      <section>",
            "        <h2>Recent jobs</h2>",
            "        <div class=\"toolbar\"><button id=\"runJobs\">Run jobs</button><button id=\"clearJobs\" class=\"secondary\">Clear jobs</button></div>",
            "        <button id=\"refreshJobs\" class=\"secondary\">Refresh jobs</button>",
            "        <div id=\"jobs\" class=\"jobs\"><p class=\"muted\">No jobs loaded.</p></div>",
            "      </section>",
            "      <section>",
            "        <h2>Shopping list</h2>",
            "        <div class=\"row\">",
            "          <div><label for=\"search\">Find recipes</label><input id=\"search\" placeholder=\"Search by name, category, or tag\"></div>",
            "          <div class=\"toolbar\"><button id=\"buildList\">Build list</button><button id=\"copyList\" class=\"secondary\">Copy</button></div>",
            "        </div>",
            "        <div id=\"recipes\" class=\"recipes\"><p class=\"muted\">Loading recipes...</p></div>",
            "        <div id=\"shopping\" class=\"items\"></div>",
            "      </section>",
            "      <section>",
            "        <p><a href=\"/dashboard\">Open dashboard</a></p>",
            "      </section>",
            "      <section>",
            "        <h2>Command tips</h2>",
            "        <div class=\"tips\">",
            "          <details><summary>Start local mobile access</summary><p class=\"muted\"><code>cronpot start --vault docs --lan</code> starts the dashboard, prints a pairing code, and exposes <code>/mobile</code> on your local network.</p></details>",
            "          <details><summary>Import a recipe URL</summary><p class=\"muted\"><code>cronpot ingest URL --vault docs</code> extracts, normalises, and optionally rewrites a web recipe before writing Markdown.</p></details>",
            "          <details><summary>Run queued jobs</summary><p class=\"muted\"><code>cronpot jobs run --vault docs</code> processes queued ingest work that was created from the mobile UI or API.</p></details>",
            "          <details><summary>Sync the Kubernetes vault</summary><p class=\"muted\"><code>cronpot k8s github pull</code> brings the backing vault repo into Kubernetes. <code>cronpot k8s github push</code> writes Kubernetes vault changes back to that repo.</p></details>",
            "        </div>",
            "      </section>",
            "    </div>",
            "  </main>",
            "  <script>",
            "    const state = { recipes: [], shoppingText: '' };",
            "    const $ = (id) => document.getElementById(id);",
            "    async function request(path, options = {}) {",
            "      const response = await fetch(path, { credentials: 'same-origin', ...options });",
            "      const text = await response.text();",
            "      const data = text ? JSON.parse(text) : {};",
            "      if (!response.ok) throw new Error(data.error || response.statusText);",
            "      return data;",
            "    }",
            "    function renderStatus(data) {",
            "      const items = [data.service, data.k8s].filter(Boolean);",
            "      $('statusStrip').innerHTML = items.map(item => '<span class=\"status-pill\" title=\"' + escapeAttr(item.detail || '') + '\"><span class=\"dot ' + escapeAttr(item.level) + '\"></span>' + escapeHtml(item.label) + '</span>').join('');",
            "    }",
            "    async function loadStatus() {",
            "      try { renderStatus(await request('/status')); }",
            "      catch (error) { $('statusStrip').innerHTML = '<span class=\"status-pill\"><span class=\"dot red\"></span>Status unavailable</span>'; }",
            "    }",
            "    async function pair() {",
            "      $('authStatus').textContent = 'Checking code...';",
            "      try {",
            "        await request('/auth', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code: $('code').value }) });",
            "        $('auth').style.display = 'none'; $('app').style.display = 'block';",
            "        await loadAll();",
            "      } catch (error) { $('authStatus').innerHTML = '<span class=\"error\">' + error.message + '</span>'; }",
            "    }",
            "    async function queueIngest() {",
            "      const url = $('url').value.trim();",
            "      if (!url) return;",
            "      $('ingestStatus').textContent = 'Queueing...';",
            "      try {",
            "        const job = await request('/jobs/ingest', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url }) });",
            "        $('ingestStatus').textContent = 'Queued ' + job.id;",
            "        $('url').value = '';",
            "        await loadJobs();",
            "      } catch (error) { $('ingestStatus').innerHTML = '<span class=\"error\">' + error.message + '</span>'; }",
            "    }",
            "    async function syncVault(direction) {",
            "      $('syncStatus').textContent = (direction === 'pull' ? 'Pulling' : 'Pushing') + ' vault via Kubernetes...';",
            "      try {",
            "        const result = await request('/k8s/github/' + direction, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });",
            "        const summary = result.output ? result.output.split('\\n').slice(-2).join(' ') : 'Sync complete.';",
            "        $('syncStatus').innerHTML = '<span class=\"success\">' + escapeHtml(summary) + '</span>';",
            "        await Promise.all([loadRecipes(), loadJobs()]);",
            "      } catch (error) {",
            "        $('syncStatus').innerHTML = '<span class=\"error\">' + escapeHtml(error.message) + '</span>';",
            "      }",
            "    }",
            "    async function runJobs() {",
            "      $('jobs').innerHTML = '<p class=\"muted\">Running queued jobs...</p>';",
            "      try {",
            "        const result = await request('/jobs/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });",
            "        await loadJobs();",
            "        $('jobs').insertAdjacentHTML('afterbegin', '<p class=\"success\">Processed ' + result.jobs.length + ' job' + (result.jobs.length === 1 ? '' : 's') + '.</p>');",
            "      } catch (error) { $('jobs').innerHTML = '<p class=\"error\">' + escapeHtml(error.message) + '</p>'; }",
            "    }",
            "    async function clearQueuedJobs() {",
            "      try {",
            "        const result = await request('/jobs/clear', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });",
            "        $('jobs').innerHTML = '<p class=\"muted\">Cleared ' + result.cleared + ' job' + (result.cleared === 1 ? '' : 's') + '.</p>';",
            "      } catch (error) { $('jobs').innerHTML = '<p class=\"error\">' + escapeHtml(error.message) + '</p>'; }",
            "    }",
            "    async function retryJob(jobId) {",
            "      try {",
            "        await request('/jobs/' + encodeURIComponent(jobId) + '/retry', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });",
            "        await loadJobs();",
            "      } catch (error) { $('jobs').insertAdjacentHTML('afterbegin', '<p class=\"error\">' + escapeHtml(error.message) + '</p>'); }",
            "    }",
            "    async function loadJobs() {",
            "      const data = await request('/jobs');",
            "      const jobs = data.jobs.slice().sort((a, b) => b.updated_at - a.updated_at).slice(0, 8);",
            "      $('jobs').innerHTML = jobs.length ? jobs.map(job => { const retry = job.status === 'failed' || job.status === 'running'; return '<div class=\"job ' + (retry ? '' : 'no-action') + '\"><div class=\"job-title\"><strong>' + job.status + '</strong><br><span>' + escapeHtml(job.title || job.url || job.id) + '</span><br><span class=\"muted\">Attempts: ' + job.attempts + '</span></div>' + (retry ? '<button class=\"secondary icon-button retry-job\" data-job-id=\"' + escapeAttr(job.id) + '\" title=\"Retry job\" aria-label=\"Retry job\"><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"M20 11a8 8 0 1 0-2.34 5.66\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\" stroke-linecap=\"round\"/><path d=\"M20 4v7h-7\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/></svg></button>' : '') + '</div>'; }).join('') : '<p class=\"muted\">No queued jobs.</p>';",
            "    }",
            "    async function loadRecipes() {",
            "      const data = await request('/recipes');",
            "      state.recipes = data.recipes;",
            "      renderRecipes();",
            "    }",
            "    function renderRecipes() {",
            "      const query = $('search').value.trim().toLowerCase();",
            "      const recipes = state.recipes.filter(recipe => !query || [recipe.title, recipe.name, ...(recipe.categories || []), ...(recipe.tags || [])].join(' ').toLowerCase().includes(query)).slice(0, 50);",
            "      $('recipes').innerHTML = recipes.length ? recipes.map(recipe => '<label class=\"recipe\"><input type=\"checkbox\" value=\"' + escapeAttr(recipe.name) + '\"><span><strong>' + escapeHtml(recipe.title || recipe.name) + '</strong><br><span class=\"muted\">' + escapeHtml((recipe.categories || []).join(', ')) + '</span></span></label>').join('') : '<p class=\"muted\">No matching recipes.</p>';",
            "    }",
            "    async function buildShoppingList() {",
            "      const selected = Array.from(document.querySelectorAll('#recipes input:checked')).map(input => input.value);",
            "      if (!selected.length) { $('shopping').innerHTML = '<p class=\"muted\">Select at least one recipe.</p>'; return; }",
            "      const query = selected.map(name => 'recipe=' + encodeURIComponent(name)).join('&');",
            "      const data = await request('/shopping-list?' + query);",
            "      state.shoppingText = 'Shopping list\\n' + data.items.map(item => '- ' + item).join('\\n');",
            "      $('shopping').innerHTML = data.items.map(item => '<div class=\"item\">' + escapeHtml(item) + '</div>').join('');",
            "    }",
            "    async function copyShoppingList() {",
            "      if (!state.shoppingText) await buildShoppingList();",
            "      if (state.shoppingText) await navigator.clipboard.writeText(state.shoppingText);",
            "    }",
            "    function escapeHtml(value) { return String(value || '').replace(/[&<>\"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[char])); }",
            "    function escapeAttr(value) { return escapeHtml(value).replace(/'/g, '&#39;'); }",
            "    async function loadAll() { await Promise.all([loadStatus(), loadRecipes(), loadJobs()]); }",
            "    $('pair').addEventListener('click', pair);",
            "    $('code').addEventListener('keydown', event => { if (event.key === 'Enter') pair(); });",
            "    $('queue').addEventListener('click', queueIngest);",
            "    $('pullVault').addEventListener('click', () => syncVault('pull'));",
            "    $('pushVault').addEventListener('click', () => syncVault('push'));",
            "    $('runJobs').addEventListener('click', runJobs);",
            "    $('clearJobs').addEventListener('click', clearQueuedJobs);",
            "    $('refreshJobs').addEventListener('click', loadJobs);",
            "    $('jobs').addEventListener('click', event => { const button = event.target.closest('.retry-job'); if (button) retryJob(button.dataset.jobId); });",
            "    $('search').addEventListener('input', renderRecipes);",
            "    $('buildList').addEventListener('click', buildShoppingList);",
            "    $('copyList').addEventListener('click', copyShoppingList);",
            "    loadStatus();",
            f"    if ({str(authorised).lower()}) loadAll();",
            "    setInterval(() => { if ($('app').style.display !== 'none') loadJobs().catch(() => {}); }, 5000);",
            "    setInterval(loadStatus, 15000);",
            "  </script>",
            "</body>",
            "</html>",
            "",
        ]
    )


def _dashboard_html(vault_path: Path, config: AutomationConfig) -> str:
    analytics = analyse_vault(vault_path, ingredient_aliases=_cached_llm_ingredient_aliases(vault_path, config), config=config)
    recipes = load_recipes(vault_path, config)
    jobs = list_jobs(vault_path)
    recent_jobs = sorted(jobs, key=lambda job: job.updated_at, reverse=True)[:10]
    recipe_rows = "\n".join(_dashboard_recipe_row(path, recipe) for path, recipe in recipes)
    recipe_table_body = recipe_rows or '<tr><td colspan="4">No recipes found.</td></tr>'
    missing_source = analytics.recipes_missing_source
    sourced_count = max(analytics.recipe_count - missing_source, 0)
    open_jobs = sum(1 for job in jobs if job.status in {"pending", "running"})
    status = _app_status(vault_path)
    status_strip = _status_strip(status)

    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en-GB">',
            "<head>",
            '  <meta charset="utf-8">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1">',
            '  <link rel="icon" href="/favicon.svg" type="image/svg+xml">',
            "  <title>CronPot Dashboard</title>",
            "  <style>",
            "    :root { color-scheme: light; --ink: #20241f; --muted: #667064; --line: #d9ded4; --surface: #f7f6ef; --panel: #ffffff; --accent: #38704c; }",
            "    * { box-sizing: border-box; }",
            "    body { margin: 0; background: var(--surface); color: var(--ink); font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; line-height: 1.45; }",
            "    main { max-width: 1180px; margin: 0 auto; padding: 32px 24px 48px; }",
            "    header { display: flex; align-items: end; justify-content: space-between; gap: 24px; border-bottom: 1px solid var(--line); padding-bottom: 18px; }",
            "    .brand { display: flex; align-items: center; gap: 16px; min-width: 0; }",
            "    .logo { width: 60px; height: 60px; object-fit: contain; flex: 0 0 auto; }",
            "    h1 { font-size: 32px; margin: 0 0 6px; font-weight: 720; }",
            "    h2 { font-size: 16px; margin: 0 0 14px; font-weight: 700; }",
            "    p { margin: 0; }",
            "    a { color: var(--accent); text-decoration: none; }",
            "    .muted { color: var(--muted); }",
            "    .status { color: var(--accent); font-weight: 700; white-space: nowrap; }",
            "    .status-strip { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }",
            "    .status-pill { display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--line); background: var(--panel); color: var(--muted); padding: 5px 8px; font-size: 12px; font-weight: 650; white-space: nowrap; }",
            "    .dot { width: 9px; height: 9px; border-radius: 999px; background: #9a9a9a; flex: 0 0 auto; }",
            "    .dot.green { background: #2f7d46; }",
            "    .dot.amber { background: #b97822; }",
            "    .dot.red { background: #b33f3f; }",
            "    .metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 18px; margin: 28px 0; }",
            "    .metric { border-bottom: 2px solid var(--line); padding-bottom: 14px; }",
            "    .metric strong { display: block; font-size: 34px; line-height: 1; margin-bottom: 8px; }",
            "    .workspace { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 30px; align-items: start; }",
            "    .section { background: var(--panel); border: 1px solid var(--line); padding: 18px; }",
            "    .section + .section { margin-top: 18px; }",
            "    .section-head { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-bottom: 14px; }",
            "    .section-head h2 { margin: 0; }",
            "    .search { min-height: 36px; width: min(280px, 100%); border: 1px solid var(--line); background: var(--panel); color: var(--ink); font: inherit; padding: 7px 9px; }",
            "    .table-scroll { max-height: 560px; overflow: auto; border-top: 1px solid var(--line); }",
            "    .bars { display: grid; gap: 12px; }",
            "    .bar { display: grid; grid-template-columns: 120px 1fr 36px; gap: 10px; align-items: center; font-size: 14px; }",
            "    .track { height: 10px; background: #e7eadf; overflow: hidden; }",
            "    .fill { display: block; height: 100%; transition: width .2s ease; }",
            "    table { width: 100%; border-collapse: collapse; font-size: 14px; }",
            "    th { color: var(--muted); font-weight: 650; text-align: left; border-bottom: 1px solid var(--line); padding: 9px 0; position: sticky; top: 0; background: var(--panel); z-index: 1; }",
            "    td { border-bottom: 1px solid var(--line); padding: 11px 8px 11px 0; vertical-align: top; }",
            "    tr { transition: background-color .15s ease; }",
            "    tbody tr:hover { background: #f8faf4; }",
            "    .tagline { display: flex; flex-wrap: wrap; gap: 6px; }",
            "    .tag { background: #edf2e8; color: #38513f; padding: 2px 7px; font-size: 12px; }",
            "    @media (max-width: 820px) { main { padding: 22px 16px 34px; } header, .workspace { display: block; } .brand { align-items: flex-start; } .logo { width: 50px; height: 50px; } .metrics { grid-template-columns: 1fr; } .status, .status-strip { display: flex; justify-content: flex-start; margin-top: 12px; } .section-head { display: block; } .search { margin-top: 10px; width: 100%; } .bar { grid-template-columns: 96px 1fr 30px; } }",
            "  </style>",
            "</head>",
            "<body>",
            "  <main>",
            "    <header>",
            "      <div class=\"brand\">",
            "        <img class=\"logo\" src=\"/assets/cronpot-logo.svg\" alt=\"CronPot logo\">",
            "        <div>",
            "          <h1>CronPot Dashboard</h1>",
            f"          <p class=\"muted\">Vault: {escape(str(vault_path))}</p>",
            "        </div>",
            "      </div>",
            f"      <div id=\"dashboardStatus\">{status_strip}</div>",
            "    </header>",
            "    <section class=\"metrics\" aria-label=\"Selected KPIs\">",
            f"      {_metric('Recipes', analytics.recipe_count)}",
            f"      {_metric('With source', sourced_count)}",
            f"      {_metric('Missing source', missing_source)}",
            f"      {_metric('Open jobs', open_jobs)}",
            "    </section>",
            "    <div class=\"workspace\">",
            "      <div>",
            "      <section class=\"section\">",
            "        <div class=\"section-head\"><h2>Recipes</h2><input id=\"recipeFilter\" class=\"search\" placeholder=\"Search recipes\"></div>",
            "        <div class=\"table-scroll\">",
            "        <table>",
            "          <thead><tr><th>Name</th><th>Category</th><th>Tags</th><th>Content</th></tr></thead>",
            f"          <tbody id=\"recipeRows\">{recipe_table_body}</tbody>",
            "        </table>",
            "        </div>",
            "      </section>",
            f"      {_dashboard_jobs(recent_jobs)}",
            "      </div>",
            "      <aside>",
            f"        {_dashboard_bars('Top tags', analytics.tag_counts.most_common(8))}",
            f"        {_dashboard_bars('Top categories', analytics.category_counts.most_common(8))}",
            f"        {_dashboard_bars('Top ingredients', analytics.ingredient_counts.most_common(8))}",
            "      </aside>",
            "    </div>",
            "  </main>",
            "  <script>",
            "    const $ = (id) => document.getElementById(id);",
            "    function escapeHtml(value) { return String(value || '').replace(/[&<>\"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[char])); }",
            "    function escapeAttr(value) { return escapeHtml(value).replace(/'/g, '&#39;'); }",
            "    function renderStatus(data) {",
            "      const items = [data.service, data.k8s].filter(Boolean);",
            "      $('dashboardStatus').innerHTML = '<div class=\"status-strip\">' + items.map(item => '<span class=\"status-pill\" title=\"' + escapeAttr(item.detail || '') + '\"><span class=\"dot ' + escapeAttr(item.level) + '\"></span>' + escapeHtml(item.label) + '</span>').join('') + '</div>';",
            "    }",
            "    async function loadStatus() {",
            "      try { const response = await fetch('/status', { credentials: 'same-origin' }); if (response.ok) renderStatus(await response.json()); } catch (_error) {}",
            "    }",
            "    function filterRecipes() {",
            "      const query = $('recipeFilter').value.trim().toLowerCase();",
            "      document.querySelectorAll('#recipeRows tr').forEach(row => { row.style.display = !query || row.dataset.search.includes(query) ? '' : 'none'; });",
            "    }",
            "    $('recipeFilter').addEventListener('input', filterRecipes);",
            "    setInterval(loadStatus, 15000);",
            "    loadStatus();",
            "  </script>",
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
    searchable = " ".join([recipe.title or path.stem, categories, " ".join(recipe.tags), content]).casefold()
    return (
        f'<tr data-search="{escape(searchable, quote=True)}">'
        f'<td><a href="/recipes/{quote(path.stem)}">{escape(recipe.title or path.stem)}</a></td>'
        f"<td>{escape(categories)}</td>"
        f'<td><span class="tagline">{tags or "-"}</span></td>'
        f"<td>{escape(content)}</td>"
        "</tr>"
    )


def _dashboard_jobs(jobs: list[object]) -> str:
    if not jobs:
        body = '<p class="muted">No queued jobs.</p>'
    else:
        rows: list[str] = []
        for job in sorted(jobs, key=lambda item: getattr(item, "updated_at", 0), reverse=True):
            status = escape(getattr(job, "status", ""))
            title = getattr(job, "title", "") or getattr(job, "url", "")
            path = getattr(job, "path", "")
            error = getattr(job, "error", "")
            detail = error or title
            if path and title:
                detail = f'<a href="/recipes/{quote(Path(path).stem)}">{escape(title)}</a>'
            else:
                detail = escape(detail)
            rows.append(
                "<tr>"
                f"<td>{status}</td>"
                f"<td>{detail}</td>"
                f"<td>{getattr(job, 'attempts', 0)}</td>"
                "</tr>"
            )
        body = (
            "<table>"
            "<thead><tr><th>Status</th><th>Job</th><th>Attempts</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody>"
            "</table>"
        )
    return f'<section class="section"><h2>Ingest jobs</h2>{body}</section>'


def _status_strip(status: dict[str, dict[str, str]]) -> str:
    parts = []
    for item in (status.get("service"), status.get("k8s")):
        if not item:
            continue
        level = escape(item.get("level", "amber"))
        label = escape(item.get("label", "Status"))
        detail = escape(item.get("detail", ""), quote=True)
        parts.append(f'<span class="status-pill" title="{detail}"><span class="dot {level}"></span>{label}</span>')
    return '<div class="status-strip">' + "".join(parts) + "</div>"


def _app_status(vault_path: Path) -> dict[str, dict[str, str]]:
    jobs = list_jobs(vault_path)
    failed_jobs = sum(1 for job in jobs if job.status == "failed")
    open_jobs = sum(1 for job in jobs if job.status in {"pending", "running"})
    if not vault_path.exists() or not vault_path.is_dir():
        service = {"level": "red", "label": "Vault offline", "detail": f"Vault folder is unavailable: {vault_path}"}
    elif failed_jobs:
        service = {"level": "red", "label": f"{failed_jobs} failed job{'s' if failed_jobs != 1 else ''}", "detail": "Review or retry failed ingest jobs."}
    elif open_jobs:
        service = {"level": "amber", "label": f"{open_jobs} open job{'s' if open_jobs != 1 else ''}", "detail": "Queued or running ingest jobs are present."}
    else:
        service = {"level": "green", "label": "Service ready", "detail": "Vault is available and no ingest jobs need attention."}
    return {"service": service, "k8s": _k8s_status_indicator()}


def _k8s_status_indicator(namespace: str = "cronpot-local") -> dict[str, str]:
    def probe(command: list[str]) -> tuple[bool, str]:
        try:
            result = subprocess.run(command, text=True, capture_output=True, check=False, timeout=2)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)
        return result.returncode == 0, (result.stdout or result.stderr).strip()

    namespace_ok, namespace_output = probe(["kubectl", "get", "namespace", namespace, "-o", "name"])
    if not namespace_ok:
        cluster_ok, cluster_output = probe(["kubectl", "cluster-info"])
        if not cluster_ok:
            return {"level": "red", "label": "K8s offline", "detail": namespace_output or cluster_output or "Cluster is not reachable."}
        return {"level": "amber", "label": "K8s namespace missing", "detail": namespace_output or f"Namespace {namespace} was not found."}
    api_ok, api_output = probe(
        [
            "kubectl",
            "-n",
            namespace,
            "get",
            "pod",
            "-l",
            "app.kubernetes.io/component=api",
            "-o",
            "jsonpath={.items[?(@.status.phase=='Running')].metadata.name}",
        ]
    )
    if not api_ok or not api_output.strip():
        return {"level": "amber", "label": "K8s no API pod", "detail": api_output or "CronPot API pod is not running."}
    return {"level": "green", "label": "K8s ready", "detail": f"Running API pod: {api_output.split()[0]}"}


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


def _mobile_k8s_error(direction: str, namespace: str, output: str) -> str:
    details = output.strip()
    hint = (
        f"Could not {direction} the GitHub vault through Kubernetes namespace {namespace}. "
        "Check that Docker Desktop or your cluster is running, the namespace exists, and the GitHub vault Secret is configured."
    )
    return f"{hint} {details}" if details else hint


def run_server(host: str, port: int, vault_path: Path | str, config: AutomationConfig, pairing_code: str = "") -> None:
    CronPotHandler.vault_path = Path(vault_path)
    CronPotHandler.config = config
    CronPotHandler.pairing_code = pairing_code
    CronPotHandler.session_tokens = set()
    server = ThreadingHTTPServer((host, port), CronPotHandler)
    server.serve_forever()
