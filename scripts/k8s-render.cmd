@echo off
setlocal

set "OVERLAY=%~1"
if "%OVERLAY%"=="" set "OVERLAY=local"

kubectl kustomize --load-restrictor=LoadRestrictionsNone "k8s/overlays/%OVERLAY%"
exit /b %ERRORLEVEL%
