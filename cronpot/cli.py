from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from cronpot.analytics import analyse_vault, build_shopping_list, bundle_markdown, html_cookbook, pdf_cookbook
from cronpot.config import load_config
from cronpot.extraction import fetch_html
from cronpot.ingest import prepare_ingested_recipe
from cronpot.importing import import_markdown_vault
from cronpot.jobs import enqueue_ingest_job, job_to_dict, list_jobs, run_pending_jobs
from cronpot.llm import LlmError, suggest_ingredient_alias_map, suggest_ingredient_aliases
from cronpot.models import Recipe
from cronpot.server import run_server
from cronpot.vault import (
    commit_paths,
    find_recipe_file,
    load_recipes,
    parse_markdown_recipe,
    render_markdown,
    validate_vault,
    write_recipe_to_vault,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Automate an Obsidian recipe vault.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    config_parent = argparse.ArgumentParser(add_help=False)
    config_parent.add_argument("--config", default=None, help="Path to an optional CronPot TOML config file.")

    ingest = subparsers.add_parser("ingest", parents=[config_parent], help="Extract a recipe URL into vault Markdown.")
    ingest.add_argument("url")
    ingest.add_argument("--vault", default=None, help="Path to the Obsidian/MkDocs recipe vault.")
    ingest.add_argument("--html-file", help="Use a saved HTML file instead of fetching the URL.")
    ingest.add_argument("--allow-incomplete", action="store_true", help="Write Markdown even if extraction is partial.")
    ingest.add_argument("--dry-run", action="store_true", help="Print generated Markdown instead of writing it.")
    ingest.add_argument("--no-overwrite", action="store_true", help="Fail if the source already exists in the vault.")
    ingest.add_argument("--commit", action="store_true", help="Commit the generated recipe when the workspace is a Git repo.")
    ingest.add_argument("--title", help="Use this recipe title instead of prompting for the extracted suggestion.")
    ingest.set_defaults(func=cmd_ingest)

    import_vault = subparsers.add_parser("import-vault", parents=[config_parent], help="Batch import Markdown recipes from an Obsidian vault or cloned repo.")
    import_vault.add_argument("source", help="Source Markdown file, Obsidian vault, or local cloned repository.")
    import_vault.add_argument("--vault", default=None, help="Target recipe vault.")
    import_vault.add_argument("--no-recursive", action="store_true", help="Only import top-level Markdown files.")
    import_vault.add_argument("--allow-incomplete", action="store_true", help="Import Markdown files that do not have both ingredients and method steps.")
    import_vault.add_argument("--dry-run", action="store_true", help="Report what would be imported without writing files.")
    import_vault.add_argument("--no-overwrite", action="store_true", help="Skip sources that already exist in the target vault.")
    import_vault.add_argument("--commit", action="store_true", help="Commit imported recipes when the workspace is a Git repo.")
    import_vault.set_defaults(func=cmd_import_vault)

    validate = subparsers.add_parser("validate", parents=[config_parent], help="Check recipe files for schema gaps.")
    validate.add_argument("--vault", default=None)
    validate.set_defaults(func=cmd_validate)

    analytics = subparsers.add_parser("analytics", parents=[config_parent], help="Summarise cookbook tags, categories, and ingredients.")
    analytics.add_argument("--vault", default=None)
    analytics.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    analytics.add_argument("--top", type=int, default=10, help="Number of top values to show.")
    analytics.set_defaults(func=cmd_analytics)

    normalise = subparsers.add_parser("normalise", parents=[config_parent], help="Suggest or apply normalisation improvements.")
    normalise_subparsers = normalise.add_subparsers(dest="normalise_command", required=True)
    normalise_ingredients = normalise_subparsers.add_parser("ingredients", help="Suggest canonical ingredient aliases with the configured LLM.")
    normalise_ingredients.add_argument("--vault", default=None)
    normalise_ingredients.add_argument("--suggest", action="store_true", help="Print suggested aliases without changing config or recipes.")
    normalise_ingredients.add_argument("--limit", type=int, default=80, help="Number of common ingredient keys to send to the LLM.")
    normalise_ingredients.add_argument("--model", help="Override the configured Ollama model.")
    normalise_ingredients.add_argument("--base-url", help="Override the configured Ollama base URL.")
    normalise_ingredients.set_defaults(func=cmd_normalise_ingredients)

    jobs = subparsers.add_parser("jobs", parents=[config_parent], help="Queue and inspect background jobs.")
    jobs_subparsers = jobs.add_subparsers(dest="jobs_command", required=True)
    jobs_ingest = jobs_subparsers.add_parser("ingest", help="Queue a URL ingest job.")
    jobs_ingest.add_argument("url")
    jobs_ingest.add_argument("--vault", default=None)
    jobs_ingest.set_defaults(func=cmd_jobs_ingest)
    jobs_list = jobs_subparsers.add_parser("list", help="List queued ingest jobs.")
    jobs_list.add_argument("--vault", default=None)
    jobs_list.set_defaults(func=cmd_jobs_list)
    jobs_run = jobs_subparsers.add_parser("run", help="Run pending ingest jobs.")
    jobs_run.add_argument("--vault", default=None)
    jobs_run.add_argument("--workers", type=int, default=None, help="Number of parallel workers.")
    jobs_run.add_argument("--limit", type=int, default=None, help="Maximum jobs to process.")
    jobs_run.set_defaults(func=cmd_jobs_run)

    worker = subparsers.add_parser("worker", parents=[config_parent], help="Process queued background jobs.")
    worker.add_argument("--vault", default=None)
    worker.add_argument("--workers", type=int, default=None, help="Number of parallel workers.")
    worker.add_argument("--limit", type=int, default=None, help="Maximum jobs to process before exiting.")
    worker.add_argument("--once", action="store_true", help="Process the current queue and exit.")
    worker.set_defaults(func=cmd_worker)

    export = subparsers.add_parser("export", parents=[config_parent], help="Export recipes as HTML, Markdown, or a shopping list.")
    export.add_argument("recipes", nargs="*", help="Recipe names, Markdown files, or stems.")
    export.add_argument("--vault", default=None)
    export.add_argument("--all", action="store_true", help="Use every recipe in the vault.")
    export.add_argument(
        "--format",
        choices=["html", "markdown", "pdf", "shopping-list"],
        default="html",
        help="Export format.",
    )
    export.add_argument("--title", default="CronPot Cookbook", help="HTML document title.")
    export.add_argument("--output", help="Write the export to a file instead of stdout.")
    export.set_defaults(func=cmd_export)

    shopping = subparsers.add_parser("shopping-list", parents=[config_parent], help="Build a WhatsApp-ready shopping list.")
    shopping.add_argument("recipes", nargs="*", help="Recipe names, Markdown files, or stems.")
    shopping.add_argument("--vault", default=None)
    shopping.add_argument("--all", action="store_true", help="Use every recipe in the vault.")
    shopping.add_argument("--output", help="Write the list to a file instead of stdout.")
    shopping.set_defaults(func=cmd_shopping_list)

    bundle = subparsers.add_parser("bundle", parents=[config_parent], help="Export selected recipes as one Markdown bundle.")
    bundle.add_argument("recipes", nargs="*", help="Recipe names, Markdown files, or stems.")
    bundle.add_argument("--vault", default=None)
    bundle.add_argument("--all", action="store_true", help="Use every recipe in the vault.")
    bundle.add_argument("--output", help="Write the bundle to a file instead of stdout.")
    bundle.set_defaults(func=cmd_bundle)

    html = subparsers.add_parser("html", parents=[config_parent], help="Export selected recipes as a standalone HTML cookbook.")
    html.add_argument("recipes", nargs="*", help="Recipe names, Markdown files, or stems.")
    html.add_argument("--vault", default=None)
    html.add_argument("--all", action="store_true", help="Use every recipe in the vault.")
    html.add_argument("--title", default="CronPot Cookbook", help="Document title.")
    html.add_argument("--output", help="Write the HTML to a file instead of stdout.")
    html.set_defaults(func=cmd_html)

    serve = subparsers.add_parser("serve", parents=[config_parent], help="Run the ingestion and analytics HTTP service.")
    serve.add_argument("--vault", default=None)
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8080)
    serve.set_defaults(func=cmd_serve)

    start = subparsers.add_parser("start", parents=[config_parent], help="Start the ingestion and analytics HTTP service.")
    start.add_argument("--vault", default=None)
    start.add_argument("--host", default="0.0.0.0")
    start.add_argument("--port", type=int, default=8080)
    start.set_defaults(func=cmd_serve)

    return parser


def cmd_ingest(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    vault = _vault_path(args, config)
    html_text = Path(args.html_file).read_text(encoding="utf-8") if args.html_file else fetch_html(args.url)
    recipe = prepare_ingested_recipe(html_text, args.url, vault, config)
    recipe.title = _ingest_title(recipe.title, args.title, prompt=not args.dry_run)

    if not args.allow_incomplete and not recipe.has_core_content():
        missing = []
        if not recipe.title:
            missing.append("title")
        if not recipe.ingredients:
            missing.append("ingredients")
        if not recipe.steps:
            missing.append("method steps")
        print(f"Extraction incomplete; missing {', '.join(missing)}. Re-run with --allow-incomplete to write a draft.", file=sys.stderr)
        return 2

    if args.dry_run:
        print(render_markdown(recipe, config), end="")
        return 0

    target = write_recipe_to_vault(recipe, vault, overwrite=not args.no_overwrite, config=config)
    print(f"Wrote {target}")

    if args.commit:
        result = commit_paths(Path.cwd(), [target], f"Add recipe: {recipe.title}")
        if result.committed:
            print(result.output)
        else:
            print(f"Git commit skipped: {result.skipped_reason}")
    return 0


def cmd_import_vault(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    vault = _vault_path(args, config)
    result = import_markdown_vault(
        args.source,
        vault,
        config=config,
        recursive=not args.no_recursive,
        allow_incomplete=args.allow_incomplete,
        overwrite=not args.no_overwrite,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        print(f"Would import {len(result.previews)} Markdown recipe file(s).")
    else:
        print(f"Imported {len(result.imported)} Markdown recipe file(s).")
    for skip in result.skipped:
        print(f"Skipped {skip.path}: {skip.reason}")

    if not result.imported and not result.previews and not result.skipped:
        print("No Markdown files found.")
        return 1

    if args.commit and result.imported:
        commit = commit_paths(Path.cwd(), result.imported, "Import recipe vault")
        if commit.committed:
            print(commit.output)
        else:
            print(f"Git commit skipped: {commit.skipped_reason}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    issues = validate_vault(_vault_path(args, config), config)
    if not issues:
        print("No validation issues found.")
        return 0
    for issue in issues:
        print(f"{issue.path}: {issue.message}")
    return 1


def cmd_analytics(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    vault = _vault_path(args, config)
    analytics = analyse_vault(vault, ingredient_aliases=_llm_ingredient_aliases(vault, config), config=config)
    if args.json:
        print(
            json.dumps(
                {
                    "recipe_count": analytics.recipe_count,
                    "recipes_missing_source": analytics.recipes_missing_source,
                    "tags": dict(analytics.tag_counts.most_common(args.top)),
                    "categories": dict(analytics.category_counts.most_common(args.top)),
                    "ingredients": dict(analytics.ingredient_counts.most_common(args.top)),
                },
                indent=2,
            )
        )
        return 0

    print(f"Recipes: {analytics.recipe_count}")
    print(f"Missing source: {analytics.recipes_missing_source}")
    _print_counter("Top tags", analytics.tag_counts, args.top)
    _print_counter("Top categories", analytics.category_counts, args.top)
    _print_counter("Top ingredients", analytics.ingredient_counts, args.top)
    return 0


def _llm_ingredient_aliases(vault_path: str, config: object) -> dict[str, str]:
    if not getattr(config, "llm_auto_normalise_ingredients", False):
        return {}
    try:
        return suggest_ingredient_alias_map(vault_path, config, limit=getattr(config, "llm_ingredient_limit", 120))
    except LlmError as exc:
        print(f"LLM ingredient normalisation skipped: {exc}", file=sys.stderr)
        return {}


def cmd_normalise_ingredients(args: argparse.Namespace) -> int:
    if not args.suggest:
        raise ValueError("Only --suggest is currently supported.")
    config = load_config(args.config)
    if args.model:
        config.llm_model = args.model
    if args.base_url:
        config.llm_base_url = args.base_url.rstrip("/")
    suggestions = suggest_ingredient_aliases(_vault_path(args, config), config, limit=args.limit)
    if not suggestions:
        print("No ingredient alias suggestions found.")
        return 0
    print("Suggested ingredient aliases:")
    for suggestion in suggestions:
        print(f"- {suggestion.source} -> {suggestion.canonical} ({suggestion.count})")
    return 0


def cmd_jobs_ingest(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    job = enqueue_ingest_job(_vault_path(args, config), args.url)
    print(json.dumps(job_to_dict(job), indent=2))
    return 0


def cmd_jobs_list(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    jobs = [job_to_dict(job) for job in list_jobs(_vault_path(args, config))]
    print(json.dumps(jobs, indent=2))
    return 0


def cmd_jobs_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    processed = run_pending_jobs(
        _vault_path(args, config),
        config,
        workers=args.workers or config.worker_count,
        limit=args.limit,
    )
    print(json.dumps([job_to_dict(job) for job in processed], indent=2))
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    vault = _vault_path(args, config)
    workers = args.workers or config.worker_count
    while True:
        processed = run_pending_jobs(vault, config, workers=workers, limit=args.limit)
        for job in processed:
            print(f"{job.id}: {job.status} {job.title or job.error}")
        if args.once or args.limit is not None:
            return 0
        time.sleep(5)


def cmd_shopping_list(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    recipes = _select_recipes(_vault_path(args, config), args.recipes, args.all, config)
    items = build_shopping_list([recipe for _path, recipe in recipes])
    output = "Shopping list\n" + "\n".join(f"- {item}" for item in items) + "\n"
    _write_or_print(output, args.output)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    recipes = _select_recipes(_vault_path(args, config), args.recipes, args.all, config)
    if args.format == "markdown":
        output = bundle_markdown(recipes)
    elif args.format == "shopping-list":
        items = build_shopping_list([recipe for _path, recipe in recipes])
        output = "Shopping list\n" + "\n".join(f"- {item}" for item in items) + "\n"
    elif args.format == "pdf":
        _write_bytes(pdf_cookbook(recipes, title=args.title), args.output or _default_export_path(recipes, "pdf"))
        return 0
    else:
        output = html_cookbook(recipes, title=args.title)
    _write_or_print(output, args.output)
    return 0


def cmd_bundle(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    recipes = _select_recipes(_vault_path(args, config), args.recipes, args.all, config)
    output = bundle_markdown(recipes)
    _write_or_print(output, args.output)
    return 0


def cmd_html(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    recipes = _select_recipes(_vault_path(args, config), args.recipes, args.all, config)
    output = html_cookbook(recipes, title=args.title)
    _write_or_print(output, args.output)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    host = args.host
    port = args.port
    print(f"CronPot serving on http://{host}:{port}", flush=True)
    run_server(host, port, _vault_path(args, config), config)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, LlmError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _vault_path(args: argparse.Namespace, config: object) -> str:
    return args.vault or config.default_vault


def _select_recipes(vault_path: str, requested: list[str], use_all: bool, config: object | None = None) -> list[tuple[Path, Recipe]]:
    if use_all:
        return load_recipes(vault_path, config)  # type: ignore[arg-type]
    if not requested:
        raise ValueError("Pass recipe names or use --all.")
    selected = []
    for item in requested:
        path = find_recipe_file(vault_path, item)
        selected.append((path, parse_markdown_recipe(path, config=config)))  # type: ignore[arg-type]
    return selected


def _print_counter(title: str, counter: object, limit: int) -> None:
    print(title + ":")
    for key, count in counter.most_common(limit):
        print(f"- {key}: {count}")


def _write_or_print(output: str, destination: str | None) -> None:
    if destination:
        Path(destination).write_text(output, encoding="utf-8", newline="\n")
        print(f"Wrote {destination}")
    else:
        print(output, end="")


def _write_bytes(output: bytes, destination: str) -> None:
    Path(destination).write_bytes(output)
    print(f"Wrote {destination}")


def _ingest_title(suggestion: str, override: str | None, prompt: bool = True) -> str:
    suggested = suggestion.strip() or "Untitled Recipe"
    if override is not None:
        return override.strip() or suggested
    if not prompt or not sys.stdin.isatty():
        return suggested

    response = input(f"Recipe name [{suggested}]: ").strip()
    return response or suggested


def _default_export_path(recipes: list[tuple[Path, Recipe]], extension: str) -> str:
    if len(recipes) == 1:
        name = recipes[0][1].title or recipes[0][0].stem
    elif recipes:
        name = "cookbook"
    else:
        name = "cronpot-export"
    clean = "".join(character for character in name if character not in '<>:"/\\|?*').strip(" .")
    clean = re.sub(r"\s+", " ", clean)
    return f"{clean or 'cronpot-export'}.{extension}"
