@echo off
setlocal enabledelayedexpansion

set "SOURCE=%~1"
if "%SOURCE%"=="" set "SOURCE=docs"

set "NAMESPACE=%~2"
if "%NAMESPACE%"=="" set "NAMESPACE=cronpot-local"

if not exist "%SOURCE%\" (
  echo Source vault folder does not exist: %SOURCE%
  exit /b 1
)

for /f "usebackq delims=" %%p in (`kubectl -n "%NAMESPACE%" get pod -l app.kubernetes.io/component^=api -o jsonpath^="{.items[?(@.status.phase=='Running')].metadata.name}"`) do set "POD=%%p"

if "%POD%"=="" (
  echo No running API pod found in namespace %NAMESPACE%.
  exit /b 1
)

for /f "tokens=1" %%p in ("%POD%") do set "POD=%%p"

kubectl -n "%NAMESPACE%" exec "%POD%" -- mkdir -p /vault
if errorlevel 1 exit /b %ERRORLEVEL%

kubectl -n "%NAMESPACE%" cp "%SOURCE%\." "%POD%:/vault" --container api
if errorlevel 1 exit /b %ERRORLEVEL%

kubectl -n "%NAMESPACE%" exec "%POD%" -- sh -c "find /vault -maxdepth 1 -name '*.md' | wc -l"
exit /b %ERRORLEVEL%
