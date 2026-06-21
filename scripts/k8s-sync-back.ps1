param(
    [string]$Target = "docs",
    [string]$Namespace = "cronpot-local",
    [switch]$Commit,
    [string]$Message = "Sync CronPot vault from Kubernetes"
)

$ErrorActionPreference = "Stop"

$pod = kubectl -n $Namespace get pod `
    -l app.kubernetes.io/component=api `
    -o jsonpath="{.items[?(@.status.phase=='Running')].metadata.name}"

if (-not $pod) {
    throw "No running API pod found in namespace $Namespace."
}

$pod = ($pod -split "\s+")[0]
$targetPath = Resolve-Path -Path $Target -ErrorAction SilentlyContinue
if ($targetPath) {
    $targetPath = $targetPath.Path
} else {
    New-Item -ItemType Directory -Path $Target | Out-Null
    $targetPath = (Resolve-Path -Path $Target).Path
}

$staging = Join-Path ([System.IO.Path]::GetTempPath()) "cronpot-k8s-sync-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $staging | Out-Null

try {
    Push-Location $staging
    try {
        kubectl -n $Namespace cp "${pod}:/vault/." "." --container api
    }
    finally {
        Pop-Location
    }

    $copySource = $staging
    $nested = Join-Path $staging (Split-Path -Leaf $targetPath)
    $topLevelRecipes = Get-ChildItem -LiteralPath $staging -Filter "*.md" -File -ErrorAction SilentlyContinue
    if ((Test-Path -LiteralPath $nested -PathType Container) -and $topLevelRecipes.Count -eq 0) {
        Write-Host "Detected /vault/$(Split-Path -Leaf $targetPath); syncing that folder into $targetPath instead of creating a nested copy."
        $copySource = $nested
    }

    Get-ChildItem -LiteralPath $copySource -Force | Where-Object { $_.Name -ne ".cronpot" } | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $targetPath -Recurse -Force
    }

    $count = (Get-ChildItem -LiteralPath $targetPath -Filter "*.md" -File -ErrorAction SilentlyContinue | Measure-Object).Count
    Write-Host "Synced Kubernetes vault from $Namespace/${pod}:/vault to $targetPath"
    Write-Host "Target now contains $count Markdown file(s)."

    if (Test-Path -LiteralPath (Join-Path $targetPath ".git") -PathType Container) {
        $status = git -C $targetPath status --short
        if ($status) {
            Write-Host "Git changes:"
            $status | ForEach-Object { Write-Host $_ }
        } else {
            Write-Host "Git changes: none"
        }

        if ($Commit -and $status) {
            git -C $targetPath add -A
            git -C $targetPath commit -m $Message
        } elseif ($Commit) {
            Write-Host "Git commit skipped: no changes."
        }
    } elseif ($Commit) {
        Write-Host "Git commit skipped: target is not a Git repository."
    }
}
finally {
    if (Test-Path -LiteralPath $staging) {
        Remove-Item -LiteralPath $staging -Recurse -Force
    }
}
