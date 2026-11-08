@echo off
rem brain launcher (cmd / PowerShell) - finds node in .runtime\ or PATH and runs cli.js
setlocal
set "DIR=%~dp0"
set "ROOT=%DIR%.."

set "NODE="
for /d %%d in ("%ROOT%\.runtime\node-*") do set "NODE=%%d\node.exe"
if exist "%NODE%" goto :run
where node >nul 2>nul
if %errorlevel%==0 (
  set "NODE=node"
  goto :run
)
echo brain: no node found ^(looked in .runtime\node-* and PATH^) 1>&2
exit /b 1

:run
"%NODE%" "%DIR%cli.js" %*