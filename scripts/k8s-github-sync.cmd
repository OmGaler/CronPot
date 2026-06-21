@echo off
setlocal

set "DIRECTION=%~1"
if "%DIRECTION%"=="" set "DIRECTION=push"

set "NAMESPACE=%~2"
if "%NAMESPACE%"=="" set "NAMESPACE=cronpot-local"

set "MESSAGE=%~3"
if "%MESSAGE%"=="" set "MESSAGE=Sync CronPot vault from Kubernetes"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0k8s-github-sync.ps1" -Direction "%DIRECTION%" -Namespace "%NAMESPACE%" -Message "%MESSAGE%"
exit /b %ERRORLEVEL%
