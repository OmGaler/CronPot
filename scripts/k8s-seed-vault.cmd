@echo off
setlocal enabledelayedexpansion

set "SOURCE=%~1"
if "%SOURCE%"=="" set "SOURCE=docs"

set "NAMESPACE=%~2"
if "%NAMESPACE%"=="" set "NAMESPACE=cronpot-local"

set "CLEAR=%~3"

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

if /i "%CLEAR%"=="/clear" (
  kubectl -n "%NAMESPACE%" exec "%POD%" -- sh -c "find /vault -mindepth 1 -maxdepth 1 -exec rm -rf {} +"
  if errorlevel 1 exit /b %ERRORLEVEL%
)

kubectl -n "%NAMESPACE%" exec "%POD%" -- mkdir -p /vault
if errorlevel 1 exit /b %ERRORLEVEL%

kubectl -n "%NAMESPACE%" cp "%SOURCE%\." "%POD%:/vault" --container api
if errorlevel 1 exit /b %ERRORLEVEL%

set "COUNT="
for /f "usebackq delims=" %%c in (`kubectl -n "%NAMESPACE%" exec "%POD%" -- sh -c "find /vault -maxdepth 1 -name '*.md' | wc -l"`) do set "COUNT=%%c"
if "%COUNT%"=="" exit /b 1
echo Seeded %COUNT% Markdown file(s) into %NAMESPACE%/%POD%:/vault
exit /b %ERRORLEVEL%
