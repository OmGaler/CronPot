@echo off
setlocal

set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=docs"

set "NAMESPACE=%~2"
if "%NAMESPACE%"=="" set "NAMESPACE=cronpot-local"

set "COMMIT=%~3"
set "MESSAGE=%~4"
if "%MESSAGE%"=="" set "MESSAGE=Sync CronPot vault from Kubernetes"

if /I "%COMMIT%"=="/commit" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0k8s-sync-back.ps1" -Target "%TARGET%" -Namespace "%NAMESPACE%" -Commit -Message "%MESSAGE%"
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0k8s-sync-back.ps1" -Target "%TARGET%" -Namespace "%NAMESPACE%" -Message "%MESSAGE%"
)
exit /b %ERRORLEVEL%
