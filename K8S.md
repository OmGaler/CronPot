<p align="center">
  <img src="assets/cronpot-logo.svg" alt="CronPot logo" width="96">
</p>

# CronPot Kubernetes Reference

CronPot uses plain Kubernetes manifests with Kustomize overlays. Helm is not required. The detailed manifest walkthrough lives in [k8s/README.md](k8s/README.md); this file is the quick operator reference.

## What Kubernetes Runs

| Resource | Purpose |
| --- | --- |
| Namespace | Separates `local`, `dev`, `staging`, and `production`. |
| ServiceAccount | Gives CronPot workloads a named runtime identity. |
| ConfigMap | Mounts `cronpot.toml` and, in local mode, the Python source. |
| PersistentVolumeClaim | Stores the recipe vault at `/vault`. |
| API Deployment | Runs `cronpot serve` for dashboard and HTTP API. |
| Worker Deployment | Runs `cronpot worker` for background URL ingestion. |
| Service | Gives the API a stable in-cluster endpoint. |
| Analytics CronJob | Runs scheduled JSON analytics against the same vault. |
| NetworkPolicy | Limits ingress to API pods. |

## Environments

| Overlay | Namespace | Use |
| --- | --- | --- |
| `local` | `cronpot-local` | Laptop feedback loop with Docker Desktop and mounted source. |
| `dev` | `cronpot-dev` | First shared image-backed deployment. |
| `staging` | `cronpot-staging` | Production-shaped rehearsal. |
| `production` | `cronpot` | Real service namespace. |

Only `local` is started by the helper scripts. `dev`, `staging`, and `production` are intended for CI/CD or explicit deploy commands.

## Deployment Ladder

Treat the overlays as a learning sequence:

1. `local` runs on your laptop through Docker Desktop. It is the fast feedback loop and is never deployed by GitHub Actions.
2. `dev` proves that the image built by CI works in Kubernetes. A `master` push deploys it when `KUBE_CONFIG_DEV` exists, or it can be selected manually in the `CI/CD` workflow.
3. `staging` rehearses the release in its own namespace. It is manual-only.
4. `production` is the real deployment. It is manual-only.

To walk the complete path, run `scripts\k8s-start.cmd docs` locally, then use GitHub Actions to run `CI/CD` three times: `dev`, then `staging`, then `production`. Configure the matching `KUBE_CONFIG_*` secret for each environment first. A single remote cluster may serve all three namespaces with the same kubeconfig; separate secrets make the intended boundary clear.

GitHub-hosted Actions runners cannot use a Docker Desktop kubeconfig that points to `127.0.0.1`. CI deployment requires a remote cluster reachable by GitHub or a self-hosted runner that can reach the cluster.

## Local Start

Start the local cluster flow and seed the PVC from `docs`:

```cmd
scripts\k8s-start.cmd docs
```

PowerShell:

```powershell
.\scripts\k8s-start.ps1 -Source docs
```

This applies the local overlay, restarts the API and worker Deployments, seeds `docs` into `/vault/docs`, prints the dashboard URL, and starts a blocking port-forward to:

```text
http://127.0.0.1:8080/dashboard
```

Start without seeding:

```cmd
scripts\k8s-start.cmd -
```

Clear the PVC before seeding:

```cmd
scripts\k8s-start.cmd docs cronpot-local 8080 /clear
```

## Local Mobile Access

Start local Kubernetes with LAN pairing:

```cmd
scripts\k8s-start.cmd docs /lan
```

PowerShell:

```powershell
.\scripts\k8s-start.ps1 -Source docs -Lan
```

The helper:

- generates a six digit pairing code
- stores it in Secret `cronpot-local-auth` as key `code`
- deploys/restarts the local API pod
- binds port-forwarding to `0.0.0.0`
- prints mobile URLs such as `http://192.168.1.42:8080/mobile`

The local API Deployment reads that Secret through `CRONPOT_AUTH_CODE`. The Secret is optional in the manifest so ordinary local rendering still works. Running `scripts\k8s-start.cmd docs` without `/lan` deletes the local auth Secret and returns to localhost-only development.

## Render And Deploy

Render an overlay without applying it:

```cmd
scripts\k8s-render.cmd local
scripts\k8s-render.cmd dev
scripts\k8s-render.cmd staging
scripts\k8s-render.cmd production
```

Apply local:

```cmd
scripts\k8s-deploy.cmd local
```

The local deploy helper restarts both long-running Deployments so ConfigMap-mounted source updates are loaded by Python.

## Port Forwarding

Local browser access:

```cmd
scripts\k8s-port-forward.cmd cronpot-local
```

Manual phone access on the same Wi-Fi, with care:

```powershell
kubectl -n cronpot-local port-forward --address 0.0.0.0 service/cronpot-api 8080:80
```

Then open this from the phone:

```text
http://YOUR-PC-IP:8080/mobile
```

Prefer the `/lan` helper above because it also creates the pairing Secret. Use manual `--address 0.0.0.0` only on a trusted private network or after creating your own auth Secret.

## Seeding The Vault

Copy a local vault into the Kubernetes PVC:

```cmd
scripts\k8s-seed-vault.cmd docs cronpot-local
```

Clear first:

```cmd
scripts\k8s-seed-vault.cmd docs cronpot-local /clear
```

## Syncing Back From Kubernetes

Copy the PVC-backed vault back into a local folder:

```cmd
cronpot k8s status --namespace cronpot-local
cronpot k8s sync-back docs --namespace cronpot-local
```

`status` is the quick health check. It reports whether `kubectl` is available, whether the cluster is reachable, whether the namespace exists, and, when possible, the running API pod, worker readiness, vault recipe count, and stored job count.

Script wrapper:

```powershell
.\scripts\k8s-sync-back.ps1 -Target docs -Namespace cronpot-local
```

Sync-back is additive by default: it copies new and changed files from `/vault` into the target folder, but it does not delete local files that are absent from the PVC. This is deliberate for Obsidian/Git-backed vaults. Runtime queue files under `.cronpot` are not copied back.

Copy a local vault folder into the PVC before pushing those local changes to GitHub:

```cmd
cronpot k8s push-local docs --namespace cronpot-local
```

This copies `docs` into `/vault/docs` by default. Use `--clear` to replace the remote folder contents before copying.

If the target is a Git repository, show changes and commit them:

```cmd
cronpot k8s sync-back docs --namespace cronpot-local --commit --message "Sync CronPot vault from Kubernetes"
```

```powershell
.\scripts\k8s-sync-back.ps1 -Target docs -Namespace cronpot-local -Commit -Message "Sync CronPot vault from Kubernetes"
```

## Syncing Directly With GitHub

Configure a Kubernetes Secret for a separate GitHub-backed vault repository. Do not use the public CronPot application repository as the vault; the CLI rejects a URL matching this checkout's `origin` remote. Use a fine-grained GitHub token with repository contents read/write access. Prefer an environment variable so the token is not typed into shell history:

```powershell
$env:CRONPOT_GITHUB_TOKEN = "github_pat_..."
cronpot k8s github secret --namespace cronpot-local --repo "https://github.com/YOU/YOUR-VAULT.git" --branch main --path "." --author-name "cronpot-bot" --author-email "cronpot-bot@example.local"
```

Short alias:

```cmd
cronpot k github secret --namespace cronpot-local --repo "https://github.com/YOU/YOUR-VAULT.git" --author-name "cronpot-bot" --author-email "cronpot-bot@example.local"
```

Pull the repository into the PVC:

```cmd
cronpot k8s github pull --namespace cronpot-local
```

This updates the Kubernetes PVC at `/vault`. It does not change your local `docs` folder unless you sync back afterwards:

```cmd
cronpot k8s github pull --namespace cronpot-local --sync-back docs
```

Push the current PVC contents back to GitHub:

```cmd
cronpot k8s push-local docs --namespace cronpot-local
cronpot k8s github push --namespace cronpot-local --message "Sync CronPot vault from Kubernetes"
cronpot k8s github push --namespace cronpot-local --seed-from docs
cronpot k8s github push --namespace cronpot-local --no-seed
```

`github push` seeds from local `docs` into `/vault/docs` by default, removes duplicate top-level Markdown files left by older bad pushes, then pushes `/vault` to GitHub. Use `--seed-from other-vault` for a different local folder. Use `--no-seed` only when you deliberately want to push whatever is already in `/vault`.

The sync runs as a one-shot Kubernetes Job using `alpine/git`. It mounts the same `cronpot-vault` PVC as the API and worker, skips `.cronpot` runtime queue files when pushing, preserves GitHub files that are absent from the PVC, and deletes the Job after completion by default.

The older `scripts\k8s-github-*.ps1` and `.cmd` wrappers remain available, but the CLI is the preferred interface because it validates options consistently.

## Jobs And Workers

Queue an ingest job through the API:

```powershell
Invoke-RestMethod http://127.0.0.1:8080/jobs/ingest -Method Post -ContentType "application/json" -Body '{"url":"https://example.com/recipe"}'
```

Inspect the worker:

```cmd
kubectl -n cronpot-local logs deployment/cronpot-worker
kubectl -n cronpot-local get pods
```

The queue is currently file-backed under `.cronpot/jobs` on the vault PVC. Keep the worker Deployment at one replica until CronPot gains cross-pod locking or an external queue.

## Analytics CronJob

Run scheduled analytics manually:

```cmd
kubectl -n cronpot-local create job --from=cronjob/cronpot-analytics cronpot-analytics-manual
kubectl -n cronpot-local logs job/cronpot-analytics-manual
```

The CronJob mounts the same `/vault` and `/config/cronpot.toml` as the API and worker.

## Live Smoke Test

Run the live smoke path:

```cmd
scripts\k8s-smoke.cmd docs
```

The smoke helper deploys the local overlay, clears and seeds the PVC, starts a temporary local recipe source, queues an ingest job through the API, waits for the worker Deployment to complete it, syncs the PVC back into a temporary local folder, and verifies that the new recipe Markdown was copied back.

## LLM Integration

The base config points at an in-cluster Ollama DNS name:

```toml
[llm]
base_url = "http://ollama.ollama.svc.cluster.local:11434"
model = "qwen2.5:3b"
auto_normalise_ingredients = true
rewrite_ingested_recipes = true
```

For local-only development, either deploy Ollama in-cluster or change the ConfigMap to point to an endpoint reachable from the pod.

## CI/CD

GitHub Actions renders all overlays, builds a GHCR image, applies the selected overlay, then updates these images to the pushed SHA tag:

- `deployment/cronpot-api`
- `deployment/cronpot-worker`
- `cronjob/cronpot-analytics`

Required secrets:

- `KUBE_CONFIG_DEV`
- `KUBE_CONFIG_STAGING`
- `KUBE_CONFIG_PRODUCTION`

Pushes to `master` deploy `dev` automatically when `KUBE_CONFIG_DEV` exists. `staging` and `production` deploy through workflow dispatch. Before any remote deployment is configured, CI still renders all four overlays, checks their namespace/image/storage contracts, builds the container image, and calls its health endpoints.

## Troubleshooting

Check cluster access:

```cmd
kubectl cluster-info
kubectl config current-context
```

Check pods and recent events:

```cmd
kubectl -n cronpot-local get pods
kubectl -n cronpot-local get events --sort-by=.lastTimestamp
```

If `kubectl` cannot reach `127.0.0.1:<port>/openapi/v2`, Docker Desktop Kubernetes is probably stopped or the current context is stale.
