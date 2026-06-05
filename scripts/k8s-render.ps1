param(
    [ValidateSet("local", "dev", "staging", "production")]
    [string]$Overlay = "local"
)

$ErrorActionPreference = "Stop"

kubectl kustomize --load-restrictor=LoadRestrictionsNone "k8s/overlays/$Overlay"
