@echo off
setlocal

set "NAMESPACE=%~1"
if "%NAMESPACE%"=="" set "NAMESPACE=cronpot-local"

set "LOCAL_PORT=%~2"
if "%LOCAL_PORT%"=="" set "LOCAL_PORT=8080"

kubectl -n "%NAMESPACE%" port-forward service/cronpot-api "%LOCAL_PORT%:80"
exit /b %ERRORLEVEL%
