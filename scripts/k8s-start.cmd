@echo off
setlocal

set "SOURCE=%~1"
if "%SOURCE%"=="" set "SOURCE=docs"

set "NAMESPACE=%~2"
if "%NAMESPACE%"=="" set "NAMESPACE=cronpot-local"

set "LOCAL_PORT=%~3"
if "%LOCAL_PORT%"=="" set "LOCAL_PORT=8080"

set "CLEAR=%~4"

kubectl cluster-info >nul 2>nul
if errorlevel 1 (
  echo Could not reach the Kubernetes API for the current kubectl context.
  echo Start Docker Desktop, enable Kubernetes, or switch to a working context with kubectl config use-context.
  echo Then run: scripts\k8s-start.cmd %SOURCE%
  exit /b 1
)

call "%~dp0k8s-deploy.cmd" local
if errorlevel 1 exit /b %ERRORLEVEL%

if not "%SOURCE%"=="-" (
  call "%~dp0k8s-seed-vault.cmd" "%SOURCE%" "%NAMESPACE%" "%CLEAR%"
  if errorlevel 1 exit /b %ERRORLEVEL%
)

echo CronPot Kubernetes dashboard: http://127.0.0.1:%LOCAL_PORT%/dashboard
echo Press Ctrl+C to stop port-forwarding.
call "%~dp0k8s-port-forward.cmd" "%NAMESPACE%" "%LOCAL_PORT%"
exit /b %ERRORLEVEL%
