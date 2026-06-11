# Kubernetes Setup

CronPot uses Kustomize because `kubectl` includes it and Helm is not required. The manifests are intentionally plain Kubernetes so each part is visible while the project is still small.

## Layout

- `base`: shared production-shaped resources
- `overlays/local`: local Docker Desktop development using `python:3.12-slim` with source mounted from a ConfigMap
- `overlays/dev`: deployable development environment
- `overlays/staging`: deployable staging environment
- `overlays/production`: deployable production environment

The base includes:

- Namespace
- ServiceAccount
- ConfigMap
- PersistentVolumeClaim for the vault
- API Deployment
- ClusterIP Service
- analytics CronJob
- NetworkPolicy

## Pedagogy Map

| Kubernetes piece | CronPot role | What it teaches |
| --- | --- | --- |
| Namespace | Separates `local`, `dev`, `staging`, and `production` installs | Environment boundaries and safe namespacing |
| ServiceAccount | Runs the API and analytics worker as a named workload identity | Least-privilege workload identity, even before RBAC grows |
| ConfigMap | Mounts `cronpot.toml` into `/config` | Runtime configuration separate from the container image |
| PersistentVolumeClaim | Stores the recipe vault at `/vault` | Stateful application data and why storage affects scaling |
| Deployment | Runs the HTTP API and dashboard | Long-running workloads, probes, resources, and security context |
| Service | Gives the API a stable in-cluster address | Service discovery independent of individual pods |
| CronJob | Runs scheduled analytics with the same vault and config | Batch workloads sharing state and configuration with the API |
| NetworkPolicy | Allows HTTP ingress to the API pods | Pod traffic boundaries and how policy becomes explicit |
| Overlays | Adjust namespaces, images, PVC size, and local source mounting | Promotion between environments without duplicating the base |

## Environments

| Overlay | Namespace | Image source | Vault PVC |
| --- | --- | --- | --- |
| `local` | `cronpot-local` | `python:3.12-slim` plus mounted source | 1Gi |
| `dev` | `cronpot-dev` | GHCR image | 1Gi |
| `staging` | `cronpot-staging` | GHCR image | 2Gi |
| `production` | `cronpot` | GHCR image | 5Gi |

Practical differences:

| Overlay | Use it when | What changes compared with the previous step |
| --- | --- | --- |
| `local` | You are editing code on your laptop and want the fastest Kubernetes feedback loop | Runs from mounted source files, uses Docker Desktop-friendly defaults, and is started by `scripts\k8s-start.cmd docs` |
| `dev` | You want the first shared cluster deployment after code has been built into an image | Uses the packaged GHCR image instead of mounted source, so it tests the container artefact |
| `staging` | You want a production-like rehearsal before a release | Keeps the same workload shape but uses a separate namespace and a larger vault PVC |
| `production` | You are deploying the real service | Uses the stable production namespace and the largest PVC in the current manifests |

Each environment currently runs one API replica. The vault is a writable PVC, so multi-replica scaling should wait until we introduce RWX storage or move mutable state into a database/object store. That limitation is useful here: it keeps the stateful part honest instead of pretending a file-backed app can be horizontally scaled without a storage decision.

## Local Flow

The local overlay runs from `python:3.12-slim` and mounts the local source files through a ConfigMap. This avoids the Docker Desktop multi-node image-loading problem where a locally built image is visible to Docker but not to Kubernetes.

The shortest local loop is:

```cmd
scripts\k8s-start.cmd docs
```

It applies the local overlay, waits for the API Deployment, seeds `/vault` from `docs`, prints the dashboard URL, and starts a blocking port-forward to `http://127.0.0.1:8080/dashboard`. Press `Ctrl+C` to stop port-forwarding.

PowerShell equivalent:

```powershell
.\scripts\k8s-start.ps1 -Source docs
```

To start without seeding the PVC:

```cmd
scripts\k8s-start.cmd -
```

To clear the PVC before seeding:

```cmd
scripts\k8s-start.cmd docs cronpot-local 8080 /clear
```

```powershell
.\scripts\k8s-render.ps1 -Overlay local
.\scripts\k8s-deploy.ps1 -Overlay local
.\scripts\k8s-port-forward.ps1 -Namespace cronpot-local
```

If PowerShell blocks `.ps1` scripts, use the `.cmd` helpers instead:

```cmd
scripts\k8s-render.cmd local
scripts\k8s-deploy.cmd local
scripts\k8s-port-forward.cmd cronpot-local
```

The local deploy helper restarts the API Deployment after applying manifests so updated source files mounted from the ConfigMap are loaded by Python.

The Docker image build is still useful for production and for learning the container path:

```cmd
scripts\docker-build.cmd
```

Use this loop when changing Kubernetes resources:

```cmd
scripts\k8s-render.cmd local
scripts\k8s-deploy.cmd local
kubectl -n cronpot-local rollout status deployment/cronpot-api
```

Use this loop when changing Python code used by the local overlay:

```cmd
scripts\k8s-deploy.cmd local
scripts\k8s-port-forward.cmd cronpot-local
```

The local overlay includes every imported `cronpot` module in the source ConfigMap. If a new Python module is added and the pod fails with `ModuleNotFoundError`, add that module to `k8s/overlays/local/kustomization.yaml`.

## Seeding The Vault

The local cluster starts with an empty PVC. Seed it from a local vault folder:

```cmd
scripts\k8s-seed-vault.cmd docs cronpot-local
```

To clear the PVC first and then seed it:

```cmd
scripts\k8s-seed-vault.cmd docs cronpot-local /clear
```

PowerShell equivalent:

```powershell
.\scripts\k8s-seed-vault.ps1 -Source docs -Namespace cronpot-local -Clear
```

Then port-forward and check analytics:

```cmd
scripts\k8s-port-forward.cmd cronpot-local
```

```powershell
Invoke-RestMethod http://127.0.0.1:8080/analytics
```

Expected result after seeding: the Kubernetes API should report the same recipe count as the local CLI.

This copies files into the Kubernetes PVC. It does not require Git, and it avoids storing hundreds of Markdown files inside Kubernetes ConfigMaps.

## API Checks

With port-forwarding running, try:

```powershell
Invoke-RestMethod http://127.0.0.1:8080/recipes
Invoke-RestMethod "http://127.0.0.1:8080/recipes?tag=meaty&category=Mains"
Invoke-RestMethod "http://127.0.0.1:8080/recipes/Roast%20Chicken"
Invoke-RestMethod "http://127.0.0.1:8080/shopping-list?recipe=Aglio%20e%20Olio&recipe=Roast%20Chicken"
```

Open the dashboard at:

```text
http://127.0.0.1:8080/dashboard
```

The dashboard reads the same vault and config as the API endpoints. The analytics CronJob also mounts `/config/cronpot.toml`, so scheduled analytics use the same ingredient normalisation settings as the dashboard.

## Smoke Testing

The local smoke helper deploys the local overlay, seeds the PVC, opens a temporary port-forward, and checks `/healthz`, `/readyz`, and `/analytics`:

```cmd
scripts\k8s-smoke.cmd docs
```

PowerShell equivalent:

```powershell
.\scripts\k8s-smoke.ps1 -Source docs
```

This is intentionally a live-cluster smoke test rather than a unit test. CI still renders every overlay without needing a cluster.

## Background Workers

CronPot has a filesystem-backed ingest queue. Jobs live under `.cronpot/jobs` inside the vault PVC, so the queue survives pod restarts without adding a database yet.

API flow:

```powershell
Invoke-RestMethod http://127.0.0.1:8080/jobs/ingest -Method Post -ContentType "application/json" -Body '{"url":"https://example.com/recipe"}'
Invoke-RestMethod http://127.0.0.1:8080/jobs/run -Method Post
Invoke-RestMethod http://127.0.0.1:8080/jobs
```

CLI worker:

```cmd
cronpot worker --vault docs --once --workers 2
```

This gives CronPot real parallel worker semantics before introducing Redis, Postgres, or a dedicated worker Deployment.

## LLM Normalisation In Kubernetes

The base `cronpot.toml` includes an Ollama-oriented `[llm]` section:

```toml
[llm]
provider = "ollama"
base_url = "http://ollama.ollama.svc.cluster.local:11434"
model = "qwen2.5:3b"
auto_normalise_ingredients = true
rewrite_ingested_recipes = false
ingredient_limit = 120
```

To use LLM-backed analytics in Kubernetes, run Ollama in-cluster or expose an existing Ollama endpoint, then keep `auto_normalise_ingredients = true`. To let Kubernetes ingests rewrite extracted recipes into the vault style, also set `rewrite_ingested_recipes = true`.

This is a good next teaching slice because it introduces:

- Kubernetes service discovery through the Ollama service DNS name
- resource requests and limits for model-serving workloads
- optional GPU scheduling later
- model cache persistence through a separate PVC
- failure isolation, because CronPot should keep serving even if the LLM is unavailable

## Troubleshooting

If port-forwarding says the pod is not running, check:

```cmd
kubectl -n cronpot-local get pods
kubectl -n cronpot-local get events --sort-by=.lastTimestamp
```

If `kubectl` reports that it cannot download OpenAPI from `127.0.0.1` or that the target machine refused the connection, the Kubernetes API is not reachable. Start Docker Desktop, ensure Kubernetes is enabled, then check:

```cmd
kubectl cluster-info
kubectl config current-context
```

The unified start helpers run this check before applying manifests so the failure points at cluster availability rather than YAML validation.

`ErrImageNeverPull` means Kubernetes could not see a local image. Re-apply the current local overlay:

```cmd
scripts\k8s-deploy.cmd local
```

The local overlay should use `python:3.12-slim`, not `cronpot:local`.

After the pod is running, call:

```powershell
Invoke-RestMethod http://127.0.0.1:8080/healthz
Invoke-RestMethod http://127.0.0.1:8080/analytics
```

If the dashboard works locally but the scheduled analytics output looks different, inspect the CronJob pod:

```cmd
kubectl -n cronpot-local create job --from=cronjob/cronpot-analytics cronpot-analytics-manual
kubectl -n cronpot-local logs job/cronpot-analytics-manual
```

The CronJob should mount both `/vault` and `/config`, matching the API pod.

## Cluster Deployments

Render any deployable overlay before wiring real clusters:

```cmd
scripts\k8s-render.cmd dev
scripts\k8s-render.cmd staging
scripts\k8s-render.cmd production
```

GitHub Actions builds and pushes an image to GHCR, applies the selected overlay, then updates the Deployment and CronJob images to the pushed SHA tag.

Repository secrets:

- `KUBE_CONFIG_DEV` for namespace `cronpot-dev`
- `KUBE_CONFIG_STAGING` for namespace `cronpot-staging`
- `KUBE_CONFIG_PRODUCTION` for namespace `cronpot`

`dev` can deploy automatically from `master`; `staging` and `production` are manual workflow dispatch targets.
