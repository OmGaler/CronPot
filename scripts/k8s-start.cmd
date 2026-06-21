@echo off
setlocal EnableDelayedExpansion

set "SOURCE=%~1"
if "%SOURCE%"=="" set "SOURCE=docs"

set "NAMESPACE=cronpot-local"
set "LOCAL_PORT=8080"
set "CLEAR="
set "LAN="

if not "%~2"=="" (
  if /I "%~2"=="/lan" (
    set "LAN=/lan"
  ) else if /I "%~2"=="/clear" (
    set "CLEAR=/clear"
  ) else (
    set "NAMESPACE=%~2"
  )
)

if not "%~3"=="" (
  if /I "%~3"=="/lan" (
    set "LAN=/lan"
  ) else if /I "%~3"=="/clear" (
    set "CLEAR=/clear"
  ) else (
    set "LOCAL_PORT=%~3"
  )
)

if not "%~4"=="" (
  if /I "%~4"=="/lan" (
    set "LAN=/lan"
  ) else if /I "%~4"=="/clear" (
    set "CLEAR=/clear"
  )
)

if not "%~5"=="" (
  if /I "%~5"=="/lan" set "LAN=/lan"
  if /I "%~5"=="/clear" set "CLEAR=/clear"
)

kubectl cluster-info >nul 2>nul
if errorlevel 1 (
  echo Could not reach the Kubernetes API for the current kubectl context.
  echo Start Docker Desktop, enable Kubernetes, or switch to a working context with kubectl config use-context.
  echo Then run: scripts\k8s-start.cmd %SOURCE%
  exit /b 1
)

if /I "%LAN%"=="/lan" (
  set /a LAN_CODE=(%RANDOM% * 31 + %RANDOM%) %% 1000000
  set "LAN_CODE=000000!LAN_CODE!"
  set "LAN_CODE=!LAN_CODE:~-6!"
  kubectl create namespace "%NAMESPACE%" --dry-run=client -o yaml | kubectl apply -f -
  if errorlevel 1 exit /b %ERRORLEVEL%
  kubectl -n "%NAMESPACE%" create secret generic cronpot-local-auth --from-literal="code=!LAN_CODE!" --dry-run=client -o yaml | kubectl apply -f -
  if errorlevel 1 exit /b %ERRORLEVEL%
) else (
  kubectl -n "%NAMESPACE%" delete secret cronpot-local-auth --ignore-not-found >nul 2>nul
)

call "%~dp0k8s-deploy.cmd" local
if errorlevel 1 exit /b %ERRORLEVEL%

if not "%SOURCE%"=="-" (
  call "%~dp0k8s-seed-vault.cmd" "%SOURCE%" "%NAMESPACE%" "%CLEAR%"
  if errorlevel 1 exit /b %ERRORLEVEL%
)

echo CronPot Kubernetes dashboard: http://127.0.0.1:%LOCAL_PORT%/dashboard
if /I "%LAN%"=="/lan" (
  echo CronPot Kubernetes mobile pairing code: !LAN_CODE!
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | ForEach-Object { 'CronPot Kubernetes mobile URL: http://' + $_.IPAddress + ':%LOCAL_PORT%/mobile' }"
)
echo Press Ctrl+C to stop port-forwarding.
call "%~dp0k8s-port-forward.cmd" "%NAMESPACE%" "%LOCAL_PORT%" "%LAN%"
exit /b %ERRORLEVEL%
