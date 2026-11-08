@echo off
setlocal

rem Double-click launcher for Windows: starts this brain and opens its homepage.
rem
rem The port is deliberately NOT worked out here. An earlier version asked node for
rem it inside `for /f`, which cmd mangles in at least two ways (the backtick form
rem fails outright, and a quoted executable inside single quotes trips its quote
rem stripping). Both failures were silent: the launcher printed no error, fell back
rem to 8765, and opened the wrong URL for any brain on another port. So `serve --open`
rem resolves the port and launches the browser itself, where it already knows the
rem URL it actually bound.

set "BRAIN_DIR=%~dp0."
set "NODE="

rem Try a runtime next to the brain first, then fall back to PATH.
for %%P in ("%BRAIN_DIR%\..\.runtime\node-*\node.exe") do (
    if not defined NODE set "NODE=%%P"
)
if not defined NODE (
    for %%P in ("%BRAIN_DIR%\.runtime\node-*\node.exe") do (
        if not defined NODE set "NODE=%%P"
    )
)
if not defined NODE (
    where node >nul 2>nul && set "NODE=node"
)

if not defined NODE (
    echo brain: no node found ^(looked in .runtime\node-* and PATH^)
    pause
    exit /b 1
)

cd /d "%BRAIN_DIR%"

echo.
echo Starting the brain. It will open in your browser.
echo Close this window or press Ctrl+C to stop.
echo.

"%NODE%" cli.js serve --open
