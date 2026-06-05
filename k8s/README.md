# Kubernetes Setup

CronPot uses Kustomize because `kubectl` includes it and Helm is not required.

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

## Environments

| Overlay | Namespace | Image source | Vault PVC |
| --- | --- | --- | --- |
| `local` | `cronpot-local` | `python:3.12-slim` plus mounted source | 1Gi |
| `dev` | `cronpot-dev` | GHCR image | 1Gi |
| `staging` | `cronpot-staging` | GHCR image | 2Gi |
| `production` | `cronpot` | GHCR image | 5Gi |

Each environment currently runs one API replica. The vault is a writable PVC, so multi-replica scaling should wait until we introduce RWX storage or move mutable state into a database/object store.

## Local Flow

The local overlay runs from `python:3.12-slim` and mounts the local source files through a ConfigMap. This avoids the Docker Desktop multi-node image-loading problem where a locally built image is visible to Docker but not to Kubernetes.

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

## Seeding The Vault

The local cluster starts with an empty PVC. Seed it from a local vault folder:

```cmd
scripts\k8s-seed-vault.cmd docs cronpot-local
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

## Troubleshooting

If port-forwarding says the pod is not running, check:

```cmd
kubectl -n cronpot-local get pods
kubectl -n cronpot-local get events --sort-by=.lastTimestamp
```

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
