param(
    [string]$Source = "docs",
    [string]$Namespace = "cronpot-local",
    [int]$LocalPort = 18080
)

$ErrorActionPreference = "Stop"

kubectl cluster-info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Could not reach the Kubernetes API for the current kubectl context."
}

& "$PSScriptRoot\k8s-deploy.ps1" -Overlay local
& "$PSScriptRoot\k8s-seed-vault.ps1" -Source $Source -Namespace $Namespace

$portForward = Start-Process kubectl `
    -ArgumentList @("-n", $Namespace, "port-forward", "service/cronpot-api", "${LocalPort}:80") `
    -WindowStyle Hidden `
    -PassThru

try {
    Start-Sleep -Seconds 4
    Invoke-RestMethod "http://127.0.0.1:$LocalPort/healthz" | Out-Null
    Invoke-RestMethod "http://127.0.0.1:$LocalPort/readyz" | Out-Null
    $analytics = Invoke-RestMethod "http://127.0.0.1:$LocalPort/analytics"
    if ($analytics.recipe_count -lt 1) {
        throw "Expected at least one recipe in analytics response."
    }
    Write-Host "Kubernetes smoke passed with $($analytics.recipe_count) recipe(s)."
}
finally {
    if (-not $portForward.HasExited) {
        Stop-Process -Id $portForward.Id -Force
    }
}
