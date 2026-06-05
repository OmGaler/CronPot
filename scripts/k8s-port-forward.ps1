param(
    [string]$Namespace = "cronpot-local",
    [int]$LocalPort = 8080
)

$ErrorActionPreference = "Stop"

kubectl -n $Namespace port-forward service/cronpot-api "${LocalPort}:80"
