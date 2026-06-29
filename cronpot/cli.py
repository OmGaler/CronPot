from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from cronpot.analytics import analyse_vault, build_shopping_list, bundle_markdown, html_cookbook, pdf_cookbook
from cronpot.config import load_config
from cronpot.extraction import fetch_html
from cronpot.ingest import prepare_ingested_recipe
from cronpot.importing import import_markdown_vault
from cronpot.jobs import clear_jobs, enqueue_ingest_job, job_to_dict, list_jobs, retry_job, run_pending_jobs
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
    jobs_retry = jobs_subparsers.add_parser("retry", help="Retry a failed or stale ingest job.")
    jobs_retry.add_argument("job_id")
    jobs_retry.add_argument("--vault", default=None)
    jobs_retry.set_defaults(func=cmd_jobs_retry)
    jobs_clear = jobs_subparsers.add_parser("clear", help="Delete all stored ingest jobs.")
    jobs_clear.add_argument("--vault", default=None)
    jobs_clear.set_defaults(func=cmd_jobs_clear)

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
    serve.add_argument("--lan", action="store_true", help="Expose the mobile UI on the local network with a six digit pairing code.")
    serve.add_argument("--auth-code", help="Use this pairing code instead of generating one. Intended for repeatable local testing.")
    serve.set_defaults(func=cmd_serve)

    start = subparsers.add_parser("start", parents=[config_parent], help="Start the ingestion and analytics HTTP service.")
    start.add_argument("--vault", default=None)
    start.add_argument("--host", default="0.0.0.0")
    start.add_argument("--port", type=int, default=8080)
    start.add_argument("--lan", action="store_true", help="Expose the mobile UI on the local network with a six digit pairing code.")
    start.add_argument("--auth-code", help="Use this pairing code instead of generating one. Intended for repeatable local testing.")
    start.set_defaults(func=cmd_serve)

    k8s = subparsers.add_parser("k8s", aliases=["k"], help="Operate CronPot Kubernetes helpers.")
    k8s_subparsers = k8s.add_subparsers(dest="k8s_command", required=True)

    k8s_status = k8s_subparsers.add_parser("status", help="Show local Kubernetes and CronPot namespace status.")
    k8s_status.add_argument("--namespace", default="cronpot-local")
    k8s_status.set_defaults(func=cmd_k8s_status)

    k8s_sync_back = k8s_subparsers.add_parser("sync-back", help="Copy the Kubernetes PVC vault back to a local folder.")
    k8s_sync_back.add_argument("target", help="Local vault folder to update.")
    k8s_sync_back.add_argument("--namespace", default="cronpot-local")
    k8s_sync_back.add_argument("--commit", action="store_true", help="Commit the synced target when it is inside the current Git repo.")
    k8s_sync_back.add_argument("--message", default="Sync CronPot vault from Kubernetes")
    k8s_sync_back.set_defaults(func=cmd_k8s_sync_back)

    k8s_push_local = k8s_subparsers.add_parser("push-local", help="Copy a local vault folder into the Kubernetes PVC.")
    k8s_push_local.add_argument("source", help="Local vault folder to copy.")
    k8s_push_local.add_argument("--namespace", default="cronpot-local")
    k8s_push_local.add_argument("--destination", help="PVC destination path. Defaults to /vault/<source folder name>.")
    k8s_push_local.add_argument("--clear", action="store_true", help="Clear the destination folder before copying.")
    k8s_push_local.set_defaults(func=cmd_k8s_push_local)

    k8s_github = k8s_subparsers.add_parser("github", help="Configure and run GitHub-backed vault sync.")
    k8s_github_subparsers = k8s_github.add_subparsers(dest="github_command", required=True)

    k8s_github_secret = k8s_github_subparsers.add_parser("secret", help="Create or update the GitHub vault sync Secret.")
    k8s_github_secret.add_argument("--namespace", default="cronpot-local")
    k8s_github_secret.add_argument("--repo", required=True, help="HTTPS GitHub repository URL for the vault.")
    k8s_github_secret.add_argument("--branch", default="main")
    k8s_github_secret.add_argument("--path", default=".", help="Path inside the repository that contains the vault.")
    k8s_github_secret.add_argument("--username", default="x-access-token")
    k8s_github_secret.add_argument("--author-name", default="CronPot")
    k8s_github_secret.add_argument("--author-email", default="cronpot@example.local")
    k8s_github_secret.set_defaults(func=cmd_k8s_github_secret)

    k8s_github_pull = k8s_github_subparsers.add_parser("pull", help="Pull the GitHub vault repository into the Kubernetes PVC.")
    k8s_github_pull.add_argument("--namespace", default="cronpot-local")
    k8s_github_pull.add_argument("--timeout", type=int, default=180)
    k8s_github_pull.add_argument("--keep-job", action="store_true")
    k8s_github_pull.add_argument("--sync-back", metavar="TARGET", help="After pulling into Kubernetes, copy the PVC vault back to this local folder.")
    k8s_github_pull.add_argument("--commit-sync-back", action="store_true", help="Commit the synced target after --sync-back.")
    k8s_github_pull.add_argument("--sync-message", default="Sync CronPot vault from Kubernetes")
    k8s_github_pull.set_defaults(func=cmd_k8s_github_pull)

    k8s_github_push = k8s_github_subparsers.add_parser("push", help="Push the Kubernetes PVC vault back to GitHub.")
    k8s_github_push.add_argument("--namespace", default="cronpot-local")
    k8s_github_push.add_argument("--message", default="Sync CronPot vault from Kubernetes")
    k8s_github_push.add_argument("--timeout", type=int, default=180)
    k8s_github_push.add_argument("--keep-job", action="store_true")
    k8s_github_push.add_argument("--seed-from", default="docs", metavar="SOURCE", help="Copy a local vault into its matching folder under /vault before pushing to GitHub. Defaults to docs.")
    k8s_github_push.add_argument("--no-seed", action="store_true", help="Push the existing Kubernetes PVC without copying a local vault first.")
    k8s_github_push.set_defaults(func=cmd_k8s_github_push)

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


def cmd_jobs_retry(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    job = retry_job(_vault_path(args, config), args.job_id)
    print(json.dumps(job_to_dict(job), indent=2))
    return 0


def cmd_jobs_clear(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    cleared = clear_jobs(_vault_path(args, config))
    print(f"Cleared {cleared} job{'s' if cleared != 1 else ''}.")
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
    pairing_code = _pairing_code(args) if args.lan or args.auth_code or os.environ.get("CRONPOT_AUTH_CODE") else ""
    print(f"CronPot serving on http://{host}:{port}", flush=True)
    if pairing_code:
        print(f"CronPot mobile pairing code: {pairing_code}", flush=True)
        for address in _local_network_addresses():
            print(f"CronPot mobile URL: http://{address}:{port}/mobile", flush=True)
    run_server(host, port, _vault_path(args, config), config, pairing_code=pairing_code)
    return 0


def cmd_k8s_github_secret(args: argparse.Namespace) -> int:
    token = os.environ.get("CRONPOT_GITHUB_TOKEN", "")
    if not token:
        raise ValueError("Set CRONPOT_GITHUB_TOKEN to a GitHub token that can read and write the vault repository.")
    _validate_vault_repository(args.repo)
    _run(["kubectl", "create", "namespace", args.namespace, "--dry-run=client", "-o", "yaml"], stdout_to_stdin=["kubectl", "apply", "-f", "-"])
    _run_with_input(["kubectl", "apply", "-f", "-"], _github_secret_yaml(args, token))
    print(f"Configured GitHub vault Secret cronpot-vault-github in namespace {args.namespace}.")
    return 0


def cmd_k8s_sync_back(args: argparse.Namespace) -> int:
    _sync_k8s_vault_back(args.target, args.namespace, args.commit, args.message)
    return 0


def cmd_k8s_push_local(args: argparse.Namespace) -> int:
    _push_local_vault_to_k8s(args.source, args.namespace, args.destination, args.clear)
    return 0


def cmd_k8s_github_pull(args: argparse.Namespace) -> int:
    _run_github_sync_job("pull", args.namespace, "Sync CronPot vault from GitHub", args.timeout, args.keep_job)
    _print_k8s_vault_summary(args.namespace)
    if args.sync_back:
        _sync_k8s_vault_back(args.sync_back, args.namespace, args.commit_sync_back, args.sync_message)
    return 0


def cmd_k8s_github_push(args: argparse.Namespace) -> int:
    if not args.no_seed:
        _seed_local_vault_for_github_push(args.seed_from, args.namespace)
    _run_github_sync_job("push", args.namespace, args.message, args.timeout, args.keep_job)
    return 0


def cmd_k8s_status(args: argparse.Namespace) -> int:
    _print_k8s_status(args.namespace)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, LlmError, subprocess.CalledProcessError) as exc:
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


def _pairing_code(args: argparse.Namespace) -> str:
    configured_code = args.auth_code or os.environ.get("CRONPOT_AUTH_CODE", "")
    if configured_code:
        code = re.sub(r"\D+", "", configured_code)
        if len(code) != 6:
            raise ValueError("CronPot auth code must contain exactly six digits.")
        return code
    return f"{secrets.randbelow(1_000_000):06d}"


def _validate_vault_repository(repo: str) -> None:
    if _repository_url_contains_credentials(repo):
        raise ValueError("The vault repository URL must not contain credentials. Use CRONPOT_GITHUB_TOKEN or --token instead.")

    project_repo = _git_remote_url("origin")
    if project_repo and _normalise_repository_url(repo) == _normalise_repository_url(project_repo):
        raise ValueError("The vault repository matches this CronPot project repository. Use a separate recipe vault repository.")


def _repository_url_contains_credentials(repo: str) -> bool:
    return bool(re.match(r"^[a-z][a-z0-9+.-]*://[^/@]+@", repo, flags=re.IGNORECASE))


def _git_remote_url(name: str) -> str:
    result = subprocess.run(
        ["git", "remote", "get-url", name],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _normalise_repository_url(repo: str) -> str:
    value = repo.strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    ssh_match = re.fullmatch(r"git@([^:]+):(.+)", value, flags=re.IGNORECASE)
    if ssh_match:
        return f"{ssh_match.group(1).casefold()}/{ssh_match.group(2).casefold()}"
    https_match = re.fullmatch(r"https?://([^/]+)/(.+)", value, flags=re.IGNORECASE)
    if https_match:
        return f"{https_match.group(1).casefold()}/{https_match.group(2).casefold()}"
    return value.casefold()


def _local_network_addresses() -> list[str]:
    addresses: list[str] = []
    try:
        hostname = socket.gethostname()
        for result in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
            address = result[4][0]
            if not address.startswith("127.") and address not in addresses:
                addresses.append(address)
    except OSError:
        return []
    return addresses


def _run(command: list[str], stdout_to_stdin: list[str] | None = None, cwd: Path | str | None = None) -> subprocess.CompletedProcess[str]:
    if stdout_to_stdin is None:
        return subprocess.run(command, text=True, check=True, cwd=cwd)
    first = subprocess.run(command, text=True, capture_output=True, check=True, cwd=cwd)
    return subprocess.run(stdout_to_stdin, input=first.stdout, text=True, check=True, cwd=cwd)


def _run_with_input(command: list[str], input_text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, input=input_text, text=True, check=True)


def _kubectl_output(command: list[str]) -> str:
    return subprocess.run(command, text=True, capture_output=True, check=True).stdout.strip()


def _kubectl_probe(command: list[str]) -> tuple[bool, str]:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    output = (result.stdout or result.stderr).strip()
    return result.returncode == 0, output


def _print_k8s_status(namespace: str) -> None:
    print(f"CronPot Kubernetes status for namespace {namespace}")
    kubectl_ok, kubectl_output = _kubectl_probe(["kubectl", "version", "--client"])
    print(f"- kubectl: {'available' if kubectl_ok else 'unavailable'}")
    if not kubectl_ok and kubectl_output:
        print(f"  {kubectl_output}")
        return

    cluster_ok, cluster_output = _kubectl_probe(["kubectl", "cluster-info"])
    print(f"- cluster: {'reachable' if cluster_ok else 'unreachable'}")
    if not cluster_ok:
        if cluster_output:
            print(f"  {cluster_output}")
        return

    namespace_ok, namespace_output = _kubectl_probe(["kubectl", "get", "namespace", namespace, "-o", "name"])
    print(f"- namespace: {'found' if namespace_ok else 'missing'}")
    if not namespace_ok:
        if namespace_output:
            print(f"  {namespace_output}")
        return

    api_ok, api_pod = _kubectl_probe(
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
    api_name = api_pod.split()[0] if api_ok and api_pod.strip() else ""
    print(f"- api pod: {api_name or 'not running'}")
    worker_ok, workers = _kubectl_probe(["kubectl", "-n", namespace, "get", "deployment", "cronpot-worker", "-o", "jsonpath={.status.readyReplicas}"])
    print(f"- worker ready replicas: {workers.strip() if worker_ok and workers.strip() else '0'}")
    if not api_name:
        return

    recipes_ok, recipes = _kubectl_probe(["kubectl", "-n", namespace, "exec", api_name, "--", "sh", "-c", "find /vault -name '*.md' | wc -l"])
    jobs_ok, jobs = _kubectl_probe(["kubectl", "-n", namespace, "exec", api_name, "--", "sh", "-c", "find /vault/.cronpot/jobs -name '*.json' 2>/dev/null | wc -l"])
    print(f"- vault recipes: {recipes.strip() if recipes_ok else 'unknown'}")
    print(f"- stored jobs: {jobs.strip() if jobs_ok else 'unknown'}")


def _running_api_pod(namespace: str) -> str:
    pod = _kubectl_output(
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
    if not pod:
        raise RuntimeError(f"No running CronPot API pod found in namespace {namespace}.")
    return pod.split()[0]


def _print_k8s_vault_summary(namespace: str) -> None:
    pod = _running_api_pod(namespace)
    top_level = _kubectl_output(["kubectl", "-n", namespace, "exec", pod, "--", "sh", "-c", "find /vault -maxdepth 1 -name '*.md' | wc -l"])
    total = _kubectl_output(["kubectl", "-n", namespace, "exec", pod, "--", "sh", "-c", "find /vault -name '*.md' | wc -l"])
    print(f"Kubernetes vault now has {top_level.strip()} top-level Markdown file(s) and {total.strip()} total Markdown file(s).")
    if total.strip() != top_level.strip():
        print("CronPot indexes nested Markdown recipes too; use the GitHub secret --path option only if you want the PVC rooted at a specific repository subfolder.")


def _sync_k8s_vault_back(target: str, namespace: str, commit: bool, message: str) -> None:
    pod = _running_api_pod(namespace)
    target_path = Path(target)
    target_path.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cronpot-k8s-vault-") as temp_dir:
        staging = Path(temp_dir) / "vault"
        staging.mkdir()
        _run(["kubectl", "-n", namespace, "cp", f"{pod}:/vault/.", "vault", "--container", "api"], cwd=temp_dir)
        source = _sync_back_source(staging, target_path)
        copied = _copy_synced_vault(source, target_path)
    print(f"Synced {copied} file(s) from {namespace}/{pod}:/vault to {target_path}.")
    if commit:
        result = commit_paths(Path.cwd(), [target_path], message)
        if result.committed:
            print(result.output)
        else:
            print(f"Git commit skipped: {result.skipped_reason}")


def _push_local_vault_to_k8s(source: str, namespace: str, destination: str | None, clear: bool) -> None:
    pod = _running_api_pod(namespace)
    source_path = Path(source)
    if not source_path.is_dir():
        raise FileNotFoundError(f"Source vault folder does not exist: {source}")
    remote_path = _k8s_vault_destination(source_path, destination)
    _run(["kubectl", "-n", namespace, "exec", pod, "--", "mkdir", "-p", remote_path])
    if clear:
        _run(
            [
                "kubectl",
                "-n",
                namespace,
                "exec",
                pod,
                "--",
                "sh",
                "-c",
                f"find {_sh_single_quote(remote_path)} -mindepth 1 -maxdepth 1 -exec rm -rf {{}} +",
            ]
        )
    with tempfile.TemporaryDirectory(prefix="cronpot-local-vault-") as temp_dir:
        staging = Path(temp_dir) / source_path.name
        staging.mkdir()
        _copy_local_vault_for_push(source_path, staging)
        _run(["kubectl", "-n", namespace, "cp", f"{source_path.name}/.", f"{pod}:{remote_path}", "--container", "api"], cwd=temp_dir)
    count = _kubectl_output(
        [
            "kubectl",
            "-n",
            namespace,
            "exec",
            pod,
            "--",
            "sh",
            "-c",
            f"find {_sh_single_quote(remote_path)} -maxdepth 1 -name '*.md' | wc -l",
        ]
    )
    print(f"Pushed local {source_path} to {namespace}/{pod}:{remote_path} with {count.strip()} Markdown file(s).")


def _seed_local_vault_for_github_push(source: str, namespace: str) -> None:
    source_path = Path(source)
    _push_local_vault_to_k8s(source, namespace, None, True)
    _remove_k8s_duplicate_root_markdown(namespace, source_path.name)


def _remove_k8s_duplicate_root_markdown(namespace: str, folder_name: str) -> None:
    pod = _running_api_pod(namespace)
    folder = f"/vault/{folder_name}"
    script = (
        f"if [ -d {_sh_single_quote(folder)} ]; then "
        "for file in /vault/*.md; do "
        "[ -e \"$file\" ] || continue; "
        "name=$(basename \"$file\"); "
        "[ \"$name\" = \"README.md\" ] && continue; "
        f"[ -e {_sh_single_quote(folder)}/\"$name\" ] && rm -f \"$file\"; "
        "done; "
        "fi"
    )
    _run(["kubectl", "-n", namespace, "exec", pod, "--", "sh", "-c", script])


def _copy_local_vault_for_push(source: Path, target: Path) -> None:
    for entry in source.iterdir():
        if entry.name == ".cronpot":
            continue
        destination = target / entry.name
        if entry.is_dir():
            shutil.copytree(entry, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(entry, destination)


def _k8s_vault_destination(source: Path, destination: str | None) -> str:
    remote_path = destination or f"/vault/{source.name}"
    if not remote_path.startswith("/vault/") and remote_path != "/vault":
        raise ValueError("Kubernetes vault destination must be /vault or a path below /vault.")
    return remote_path.rstrip("/") or "/vault"


def _copy_synced_vault(source: Path, target: Path) -> int:
    copied = 0
    for entry in source.iterdir():
        if entry.name == ".cronpot":
            continue
        destination = target / entry.name
        if entry.is_dir():
            shutil.copytree(entry, destination, dirs_exist_ok=True)
            copied += sum(1 for path in entry.rglob("*") if path.is_file())
        else:
            shutil.copy2(entry, destination)
            copied += 1
    return copied


def _sync_back_source(staging: Path, target: Path) -> Path:
    nested = staging / target.name
    has_top_level_recipes = any(path.is_file() and path.suffix.casefold() == ".md" for path in staging.iterdir())
    if nested.is_dir() and not has_top_level_recipes:
        print(f"Detected /vault/{target.name}; syncing that folder into {target} instead of creating {target / target.name}.")
        return nested
    return staging


def _run_github_sync_job(direction: str, namespace: str, message: str, timeout_seconds: int, keep_job: bool) -> None:
    job_name = f"cronpot-github-{direction}-{time.strftime('%Y%m%d%H%M%S')}"
    yaml = _github_sync_job_yaml(job_name, namespace, direction, message)
    _run_with_input(["kubectl", "apply", "-f", "-"], yaml)
    try:
        _run(["kubectl", "-n", namespace, "wait", "--for=condition=complete", f"job/{job_name}", f"--timeout={timeout_seconds}s"])
        _run(["kubectl", "-n", namespace, "logs", f"job/{job_name}"])
    except subprocess.CalledProcessError:
        subprocess.run(
            ["kubectl", "-n", namespace, "logs", f"job/{job_name}", "--all-containers=true", "--ignore-errors=true"],
            text=True,
            check=False,
        )
        raise
    finally:
        if not keep_job:
            subprocess.run(["kubectl", "-n", namespace, "delete", "job", job_name, "--ignore-not-found"], text=True, check=False)


def _github_secret_yaml(args: argparse.Namespace, token: str) -> str:
    return f"""apiVersion: v1
kind: Secret
metadata:
  name: cronpot-vault-github
  namespace: {args.namespace}
type: Opaque
stringData:
  repo: {_yaml_single_quote(args.repo)}
  token: {_yaml_single_quote(token)}
  branch: {_yaml_single_quote(args.branch)}
  path: {_yaml_single_quote(args.path)}
  username: {_yaml_single_quote(args.username)}
  author_name: {_yaml_single_quote(args.author_name)}
  author_email: {_yaml_single_quote(args.author_email)}
"""


def _github_sync_job_yaml(job_name: str, namespace: str, direction: str, message: str) -> str:
    script = _github_pull_script() if direction == "pull" else _github_push_script()
    script_block = _indent(script, 16)
    return f"""apiVersion: batch/v1
kind: Job
metadata:
  name: {job_name}
  namespace: {namespace}
  labels:
    app.kubernetes.io/name: cronpot
    app.kubernetes.io/component: github-sync
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 300
  template:
    metadata:
      labels:
        app.kubernetes.io/name: cronpot
        app.kubernetes.io/component: github-sync
    spec:
      restartPolicy: Never
      containers:
        - name: github-sync
          image: alpine/git:latest
          imagePullPolicy: IfNotPresent
          command:
            - /bin/sh
            - -c
            - |
{script_block}
          env:
            - name: GITHUB_REPO
              valueFrom:
                secretKeyRef:
                  name: cronpot-vault-github
                  key: repo
            - name: GITHUB_TOKEN
              valueFrom:
                secretKeyRef:
                  name: cronpot-vault-github
                  key: token
            - name: GITHUB_BRANCH
              valueFrom:
                secretKeyRef:
                  name: cronpot-vault-github
                  key: branch
            - name: GITHUB_PATH
              valueFrom:
                secretKeyRef:
                  name: cronpot-vault-github
                  key: path
            - name: GIT_USERNAME
              valueFrom:
                secretKeyRef:
                  name: cronpot-vault-github
                  key: username
            - name: GIT_AUTHOR_NAME
              valueFrom:
                secretKeyRef:
                  name: cronpot-vault-github
                  key: author_name
            - name: GIT_AUTHOR_EMAIL
              valueFrom:
                secretKeyRef:
                  name: cronpot-vault-github
                  key: author_email
            - name: COMMIT_MESSAGE
              value: {_yaml_single_quote(message)}
          volumeMounts:
            - name: vault
              mountPath: /vault
            - name: work
              mountPath: /work
      volumes:
        - name: vault
          persistentVolumeClaim:
            claimName: cronpot-vault
        - name: work
          emptyDir: {{}}
"""


def _github_pull_script() -> str:
    return """set -eu
cat > /tmp/git-askpass <<'EOF'
#!/bin/sh
case "$1" in
  *Username*) printf '%s\\n' "${GIT_USERNAME:-x-access-token}" ;;
  *) printf '%s\\n' "$GITHUB_TOKEN" ;;
esac
EOF
chmod 700 /tmp/git-askpass
export GIT_ASKPASS=/tmp/git-askpass
export GIT_TERMINAL_PROMPT=0

git clone --depth 1 --branch "$GITHUB_BRANCH" "$GITHUB_REPO" /work/repo
git config --global --add safe.directory /work/repo
repo_path="/work/repo/$GITHUB_PATH"
if [ ! -d "$repo_path" ]; then
  echo "Repository path does not exist: $GITHUB_PATH" >&2
  exit 1
fi

find /vault -mindepth 1 -maxdepth 1 -exec rm -rf {} +
mkdir -p /vault
cp -a "$repo_path"/. /vault/
rm -rf /vault/.git /vault/.cronpot
echo "Pulled GitHub vault into /vault."
"""


def _github_push_script() -> str:
    return """set -eu
cat > /tmp/git-askpass <<'EOF'
#!/bin/sh
case "$1" in
  *Username*) printf '%s\\n' "${GIT_USERNAME:-x-access-token}" ;;
  *) printf '%s\\n' "$GITHUB_TOKEN" ;;
esac
EOF
chmod 700 /tmp/git-askpass
export GIT_ASKPASS=/tmp/git-askpass
export GIT_TERMINAL_PROMPT=0

git clone --depth 1 --branch "$GITHUB_BRANCH" "$GITHUB_REPO" /work/repo
git config --global --add safe.directory /work/repo
repo_path="/work/repo/$GITHUB_PATH"
if [ ! -d "$repo_path" ]; then
  mkdir -p "$repo_path"
fi

cp -a /vault/. "$repo_path"/
rm -rf "$repo_path/.cronpot"
if [ -d "$repo_path/docs" ]; then
  for file in "$repo_path"/*.md; do
    [ -e "$file" ] || continue
    name="$(basename "$file")"
    [ "$name" = "README.md" ] && continue
    if [ -e "$repo_path/docs/$name" ]; then
      rm -f "$file"
    fi
  done
fi

git -C /work/repo config user.name "$GIT_AUTHOR_NAME"
git -C /work/repo config user.email "$GIT_AUTHOR_EMAIL"
if [ -z "$(git -C /work/repo status --short)" ]; then
  echo "No GitHub vault changes to push."
  exit 0
fi

git -C /work/repo add -A
git -C /work/repo commit -m "$COMMIT_MESSAGE"
git -C /work/repo push origin "HEAD:$GITHUB_BRANCH"
echo "Pushed Kubernetes vault to GitHub."
"""


def _indent(value: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(f"{prefix}{line}" if line else prefix for line in value.rstrip().splitlines())


def _yaml_single_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sh_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
