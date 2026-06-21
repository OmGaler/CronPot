param(
    [string]$Source = "docs",
    [string]$Namespace = "cronpot-local",
    [int]$LocalPort = 18080,
    [int]$RecipePort = 18081
)

$ErrorActionPreference = "Stop"

kubectl cluster-info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Could not reach the Kubernetes API for the current kubectl context."
}

kubectl -n $Namespace delete secret cronpot-local-auth --ignore-not-found *> $null
& "$PSScriptRoot\k8s-deploy.ps1" -Overlay local
& "$PSScriptRoot\k8s-seed-vault.ps1" -Source $Source -Namespace $Namespace -Clear

$recipeSource = Join-Path ([System.IO.Path]::GetTempPath()) "cronpot-k8s-recipe-$([guid]::NewGuid().ToString('N'))"
$syncTarget = Join-Path ([System.IO.Path]::GetTempPath()) "cronpot-k8s-sync-target-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $recipeSource | Out-Null
New-Item -ItemType Directory -Path $syncTarget | Out-Null

$recipeHtml = @'
<!doctype html>
<html>
<head>
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "Recipe",
    "name": "Kubernetes Smoke Soup",
    "recipeIngredient": ["1/2 tsp salt", "1 carrot"],
    "recipeInstructions": ["Chop the carrot.", "Simmer with salt."]
  }
  </script>
</head>
<body>Kubernetes smoke recipe</body>
</html>
'@
$recipePath = Join-Path $recipeSource "recipe.html"
Set-Content -Path $recipePath -Value $recipeHtml -Encoding UTF8

$recipeServer = Start-Process python `
    -ArgumentList @("-m", "http.server", "$RecipePort", "--bind", "0.0.0.0", "--directory", $recipeSource) `
    -WindowStyle Hidden `
    -PassThru

$portForward = Start-Process kubectl `
    -ArgumentList @("-n", $Namespace, "port-forward", "service/cronpot-api", "${LocalPort}:80") `
    -WindowStyle Hidden `
    -PassThru

try {
    Start-Sleep -Seconds 4
    Invoke-RestMethod "http://127.0.0.1:$RecipePort/recipe.html" | Out-Null
    Invoke-RestMethod "http://127.0.0.1:$LocalPort/healthz" | Out-Null
    Invoke-RestMethod "http://127.0.0.1:$LocalPort/readyz" | Out-Null
    $analytics = Invoke-RestMethod "http://127.0.0.1:$LocalPort/analytics"
    if ($analytics.recipe_count -lt 1) {
        throw "Expected at least one recipe in analytics response."
    }

    $job = Invoke-RestMethod "http://127.0.0.1:$LocalPort/jobs/ingest" `
        -Method Post `
        -ContentType "application/json" `
        -Body (@{ url = "http://host.docker.internal:$RecipePort/recipe.html" } | ConvertTo-Json)

    $deadline = (Get-Date).AddSeconds(120)
    do {
        Start-Sleep -Seconds 3
        $jobDetail = Invoke-RestMethod "http://127.0.0.1:$LocalPort/jobs/$($job.id)"
        if ($jobDetail.status -eq "failed") {
            throw "Smoke ingest job failed: $($jobDetail.error)"
        }
    } while ($jobDetail.status -ne "complete" -and (Get-Date) -lt $deadline)

    if ($jobDetail.status -ne "complete") {
        throw "Timed out waiting for smoke ingest job $($job.id)."
    }

    & "$PSScriptRoot\k8s-sync-back.ps1" -Target $syncTarget -Namespace $Namespace
    $syncedRecipe = Join-Path $syncTarget "Kubernetes Smoke Soup.md"
    if (-not (Test-Path -LiteralPath $syncedRecipe -PathType Leaf)) {
        throw "Expected synced recipe at $syncedRecipe."
    }

    Write-Host "Kubernetes smoke passed with $($analytics.recipe_count) seeded recipe(s), completed job $($job.id), and sync-back verified."
}
finally {
    if ($portForward -and -not $portForward.HasExited) {
        Stop-Process -Id $portForward.Id -Force
    }
    if ($recipeServer -and -not $recipeServer.HasExited) {
        Stop-Process -Id $recipeServer.Id -Force
    }
    if (Test-Path -LiteralPath $recipeSource) {
        Remove-Item -LiteralPath $recipeSource -Recurse -Force
    }
    if (Test-Path -LiteralPath $syncTarget) {
        Remove-Item -LiteralPath $syncTarget -Recurse -Force
    }
}
