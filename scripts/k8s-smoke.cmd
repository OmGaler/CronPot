@echo off
setlocal

set "SOURCE=%~1"
if "%SOURCE%"=="" set "SOURCE=docs"

set "NAMESPACE=%~2"
if "%NAMESPACE%"=="" set "NAMESPACE=cronpot-local"

set "LOCAL_PORT=%~3"
if "%LOCAL_PORT%"=="" set "LOCAL_PORT=18080"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0k8s-smoke.ps1" -Source "%SOURCE%" -Namespace "%NAMESPACE%" -LocalPort %LOCAL_PORT%
exit /b %ERRORLEVEL%
