param(
    [string]$Source = "docs",
    [string]$Namespace = "cronpot-local",
    [int]$LocalPort = 8080,
    [switch]$Clear,
    [switch]$Lan
)

$ErrorActionPreference = "Stop"

kubectl cluster-info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Could not reach the Kubernetes API for the current kubectl context. Start Docker Desktop, enable Kubernetes, or switch to a working context with kubectl config use-context."
}

if ($Lan) {
    $code = "{0:D6}" -f (Get-Random -Minimum 0 -Maximum 1000000)
    kubectl create namespace $Namespace --dry-run=client -o yaml | kubectl apply -f -
    kubectl -n $Namespace create secret generic cronpot-local-auth --from-literal="code=$code" --dry-run=client -o yaml | kubectl apply -f -
} else {
    kubectl -n $Namespace delete secret cronpot-local-auth --ignore-not-found *> $null
}

& "$PSScriptRoot\k8s-deploy.ps1" -Overlay local

if ($Source -ne "-") {
    & "$PSScriptRoot\k8s-seed-vault.ps1" -Source $Source -Namespace $Namespace -Clear:$Clear
}

Write-Host "CronPot Kubernetes dashboard: http://127.0.0.1:$LocalPort/dashboard"
if ($Lan) {
    Write-Host "CronPot Kubernetes mobile pairing code: $code"
    $addresses = @(
        Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
            Select-Object -ExpandProperty IPAddress
    )
    foreach ($address in $addresses) {
        Write-Host "CronPot Kubernetes mobile URL: http://${address}:$LocalPort/mobile"
    }
}
Write-Host "Press Ctrl+C to stop port-forwarding."
& "$PSScriptRoot\k8s-port-forward.ps1" -Namespace $Namespace -LocalPort $LocalPort -Lan:$Lan
