param(
    [string]$Namespace = "cronpot-local",
    [int]$LocalPort = 8080,
    [switch]$Lan
)

$ErrorActionPreference = "Stop"

$arguments = @("-n", $Namespace, "port-forward")
if ($Lan) {
    $arguments += @("--address", "0.0.0.0")
}
$arguments += @("service/cronpot-api", "${LocalPort}:80")
kubectl @arguments
