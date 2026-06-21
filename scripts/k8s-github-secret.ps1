param(
    [Parameter(Mandatory = $true)]
    [string]$Repo,
    [string]$Namespace = "cronpot-local",
    [string]$Branch = "main",
    [string]$Path = ".",
    [string]$Username = "x-access-token",
    [string]$AuthorName = "CronPot",
    [string]$AuthorEmail = "cronpot@example.local"
)

$ErrorActionPreference = "Stop"

if (-not $env:CRONPOT_GITHUB_TOKEN) {
    throw "Set CRONPOT_GITHUB_TOKEN to a GitHub token that can read and write the vault repository."
}

if ($Repo -match "^[a-z][a-z0-9+.-]*://[^/@]+@") {
    throw "The vault repository URL must not contain credentials. Use CRONPOT_GITHUB_TOKEN instead."
}

function ConvertTo-RepositoryIdentity {
    param([string]$Url)

    $value = $Url.Trim().TrimEnd("/")
    if ($value.EndsWith(".git", [System.StringComparison]::OrdinalIgnoreCase)) {
        $value = $value.Substring(0, $value.Length - 4)
    }
    if ($value -match "^git@([^:]+):(.+)$") {
        return "$($Matches[1])/$($Matches[2])".ToLowerInvariant()
    }
    if ($value -match "^https?://([^/]+)/(.+)$") {
        return "$($Matches[1])/$($Matches[2])".ToLowerInvariant()
    }
    return $value.ToLowerInvariant()
}

$origin = git remote get-url origin 2>$null
if ($LASTEXITCODE -eq 0 -and (ConvertTo-RepositoryIdentity $Repo) -eq (ConvertTo-RepositoryIdentity $origin)) {
    throw "The vault repository matches this CronPot project repository. Use a separate recipe vault repository."
}

kubectl create namespace $Namespace --dry-run=client -o yaml | kubectl apply -f -
kubectl -n $Namespace create secret generic cronpot-vault-github `
    --from-literal="repo=$Repo" `
    --from-literal="token=$env:CRONPOT_GITHUB_TOKEN" `
    --from-literal="branch=$Branch" `
    --from-literal="path=$Path" `
    --from-literal="username=$Username" `
    --from-literal="author_name=$AuthorName" `
    --from-literal="author_email=$AuthorEmail" `
    --dry-run=client `
    -o yaml | kubectl apply -f -

Write-Host "Configured GitHub vault Secret cronpot-vault-github in namespace $Namespace."
