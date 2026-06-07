# CronPot

CronPot is automation tooling for an Obsidian-style recipe vault. The app repo tracks code, tests, Kubernetes manifests, scripts, and CI/CD. Recipe vault data is intentionally ignored here so it can be managed as a separate vault or repository later.

The current implementation can:

- ingest a recipe URL, preferring JSON-LD Recipe data
- batch import a local Obsidian vault or cloned repository of Markdown recipes
- fall back to simple HTML extraction when structured data is missing
- normalise common US cooking terms to British English
- infer Obsidian tags and category wikilinks
- enforce exactly one dietary tag (`parev`, `milky`, or `meaty`) unless disabled in config
- write idempotent Markdown recipes using a source hash
- analyse a vault
- export shopping lists, Markdown bundles, and standalone HTML cookbooks
- run as a containerised HTTP service for Kubernetes deployment
- test, build, publish, and deploy through GitHub Actions

The code uses only the Python standard library.

Git is optional. A vault can be a plain folder, an Obsidian vault, or a Git checkout. Use `--commit` only when you want CronPot to attempt a Git commit.

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
cronpot ingest "https://example.com/recipe" --html-file saved-page.html --dry-run
cronpot import-vault "C:\path\to\ObsidianVault" --vault docs
cronpot analytics --vault docs
cronpot export "Aglio e Olio" --vault docs
cronpot export --all --vault docs --output cookbook.html
cronpot export "Aglio e Olio" --vault docs --format markdown --output recipe-bundle.md
cronpot export "Aglio e Olio" "Roast Chicken" --vault docs --format shopping-list
cronpot validate --vault docs
cronpot start --vault docs --host 127.0.0.1 --port 8080
```

Use `--commit` with `ingest` or `import-vault` to request a Git commit. If the current folder is not a Git repository, the commit is skipped and the Markdown files are still written.

## HTTP API

Run locally:

```powershell
cronpot start --vault docs --host 127.0.0.1 --port 8080
```

Or use Kubernetes port-forwarding:

```cmd
scripts\k8s-port-forward.cmd cronpot-local
```

Useful read endpoints:

```powershell
Invoke-RestMethod http://127.0.0.1:8080/healthz
Invoke-RestMethod http://127.0.0.1:8080/analytics
Invoke-RestMethod http://127.0.0.1:8080/recipes
Invoke-RestMethod "http://127.0.0.1:8080/recipes?tag=meaty&category=Mains"
Invoke-RestMethod "http://127.0.0.1:8080/recipes/Aglio%20e%20Olio"
Invoke-RestMethod "http://127.0.0.1:8080/shopping-list?recipe=Aglio%20e%20Olio&recipe=Roast%20Chicken"
Invoke-RestMethod "http://127.0.0.1:8080/shopping-list?all=true"
```

Write endpoint:

```powershell
Invoke-RestMethod http://127.0.0.1:8080/ingest -Method Post -ContentType "application/json" -Body '{"url":"https://example.com/recipe"}'
```

## Config

Copy `cronpot.example.toml` to `cronpot.toml` when you want project-specific settings. `cronpot.toml` is ignored because local config can contain machine-specific paths later.

```toml
[recipe]
default_vault = "docs"
require_dietary_tag = true
```

Set `require_dietary_tag = false` only if you intentionally want to allow recipes without exactly one of `parev`, `milky`, or `meaty`.

## Kubernetes

The project includes a non-root `Dockerfile` and Kustomize manifests under `k8s`.

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

- `local`: Docker Desktop development, using `python:3.12-slim` and ConfigMap-mounted source
- `dev`: deployable cluster environment with namespace `cronpot-dev`
- `staging`: deployable cluster environment with namespace `cronpot-staging`
- `production`: deployable cluster environment with namespace `cronpot`

The Kubernetes layer demonstrates a namespace, service account, config map, persistent volume claim, API deployment, service, probes, analytics cron job, network policy, and environment overlays. See `k8s/README.md` for the full flow.

Seed the local Kubernetes PVC from a local vault:

```cmd
scripts\k8s-seed-vault.cmd docs cronpot-local
```

Current local prerequisites: Docker Desktop must be running, and a Kubernetes context must be configured. `kubectl` is available on this machine; Helm is not required.

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

- Which LLM provider should power optional messy-page extraction and advanced normalisation, if any.
- Whether to migrate existing recipes to the current schema in one batch.
- Whether to add Helm once the raw Kubernetes manifests stabilise.
