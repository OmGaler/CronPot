from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from cronpot.config import AutomationConfig
from cronpot.models import Recipe
from cronpot.normalisation import normalise_recipe
from cronpot.vault import parse_markdown_recipe, render_markdown, write_recipe_to_vault


@dataclass(slots=True)
class ImportSkip:
    path: Path
    reason: str


@dataclass(slots=True)
class ImportPreview:
    source: Path
    markdown: str


@dataclass(slots=True)
class ImportResult:
    imported: list[Path] = field(default_factory=list)
    skipped: list[ImportSkip] = field(default_factory=list)
    previews: list[ImportPreview] = field(default_factory=list)


def import_markdown_vault(
    source_path: Path | str,
    target_vault: Path | str,
    config: AutomationConfig | None = None,
    recursive: bool = True,
    allow_incomplete: bool = False,
    overwrite: bool = True,
    dry_run: bool = False,
) -> ImportResult:
    config = config or AutomationConfig()
    source = Path(source_path)
    target = Path(target_vault)
    result = ImportResult()

    for markdown_path in _markdown_paths(source, recursive=recursive):
        recipe = parse_markdown_recipe(markdown_path, config=config)
        if not allow_incomplete and not recipe.has_core_content():
            result.skipped.append(ImportSkip(markdown_path, "missing ingredients or method steps"))
            continue

        imported = _prepare_imported_recipe(recipe, source, markdown_path, config)
        if dry_run:
            result.previews.append(ImportPreview(markdown_path, render_markdown(imported, config)))
            continue

        try:
            result.imported.append(write_recipe_to_vault(imported, target, overwrite=overwrite, config=config))
        except FileExistsError as exc:
            result.skipped.append(ImportSkip(markdown_path, str(exc)))

    return result


def _markdown_paths(source: Path, recursive: bool) -> list[Path]:
    if source.is_file():
        return [source] if source.suffix.casefold() == ".md" else []
    pattern = "**/*.md" if recursive else "*.md"
    return sorted(path for path in source.glob(pattern) if path.is_file())


def _prepare_imported_recipe(
    recipe: Recipe,
    source_root: Path,
    markdown_path: Path,
    config: AutomationConfig,
) -> Recipe:
    normalised = normalise_recipe(recipe, config)
    if normalised.source_hash:
        return normalised

    normalised.source_hash = _import_hash(source_root, markdown_path)
    return normalised


def _import_hash(source_root: Path, markdown_path: Path) -> str:
    try:
        relative = markdown_path.resolve().relative_to(source_root.resolve())
    except ValueError:
        relative = markdown_path.resolve()
    payload = f"{relative.as_posix()}\n{markdown_path.read_text(encoding='utf-8', errors='replace')}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
