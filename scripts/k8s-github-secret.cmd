@echo off
setlocal

set "NAMESPACE=%~1"
if "%NAMESPACE%"=="" set "NAMESPACE=cronpot-local"

set "REPO=%~2"
set "BRANCH=%~3"
if "%BRANCH%"=="" set "BRANCH=main"

set "PATH_IN_REPO=%~4"
if "%PATH_IN_REPO%"=="" set "PATH_IN_REPO=."

set "AUTHOR_NAME=%~5"
if "%AUTHOR_NAME%"=="" set "AUTHOR_NAME=CronPot"

set "AUTHOR_EMAIL=%~6"
if "%AUTHOR_EMAIL%"=="" set "AUTHOR_EMAIL=cronpot@example.local"

if "%REPO%"=="" (
  echo Usage: scripts\k8s-github-secret.cmd NAMESPACE REPO_URL [BRANCH] [PATH_IN_REPO] [AUTHOR_NAME] [AUTHOR_EMAIL]
  echo Set CRONPOT_GITHUB_TOKEN before running. The vault must be separate from the CronPot project repository.
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0k8s-github-secret.ps1" -Namespace "%NAMESPACE%" -Repo "%REPO%" -Branch "%BRANCH%" -Path "%PATH_IN_REPO%" -AuthorName "%AUTHOR_NAME%" -AuthorEmail "%AUTHOR_EMAIL%"
exit /b %ERRORLEVEL%
