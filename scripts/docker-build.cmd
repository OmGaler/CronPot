@echo off
setlocal

set "TAG=%~1"
if "%TAG%"=="" set "TAG=cronpot:local"

docker build -t "%TAG%" .
exit /b %ERRORLEVEL%
