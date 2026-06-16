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

## Local Start

Start the local cluster flow and seed the PVC from `docs`:

```cmd
scripts\k8s-start.cmd docs
```

PowerShell:

```powershell
.\scripts\k8s-start.ps1 -Source docs
```

This applies the local overlay, restarts the API and worker Deployments, seeds `/vault`, prints the dashboard URL, and starts a blocking port-forward to:

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

Phone access on the same Wi-Fi, with care:

```powershell
kubectl -n cronpot-local port-forward --address 0.0.0.0 service/cronpot-api 8080:80
```

Then open this from the phone:

```text
http://YOUR-PC-IP:8080/mobile
```

The Kubernetes Deployment does not enable a pairing code by default. Use this only on a trusted private network, keep the default localhost-only port-forward for routine work, or add an explicit auth boundary before exposing write endpoints more broadly. For the simple paired phone flow, prefer `cronpot start --lan --vault docs` outside Kubernetes.

## Seeding The Vault

Copy a local vault into the Kubernetes PVC:

```cmd
scripts\k8s-seed-vault.cmd docs cronpot-local
```

Clear first:

```cmd
scripts\k8s-seed-vault.cmd docs cronpot-local /clear
```

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

## LLM Integration

The base config points at an in-cluster Ollama DNS name:

```toml
[llm]
base_url = "http://ollama.ollama.svc.cluster.local:11434"
model = "qwen2.5:3b"
auto_normalise_ingredients = true
rewrite_ingested_recipes = false
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

Pushes to `master` deploy `dev` automatically when `KUBE_CONFIG_DEV` exists. `staging` and `production` deploy through workflow dispatch.

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
