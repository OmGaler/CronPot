param(
    [string]$Source = "docs",
    [string]$Namespace = "cronpot-local",
    [switch]$Clear
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -Path $Source -PathType Container)) {
    throw "Source vault folder does not exist: $Source"
}

$pod = kubectl -n $Namespace get pod `
    -l app.kubernetes.io/component=api `
    -o jsonpath="{.items[?(@.status.phase=='Running')].metadata.name}"

if (-not $pod) {
    throw "No running API pod found in namespace $Namespace."
}

$pod = ($pod -split "\s+")[0]

if ($Clear) {
    kubectl -n $Namespace exec $pod -- sh -c "find /vault -mindepth 1 -maxdepth 1 -exec rm -rf {} +"
}

kubectl -n $Namespace exec $pod -- mkdir -p /vault
kubectl -n $Namespace cp "$Source/." "${pod}:/vault" --container api

$count = kubectl -n $Namespace exec $pod -- sh -c "find /vault -maxdepth 1 -name '*.md' | wc -l"
Write-Host "Seeded $count Markdown file(s) into $Namespace/$pod:/vault"
