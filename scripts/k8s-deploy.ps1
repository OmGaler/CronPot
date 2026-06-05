param(
    [ValidateSet("local", "dev", "staging", "production")]
    [string]$Overlay = "local"
)

$ErrorActionPreference = "Stop"

kubectl kustomize --load-restrictor=LoadRestrictionsNone "k8s/overlays/$Overlay" | kubectl apply -f -

if ($Overlay -eq "local") {
    kubectl -n cronpot-local rollout restart deployment/cronpot-api
    kubectl -n cronpot-local rollout status deployment/cronpot-api --timeout=180s
}
