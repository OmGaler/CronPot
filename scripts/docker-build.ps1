param(
    [string]$Tag = "cronpot:local"
)

$ErrorActionPreference = "Stop"

docker build -t $Tag .
