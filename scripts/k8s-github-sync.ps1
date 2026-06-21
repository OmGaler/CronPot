param(
    [ValidateSet("pull", "push")]
    [string]$Direction = "push",
    [string]$Namespace = "cronpot-local",
    [string]$Message = "Sync CronPot vault from Kubernetes",
    [int]$TimeoutSeconds = 180,
    [switch]$KeepJob
)

$ErrorActionPreference = "Stop"

function ConvertTo-YamlSingleQuoted {
    param([string]$Value)
    return "'" + ($Value -replace "'", "''") + "'"
}

function Indent-Script {
    param([string]$Script)
    return (($Script -split "`r?`n") | ForEach-Object { "                $_" }) -join "`n"
}

$jobName = "cronpot-github-$Direction-$((Get-Date).ToString('yyyyMMddHHmmss'))"

$pullScript = @'
set -eu
cat > /tmp/git-askpass <<'EOF'
#!/bin/sh
case "$1" in
  *Username*) printf '%s\n' "${GIT_USERNAME:-x-access-token}" ;;
  *) printf '%s\n' "$GITHUB_TOKEN" ;;
esac
EOF
chmod 700 /tmp/git-askpass
export GIT_ASKPASS=/tmp/git-askpass
export GIT_TERMINAL_PROMPT=0

git clone --depth 1 --branch "$GITHUB_BRANCH" "$GITHUB_REPO" /work/repo
git config --global --add safe.directory /work/repo
repo_path="/work/repo/$GITHUB_PATH"
if [ ! -d "$repo_path" ]; then
  echo "Repository path does not exist: $GITHUB_PATH" >&2
  exit 1
fi

find /vault -mindepth 1 -maxdepth 1 -exec rm -rf {} +
mkdir -p /vault
cp -a "$repo_path"/. /vault/
rm -rf /vault/.git /vault/.cronpot
echo "Pulled GitHub vault into /vault."
'@

$pushScript = @'
set -eu
cat > /tmp/git-askpass <<'EOF'
#!/bin/sh
case "$1" in
  *Username*) printf '%s\n' "${GIT_USERNAME:-x-access-token}" ;;
  *) printf '%s\n' "$GITHUB_TOKEN" ;;
esac
EOF
chmod 700 /tmp/git-askpass
export GIT_ASKPASS=/tmp/git-askpass
export GIT_TERMINAL_PROMPT=0

git clone --depth 1 --branch "$GITHUB_BRANCH" "$GITHUB_REPO" /work/repo
git config --global --add safe.directory /work/repo
repo_path="/work/repo/$GITHUB_PATH"
if [ ! -d "$repo_path" ]; then
  mkdir -p "$repo_path"
fi

cp -a /vault/. "$repo_path"/
rm -rf "$repo_path/.cronpot"

git -C /work/repo config user.name "$GIT_AUTHOR_NAME"
git -C /work/repo config user.email "$GIT_AUTHOR_EMAIL"
if [ -z "$(git -C /work/repo status --short)" ]; then
  echo "No GitHub vault changes to push."
  exit 0
fi

git -C /work/repo add -A
git -C /work/repo commit -m "$COMMIT_MESSAGE"
git -C /work/repo push origin "HEAD:$GITHUB_BRANCH"
echo "Pushed Kubernetes vault to GitHub."
'@

$script = if ($Direction -eq "pull") { $pullScript } else { $pushScript }
$message = ConvertTo-YamlSingleQuoted $Message
$scriptBlock = Indent-Script $script

$yaml = @"
apiVersion: batch/v1
kind: Job
metadata:
  name: $jobName
  namespace: $Namespace
  labels:
    app.kubernetes.io/name: cronpot
    app.kubernetes.io/component: github-sync
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 300
  template:
    metadata:
      labels:
        app.kubernetes.io/name: cronpot
        app.kubernetes.io/component: github-sync
    spec:
      restartPolicy: Never
      containers:
        - name: github-sync
          image: alpine/git:latest
          imagePullPolicy: IfNotPresent
          command:
            - /bin/sh
            - -c
            - |
$scriptBlock
          env:
            - name: GITHUB_REPO
              valueFrom:
                secretKeyRef:
                  name: cronpot-vault-github
                  key: repo
            - name: GITHUB_TOKEN
              valueFrom:
                secretKeyRef:
                  name: cronpot-vault-github
                  key: token
            - name: GITHUB_BRANCH
              valueFrom:
                secretKeyRef:
                  name: cronpot-vault-github
                  key: branch
            - name: GITHUB_PATH
              valueFrom:
                secretKeyRef:
                  name: cronpot-vault-github
                  key: path
            - name: GIT_USERNAME
              valueFrom:
                secretKeyRef:
                  name: cronpot-vault-github
                  key: username
            - name: GIT_AUTHOR_NAME
              valueFrom:
                secretKeyRef:
                  name: cronpot-vault-github
                  key: author_name
            - name: GIT_AUTHOR_EMAIL
              valueFrom:
                secretKeyRef:
                  name: cronpot-vault-github
                  key: author_email
            - name: COMMIT_MESSAGE
              value: $message
          volumeMounts:
            - name: vault
              mountPath: /vault
            - name: work
              mountPath: /work
      volumes:
        - name: vault
          persistentVolumeClaim:
            claimName: cronpot-vault
        - name: work
          emptyDir: {}
"@

$yaml | kubectl apply -f -
try {
    kubectl -n $Namespace wait --for=condition=complete "job/$jobName" --timeout="${TimeoutSeconds}s"
    kubectl -n $Namespace logs "job/$jobName"
}
catch {
    kubectl -n $Namespace logs "job/$jobName" --all-containers=true --ignore-errors=true
    throw
}
finally {
    if (-not $KeepJob) {
        kubectl -n $Namespace delete job $jobName --ignore-not-found | Out-Null
    }
}
