from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from cronpot.config import AutomationConfig
from cronpot.normalisation import DIETARY_TAGS
from cronpot.models import Recipe


@dataclass(slots=True)
class ValidationIssue:
    path: Path
    message: str


@dataclass(slots=True)
class GitCommitResult:
    committed: bool
    skipped_reason: str = ""
    output: str = ""


def source_hash(source: str) -> str:
    return hashlib.sha256(source.strip().encode("utf-8")).hexdigest()[:16] if source.strip() else ""


def render_markdown(recipe: Recipe) -> str:
    recipe_hash = recipe.source_hash or source_hash(recipe.source)
    lines = [
        "---",
        "tags:",
    ]
    for tag in recipe.tags:
        lines.append(f"  - {tag}")
    if recipe.source:
        lines.append(f"source: {_yaml_string(recipe.source)}")
    if recipe_hash:
        lines.append(f"source_hash: {_yaml_string(recipe_hash)}")
    if recipe.prep_time:
        lines.append(f"prep_time: {_yaml_string(recipe.prep_time)}")
    if recipe.cook_time:
        lines.append(f"cook_time: {_yaml_string(recipe.cook_time)}")
    if recipe.total_time:
        lines.append(f"total_time: {_yaml_string(recipe.total_time)}")
    if recipe.servings:
        lines.append(f"servings: {_yaml_string(recipe.servings)}")
    elif recipe.yield_amount:
        lines.append(f"yield: {_yaml_string(recipe.yield_amount)}")
    lines.append("---")
    lines.append("")

    for category in recipe.categories:
        lines.append(f"[[{category.title()}]]")
    if recipe.categories:
        lines.append("")

    lines.append("## Ingredients")
    for ingredient in recipe.ingredients:
        lines.append(f"- {ingredient}")
    lines.append("")
    lines.append("## Method")
    for index, step in enumerate(recipe.steps, start=1):
        lines.append(f"{index}. {step}")
    lines.append("")
    return "\n".join(lines)


def parse_markdown_recipe(path: Path, text: str | None = None) -> Recipe:
    raw = path.read_text(encoding="utf-8") if text is None else text
    metadata = _parse_front_matter(raw)
    title = metadata.get("title") or path.stem
    tags = metadata.get("tags", [])
    if isinstance(tags, str):
        tags = [tags] if tags else []

    return Recipe(
        title=str(title),
        ingredients=_extract_ingredients(raw),
        steps=_extract_steps(raw),
        prep_time=str(metadata.get("prep_time", "")),
        cook_time=str(metadata.get("cook_time", "")),
        total_time=str(metadata.get("total_time", "")),
        servings=str(metadata.get("servings", "")),
        yield_amount=str(metadata.get("yield", "")),
        source=str(metadata.get("source", "")),
        tags=list(tags),
        categories=_extract_categories(raw),
        source_hash=str(metadata.get("source_hash", "")),
    )


def load_recipes(vault_path: Path | str) -> list[tuple[Path, Recipe]]:
    vault = Path(vault_path)
    recipes: list[tuple[Path, Recipe]] = []
    for path in sorted(vault.glob("*.md")):
        recipe = parse_markdown_recipe(path)
        if recipe.ingredients or recipe.steps:
            recipes.append((path, recipe))
    return recipes


def write_recipe_to_vault(recipe: Recipe, vault_path: Path | str, overwrite: bool = True) -> Path:
    vault = Path(vault_path)
    vault.mkdir(parents=True, exist_ok=True)
    recipe_hash = recipe.source_hash or source_hash(recipe.source)
    target = find_existing_by_source(vault, recipe.source, recipe_hash)

    if target is None:
        target = _available_recipe_path(vault, recipe.title, recipe_hash)
    elif not overwrite:
        raise FileExistsError(f"Recipe already exists for this source: {target}")

    recipe.source_hash = recipe_hash
    target.write_text(render_markdown(recipe), encoding="utf-8", newline="\n")
    return target


def find_existing_by_source(vault: Path, source: str, recipe_hash: str) -> Path | None:
    if not source and not recipe_hash:
        return None
    for path, recipe in load_recipes(vault):
        if recipe_hash and recipe.source_hash == recipe_hash:
            return path
        if source and recipe.source == source:
            return path
    return None


def find_recipe_file(vault_path: Path | str, name_or_path: str) -> Path:
    candidate = Path(name_or_path)
    if candidate.exists():
        return candidate

    vault = Path(vault_path)
    if not candidate.suffix:
        candidate = candidate.with_suffix(".md")

    direct = vault / candidate
    if direct.exists():
        return direct

    requested = Path(name_or_path).stem.casefold()
    for path in vault.glob("*.md"):
        if path.stem.casefold() == requested:
            return path
    raise FileNotFoundError(f"No recipe found for {name_or_path!r}")


def validate_vault(vault_path: Path | str, config: AutomationConfig | None = None) -> list[ValidationIssue]:
    config = config or AutomationConfig()
    issues: list[ValidationIssue] = []
    seen_sources: dict[str, Path] = {}

    for path, recipe in load_recipes(vault_path):
        if not recipe.tags:
            issues.append(ValidationIssue(path, "missing tags"))
        elif config.require_dietary_tag:
            dietary_tags = [tag for tag in recipe.tags if tag in DIETARY_TAGS]
            if len(dietary_tags) != 1:
                issues.append(ValidationIssue(path, "must include exactly one of parev, milky, meaty"))
        if not recipe.categories:
            issues.append(ValidationIssue(path, "missing category wikilink"))
        if not recipe.ingredients:
            issues.append(ValidationIssue(path, "missing ingredients"))
        if not recipe.steps:
            issues.append(ValidationIssue(path, "missing method steps"))
        if recipe.source:
            if recipe.source in seen_sources:
                issues.append(ValidationIssue(path, f"duplicate source also used by {seen_sources[recipe.source].name}"))
            else:
                seen_sources[recipe.source] = path
    return issues


def commit_paths(repo_path: Path | str, paths: list[Path], message: str) -> GitCommitResult:
    repo = Path(repo_path)
    if not (repo / ".git").exists():
        return GitCommitResult(committed=False, skipped_reason="not a Git repository")

    relative_paths = [str(path.resolve().relative_to(repo.resolve())) for path in paths]
    add = subprocess.run(["git", "add", *relative_paths], cwd=repo, text=True, capture_output=True, check=False)
    if add.returncode != 0:
        return GitCommitResult(committed=False, skipped_reason=add.stderr.strip() or add.stdout.strip())

    commit = subprocess.run(["git", "commit", "-m", message], cwd=repo, text=True, capture_output=True, check=False)
    output = "\n".join(part for part in [commit.stdout.strip(), commit.stderr.strip()] if part)
    if commit.returncode != 0:
        return GitCommitResult(committed=False, skipped_reason=output or "git commit failed")
    return GitCommitResult(committed=True, output=output)


def _parse_front_matter(text: str) -> dict[str, object]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}

    metadata: dict[str, object] = {}
    current_list_key = ""
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if current_list_key and line.strip().startswith("- "):
            value = line.strip()[2:].strip()
            existing = metadata.setdefault(current_list_key, [])
            if isinstance(existing, list):
                existing.append(value)
            continue
        current_list_key = ""
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lstrip("*")
        value = value.strip()
        if not value:
            metadata[key] = []
            current_list_key = key
        else:
            metadata[key] = _parse_scalar(value)
    return metadata


def _parse_scalar(value: str) -> str:
    if value.startswith("[") and value.endswith("]"):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return value
        if isinstance(parsed, list):
            return [str(item) for item in parsed]  # type: ignore[return-value]
    if value[:1] in {'"', "'"}:
        try:
            return str(json.loads(value))
        except json.JSONDecodeError:
            return value.strip("\"'")
    return value


def _extract_categories(text: str) -> list[str]:
    categories: list[str] = []
    for match in re.finditer(r"^\s*\[\[([^\]|]+)(?:\|[^\]]+)?\]\]\s*$", text, flags=re.MULTILINE):
        category = match.group(1).strip()
        if category and category.casefold() not in {"readme", "index"}:
            categories.append(category)
    return _unique(categories)


def _extract_ingredients(text: str) -> list[str]:
    section = _extract_section(text, "Ingredients")
    ingredients: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            ingredients.append(stripped[2:].strip())
    return ingredients


def _extract_steps(text: str) -> list[str]:
    section = _extract_section(text, "Method")
    steps: list[str] = []
    for line in section.splitlines():
        match = re.match(r"\s*\d+\.\s+(.*)", line)
        if match:
            steps.append(match.group(1).strip())
    return steps


def _extract_section(text: str, heading: str) -> str:
    pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$", flags=re.IGNORECASE | re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return ""
    next_heading = re.search(r"^##\s+", text[match.end() :], flags=re.MULTILINE)
    if next_heading:
        return text[match.end() : match.end() + next_heading.start()]
    return text[match.end() :]


def _available_recipe_path(vault: Path, title: str, recipe_hash: str) -> Path:
    base = _safe_filename(title or "Untitled Recipe")
    candidate = vault / f"{base}.md"
    if not candidate.exists():
        return candidate

    suffix = recipe_hash[:8] if recipe_hash else "copy"
    candidate = vault / f"{base} - {suffix}.md"
    counter = 2
    while candidate.exists():
        candidate = vault / f"{base} - {suffix}-{counter}.md"
        counter += 1
    return candidate


def _safe_filename(value: str) -> str:
    clean = re.sub(r'[<>:"/\\|?*]+', "", value)
    clean = re.sub(r"\s+", " ", clean).strip(" .")
    return clean[:120] or "Untitled Recipe"


def _yaml_string(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        key = value.casefold()
        if key and key not in seen:
            unique.append(value)
            seen.add(key)
    return unique
