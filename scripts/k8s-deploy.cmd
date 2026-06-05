@echo off
setlocal

set "OVERLAY=%~1"
if "%OVERLAY%"=="" set "OVERLAY=local"

kubectl kustomize --load-restrictor=LoadRestrictionsNone "k8s/overlays/%OVERLAY%" | kubectl apply -f -
if errorlevel 1 exit /b %ERRORLEVEL%

if /I "%OVERLAY%"=="local" (
  kubectl -n cronpot-local rollout restart deployment/cronpot-api
  if errorlevel 1 exit /b %ERRORLEVEL%
  kubectl -n cronpot-local rollout status deployment/cronpot-api --timeout=180s
)
exit /b %ERRORLEVEL%
