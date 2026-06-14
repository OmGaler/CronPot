# CronPot

CronPot is automation tooling for an Obsidian-style recipe vault. The app repo tracks code, tests, Kubernetes manifests, scripts, and CI/CD. Recipe vault data is intentionally ignored here so it can be managed as a separate vault or repository later.

The current implementation can:

- ingest a recipe URL, preferring JSON-LD Recipe data
- batch import a local Obsidian vault or cloned repository of Markdown recipes
- fall back to simple HTML extraction when structured data is missing
- normalise common US cooking terms to British English
- suggest ingredient normalisation aliases through local Ollama
- optionally rewrite newly ingested web recipes through local Ollama to match the vault style
- infer Obsidian tags and category wikilinks
- enforce exactly one dietary tag (`parev`, `milky`, or `meaty`) unless disabled in config
- write idempotent Markdown recipes using a source hash
- analyse a vault with canonical ingredient grouping for common aliases
- export shopping lists, Markdown bundles, standalone HTML cookbooks, and rendered Markdown PDFs
- run as a containerised HTTP service with a built-in dashboard
- test, build, publish, and deploy through GitHub Actions

The code uses only the Python standard library.

Git is optional. A vault can be a plain folder, an Obsidian vault, or a Git checkout. Use `--commit` only when you want CronPot to attempt a Git commit.

## Documentation

- [CLI reference](CLI.md): commands, flags, examples, exports, workers, and style config.
- [HTTP API reference](API.md): endpoints, request bodies, response shapes, and status codes.
- [Kubernetes guide](k8s/README.md): local cluster flow, overlays, worker Deployment, analytics CronJob, and pedagogy map.

## Schema

Generated recipes use the current vault schema:

```markdown
---
source: ""
tags:
  - parev
prep_time: ""
cook_time: ""
servings: ""
---

[[Mains]]

## Ingredients

## Method
```

Use either `servings` or `yield`; generated Markdown writes `servings` when available and falls back to `yield`.

## Commands

Install the CLI locally when working from a checkout:

```powershell
pip install -e .
```

Then run `cronpot` commands from anywhere. Replace `docs` with any vault folder path.

```powershell
cronpot ingest "https://example.com/recipe" --vault docs
cronpot analytics --vault docs
cronpot jobs ingest "https://example.com/recipe" --vault docs
cronpot worker --vault docs --once --workers 2
cronpot export "Aglio e Olio" "Roast Chicken" --vault docs --format shopping-list
cronpot start --vault docs --host 127.0.0.1 --port 8080
```

See [CLI.md](CLI.md) for the full command reference, flags, examples, and job worker workflow.

## HTTP API

Run locally:

```powershell
cronpot start --vault docs --host 127.0.0.1 --port 8080
```

`cronpot start` prints the URL it is serving before it starts the blocking server process. Open the dashboard at `http://127.0.0.1:8080/` or `http://127.0.0.1:8080/dashboard`.

Or use Kubernetes port-forwarding:

```cmd
scripts\k8s-port-forward.cmd cronpot-local
```

See [API.md](API.md) for every endpoint, query parameter, request body, response shape, and example `Invoke-RestMethod` call.

Queued jobs are stored as JSON under `.cronpot/jobs` inside the vault. This gives CronPot durable background processing without requiring Postgres or Redis yet.

## Config

Copy `cronpot.example.toml` to `cronpot.toml` when you want project-specific settings. `cronpot.toml` is ignored because local config can contain machine-specific paths later.

```toml
[recipe]
default_vault = "docs"
require_dietary_tag = true

[schema]
ingredient_heading = "Ingredients"
method_heading = "Method"
frontmatter_fields = ["tags", "source", "source_hash", "prep_time", "cook_time", "total_time", "servings", "yield"]

[style]
english = "british"
# Options: unicode, ascii, decimal
fraction_style = "unicode"
method_style = "imperative"

[worker]
count = 2
max_attempts = 3
stale_after_seconds = 900

[llm]
provider = "ollama"
base_url = "http://127.0.0.1:11434"
model = "gemma4:latest"
auto_normalise_ingredients = false
rewrite_ingested_recipes = false
ingredient_limit = 120
```

Set `require_dietary_tag = false` only if you intentionally want to allow recipes without exactly one of `parev`, `milky`, or `meaty`.

`[schema]` controls the Markdown shape CronPot writes and reads. The default keeps `## Ingredients` and `## Method`, but vaults can rename those headings and choose which frontmatter fields are emitted.

`[style]` controls deterministic text conventions and LLM rewrite instructions. `fraction_style` can be `unicode`, `ascii`, or `decimal`. The default is Unicode, so `1/2 tsp` becomes `½ tsp` and `1 1/4 cups` becomes `1¼ cups`. Decimal mode writes those examples as `0.5 tsp` and `1.25 cups`.

`[worker]` controls default parallelism and retry behaviour for background ingest workers.

For local LLM suggestions, install Ollama, start it, and pull the configured model:

```powershell
ollama serve
ollama pull gemma4:latest
cronpot normalise ingredients --vault docs --suggest
```

The suggestion command prints proposed analytics aliases only. It does not rewrite recipe Markdown or config.

Set `auto_normalise_ingredients = true` to let `cronpot analytics` and the dashboard use local Ollama suggestions for ingredient grouping. Dashboard aliases are cached in memory to avoid calling Ollama on every refresh.

Set `rewrite_ingested_recipes = true` to let `cronpot ingest` and the HTTP `/ingest` endpoint ask the configured local LLM to rewrite extracted web recipes to match existing vault examples. CronPot still performs deterministic extraction and normalisation first, and it fails the ingest if the configured LLM cannot return valid recipe JSON.

PDF export prints the rendered HTML cookbook through Microsoft Edge or Chrome, so it follows the HTML export styling and requires one of those browsers locally.

## Kubernetes

The project includes a non-root `Dockerfile` and Kustomize manifests under `k8s`.

For the local Kubernetes path, use the unified start helper:

```cmd
scripts\k8s-start.cmd docs
```

Or, in PowerShell:

```powershell
.\scripts\k8s-start.ps1 -Source docs
```

That renders and applies the local overlay, waits for the API Deployment, seeds the local PVC from `docs`, prints the dashboard URL, then starts port-forwarding. The command blocks while port-forwarding is active; press `Ctrl+C` to stop it. Use `-` instead of a vault path to start without seeding:

```cmd
scripts\k8s-start.cmd -
```

```powershell
.\scripts\k8s-render.ps1 -Overlay local
.\scripts\k8s-deploy.ps1 -Overlay local
.\scripts\k8s-port-forward.ps1 -Namespace cronpot-local
```

If Windows blocks PowerShell scripts, use the matching `.cmd` helpers:

```cmd
scripts\k8s-render.cmd local
scripts\k8s-deploy.cmd local
scripts\k8s-port-forward.cmd cronpot-local
```

The overlays are:

| Overlay | Namespace | Practical use | Code source | Vault storage |
| --- | --- | --- | --- | --- |
| `local` | `cronpot-local` | Fast laptop feedback loop while editing CronPot | `python:3.12-slim` plus ConfigMap-mounted source files | 1Gi PVC seeded from a local folder |
| `dev` | `cronpot-dev` | First shared cluster deployment for integration checks | GHCR image tagged for dev | 1Gi PVC |
| `staging` | `cronpot-staging` | Production-shaped rehearsal before release | GHCR image tagged for staging | 2Gi PVC |
| `production` | `cronpot` | Real service namespace | GHCR release/latest image | 5Gi PVC |

Only `local` is used by `scripts\k8s-start.cmd docs`. The other overlays are for cluster promotion through CI/CD or explicit deploy commands.

The Kubernetes layer demonstrates a namespace, service account, config map, persistent volume claim, API deployment, worker deployment, service, probes, analytics cron job, network policy, and environment overlays. The API Deployment, worker Deployment, and analytics CronJob all mount `/vault` and `/config/cronpot.toml`, so dashboard analytics, background URL ingestion, and scheduled analytics use the same recipe data and ingredient normalisation settings. See `k8s/README.md` for the full flow and the teaching map for each Kubernetes resource.

Seed the local Kubernetes PVC from a local vault:

```cmd
scripts\k8s-seed-vault.cmd docs cronpot-local
```

To reset the local PVC before copying the vault:

```cmd
scripts\k8s-seed-vault.cmd docs cronpot-local /clear
```

Current local prerequisites: Docker Desktop must be running, and a Kubernetes context must be configured. `kubectl` is available on this machine; Helm is not required.

Run a live local Kubernetes smoke check with:

```cmd
scripts\k8s-smoke.cmd docs
```

## CI/CD

`.github/workflows/ci-cd.yml` compiles Python modules, runs unit tests, renders all Kubernetes overlays, builds a Docker image, publishes it to GHCR on non-PR runs, and deploys when the matching kubeconfig secret is configured.

Secrets:

- `KUBE_CONFIG_DEV` deploys `dev`
- `KUBE_CONFIG_STAGING` deploys `staging`
- `KUBE_CONFIG_PRODUCTION` deploys `production`

Pushes to `master` deploy `dev` automatically when `KUBE_CONFIG_DEV` exists. `staging` and `production` deploy through the manual GitHub Actions workflow dispatch.

## Tests

```powershell
python -m unittest discover -s tests
```

## Remaining Decisions


- Whether to migrate existing recipes to the current schema in one batch.
- Whether to add Helm once the raw Kubernetes manifests stabilise.
