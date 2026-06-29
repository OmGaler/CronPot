<p align="center">
  <img src="assets/cronpot-logo.svg" alt="CronPot logo" width="96">
</p>

# CronPot CLI

The installed command is `cronpot`. From a checkout, install it with:

```powershell
pip install -e .
```

Every command that reads config accepts `--config PATH`. If omitted, CronPot reads `cronpot.toml` from the current directory when it exists, otherwise it uses defaults. Most vault-aware commands accept `--vault PATH`; if omitted, CronPot uses `[recipe].default_vault` from config, defaulting to `docs`.

## Common Workflows

Import one recipe URL:

```powershell
cronpot ingest "https://example.com/recipe" --vault docs
```

Queue URL ingestion and process jobs:

```powershell
cronpot jobs ingest "https://example.com/recipe" --vault docs
cronpot worker --vault docs --once --workers 2
cronpot jobs list --vault docs
```

Run analytics with configured ingredient normalisation:

```powershell
cronpot analytics --vault docs --top 20
cronpot analytics --vault docs --json --top 20
```

Export two recipes as a shopping list:

```powershell
cronpot export "Aglio e Olio" "Roast Chicken" --vault docs --format shopping-list
```

Start the local HTTP service:

```powershell
cronpot start --vault docs --host 127.0.0.1 --port 8080
```

Start the mobile LAN UI with a six digit pairing code:

```powershell
cronpot start --lan --vault docs
```

## `cronpot ingest`

Extract a recipe URL, normalise it, optionally rewrite it with the configured LLM, and write Markdown into the vault.

```powershell
cronpot ingest URL [--vault VAULT] [--html-file FILE] [--allow-incomplete] [--dry-run] [--no-overwrite] [--commit] [--title TITLE] [--config CONFIG]
```

Useful examples:

```powershell
cronpot ingest "https://example.com/recipe" --vault docs
cronpot ingest "https://example.com/recipe" --html-file saved-page.html --dry-run
cronpot ingest "https://example.com/recipe" --vault docs --title "Friday Night Soup"
cronpot ingest "https://example.com/recipe" --vault docs --no-overwrite
cronpot ingest "https://example.com/recipe" --vault docs --commit
```

When run interactively, CronPot suggests the extracted recipe name before writing. Press Enter to accept it, type a replacement, or pass `--title` for non-interactive runs.

`--dry-run` prints generated Markdown and does not write. `--allow-incomplete` writes a draft even when title, ingredients, or method steps are missing. `--commit` attempts a Git commit after writing; if the current directory is not a Git repository, the Markdown is still written and the commit is skipped.

## `cronpot import-vault`

Batch import Markdown recipes from another file, folder, Obsidian vault, or cloned repository.

```powershell
cronpot import-vault SOURCE [--vault VAULT] [--no-recursive] [--allow-incomplete] [--dry-run] [--no-overwrite] [--commit] [--config CONFIG]
```

Examples:

```powershell
cronpot import-vault "C:\path\to\ObsidianVault" --vault docs
cronpot import-vault "C:\path\to\Recipe.md" --vault docs --dry-run
cronpot import-vault "C:\path\to\ObsidianVault" --vault docs --no-overwrite --commit
```

## `cronpot validate`

Check recipe files for missing tags, missing category wikilinks, missing ingredients, missing method steps, and duplicate sources.

```powershell
cronpot validate [--vault VAULT] [--config CONFIG]
```

Exit code is `0` when no issues are found and `1` when validation issues are printed.

## `cronpot analytics`

Summarise recipes, missing source links, tags, categories, and ingredient counts.

```powershell
cronpot analytics [--vault VAULT] [--top N] [--json] [--config CONFIG]
```

Examples:

```powershell
cronpot analytics --vault docs --top 20
cronpot analytics --vault docs --json --top 20
```

If `[llm].auto_normalise_ingredients = true`, analytics asks the configured local LLM for ingredient aliases and applies them to the reported counts. If the LLM call fails, CronPot prints a warning and falls back to deterministic aliases.

## `cronpot normalise ingredients`

Ask the configured LLM for ingredient alias suggestions.

```powershell
cronpot normalise ingredients --suggest [--vault VAULT] [--limit N] [--model MODEL] [--base-url URL] [--config CONFIG]
```

Example:

```powershell
cronpot normalise ingredients --vault docs --suggest --limit 100 --model qwen2.5:3b
```

This command prints suggestions only. It does not rewrite Markdown or config.

## `cronpot jobs`

Queue and inspect background URL ingestion jobs. Job JSON is stored under `.cronpot/jobs` inside the vault.

Queue a job:

```powershell
cronpot jobs ingest "https://example.com/recipe" --vault docs
```

List jobs:

```powershell
cronpot jobs list --vault docs
```

Run pending jobs once:

```powershell
cronpot jobs run --vault docs --workers 2 --limit 10
```

Retry a failed or stale job:

```powershell
cronpot jobs retry JOB_ID --vault docs
```

Clear stored jobs:

```powershell
cronpot jobs clear --vault docs
```

Statuses are `pending`, `running`, `complete`, and `failed`. Failed jobs can be retried until they hit `[worker].max_attempts`. Clearing jobs deletes only stored job records under `.cronpot/jobs`; it does not delete recipes.

## `cronpot worker`

Process queued jobs as a worker process.

```powershell
cronpot worker [--vault VAULT] [--workers N] [--limit N] [--once] [--config CONFIG]
```

Examples:

```powershell
cronpot worker --vault docs --once --workers 2
cronpot worker --vault docs --workers 2
```

`--once` processes the current queue and exits. Without `--once`, the worker keeps polling every five seconds. `--limit` exits after processing up to that many jobs.

## `cronpot export`

Export recipes as HTML, Markdown, PDF, or a shopping list.

```powershell
cronpot export [RECIPES ...] [--vault VAULT] [--all] [--format html|markdown|pdf|shopping-list] [--title TITLE] [--output FILE] [--config CONFIG]
```

Examples:

```powershell
cronpot export "Aglio e Olio" --vault docs
cronpot export --all --vault docs --output cookbook.html
cronpot export --all --vault docs --format pdf --output cookbook.pdf
cronpot export "Aglio e Olio" --vault docs --format pdf
cronpot export "Aglio e Olio" --vault docs --format markdown --output recipe-bundle.md
cronpot export "Aglio e Olio" "Roast Chicken" --vault docs --format shopping-list
```

For PDF export, CronPot renders the same HTML cookbook styling through Microsoft Edge or Chrome. If `--output` is omitted for a PDF, CronPot chooses a filename from the selected recipe title or `cookbook.pdf` for multiple recipes.

## Compatibility Commands

These commands are kept as shortcuts around `export`:

```powershell
cronpot shopping-list "Aglio e Olio" "Roast Chicken" --vault docs
cronpot bundle "Aglio e Olio" --vault docs --output recipe-bundle.md
cronpot html --all --vault docs --output cookbook.html
```

## `cronpot serve` / `cronpot start`

Run the HTTP service and dashboard. `start` is an alias for `serve`.

```powershell
cronpot start [--vault VAULT] [--host HOST] [--port PORT] [--lan] [--auth-code CODE] [--config CONFIG]
cronpot serve [--vault VAULT] [--host HOST] [--port PORT] [--lan] [--auth-code CODE] [--config CONFIG]
```

Examples:

```powershell
cronpot start --vault docs --host 127.0.0.1 --port 8080
cronpot start --lan --vault docs
```

The command prints the serving URL, then blocks while the server is running.

`--lan` is for same-network phone access. It binds to the configured host, prints a six digit pairing code, and prints detected mobile URLs such as `http://192.168.1.42:8080/mobile`. Open that URL on your phone and enter the code. After pairing, the mobile page can queue, run, retry, and clear ingest jobs; pull and push the Kubernetes-backed GitHub vault; search recipes; build shopping lists; and copy the list.

`--auth-code CODE` sets a fixed six digit code instead of generating one. It is mainly for repeatable local testing.

`CRONPOT_AUTH_CODE` can also provide the code through the environment. The local Kubernetes overlay uses this via a Secret so the command line does not contain the code.

## Style Config

The relevant style options are:

```toml
[style]
english = "british"
fraction_style = "unicode"
method_style = "imperative"
```

`fraction_style` accepts:

- `unicode`: converts `1/2 tsp` to `½ tsp` and `1 1/4 cups` to `1¼ cups`.
- `ascii`: preserves ASCII fractions from the source.
- `decimal`: converts ASCII and Unicode fractions to decimals, for example `1/2 tsp` to `0.5 tsp`, `½ tsp` to `0.5 tsp`, and `1¼ cups` to `1.25 cups`.

## `cronpot k8s`

Kubernetes helper commands are available under `cronpot k8s`. The shorter alias `cronpot k` works too.

Show Kubernetes status:

```powershell
cronpot k8s status --namespace cronpot-local
```

`status` checks `kubectl`, cluster reachability, namespace presence, API pod readiness, worker readiness, vault recipe count, and stored job count where those checks are available.

Copy the Kubernetes PVC vault back into a local folder:

```powershell
cronpot k8s sync-back docs --namespace cronpot-local
```

Add `--commit` to commit the synced folder when it is inside the current Git repository.

Copy a local vault folder into the Kubernetes PVC:

```powershell
cronpot k8s push-local docs --namespace cronpot-local
```

This copies `docs` into `/vault/docs` by default. Pass `--destination /vault/other-folder` to choose another PVC path, or `--clear` to replace the destination before copying.

Configure a GitHub-backed vault Secret:

```powershell
$env:CRONPOT_GITHUB_TOKEN = "github_pat_..."
cronpot k8s github secret --namespace cronpot-local --repo "https://github.com/YOU/YOUR-VAULT.git" --branch main --path "." --author-name "cronpot-bot" --author-email "cronpot-bot@example.local"
```

Pull GitHub into the Kubernetes PVC:

```powershell
cronpot k8s github pull --namespace cronpot-local
```

That updates the Kubernetes PVC at `/vault`. To also update your local `docs` folder in the same command:

```powershell
cronpot k8s github pull --namespace cronpot-local --sync-back docs
```

Push the Kubernetes PVC back to GitHub:

```powershell
cronpot k8s push-local docs --namespace cronpot-local
cronpot k8s github push --namespace cronpot-local --message "Sync CronPot vault from Kubernetes"
cronpot k8s github push --namespace cronpot-local --seed-from docs
cronpot k8s github push --namespace cronpot-local --no-seed
```

`github push` seeds from local `docs` into `/vault/docs` by default, removes duplicate top-level Markdown files left by older bad pushes, then commits and pushes the PVC state. Use `--seed-from other-vault` for a different local folder. Use `--no-seed` only when you deliberately want to push the current Kubernetes PVC exactly as it is.
