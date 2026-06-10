param(
    [string]$Source = "docs",
    [string]$Namespace = "cronpot-local",
    [int]$LocalPort = 8080,
    [switch]$Clear
)

$ErrorActionPreference = "Stop"

kubectl cluster-info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Could not reach the Kubernetes API for the current kubectl context. Start Docker Desktop, enable Kubernetes, or switch to a working context with kubectl config use-context."
}

& "$PSScriptRoot\k8s-deploy.ps1" -Overlay local

if ($Source -ne "-") {
    & "$PSScriptRoot\k8s-seed-vault.ps1" -Source $Source -Namespace $Namespace -Clear:$Clear
}

Write-Host "CronPot Kubernetes dashboard: http://127.0.0.1:$LocalPort/dashboard"
Write-Host "Press Ctrl+C to stop port-forwarding."
& "$PSScriptRoot\k8s-port-forward.ps1" -Namespace $Namespace -LocalPort $LocalPort
