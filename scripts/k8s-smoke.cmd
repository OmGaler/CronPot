@echo off
setlocal

set "SOURCE=%~1"
if "%SOURCE%"=="" set "SOURCE=docs"

set "NAMESPACE=%~2"
if "%NAMESPACE%"=="" set "NAMESPACE=cronpot-local"

set "LOCAL_PORT=%~3"
if "%LOCAL_PORT%"=="" set "LOCAL_PORT=18080"

kubectl cluster-info >nul 2>nul
if errorlevel 1 (
  echo Could not reach the Kubernetes API for the current kubectl context.
  exit /b 1
)

call "%~dp0k8s-deploy.cmd" local
if errorlevel 1 exit /b %ERRORLEVEL%

call "%~dp0k8s-seed-vault.cmd" "%SOURCE%" "%NAMESPACE%"
if errorlevel 1 exit /b %ERRORLEVEL%

powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Start-Process kubectl -ArgumentList @('-n','%NAMESPACE%','port-forward','service/cronpot-api','%LOCAL_PORT%:80') -WindowStyle Hidden -PassThru; try { Start-Sleep -Seconds 4; Invoke-RestMethod 'http://127.0.0.1:%LOCAL_PORT%/healthz' | Out-Null; Invoke-RestMethod 'http://127.0.0.1:%LOCAL_PORT%/readyz' | Out-Null; $a = Invoke-RestMethod 'http://127.0.0.1:%LOCAL_PORT%/analytics'; if ($a.recipe_count -lt 1) { throw 'Expected at least one recipe in analytics response.' }; Write-Host ('Kubernetes smoke passed with ' + $a.recipe_count + ' recipe(s).') } finally { if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force } }"
exit /b %ERRORLEVEL%
