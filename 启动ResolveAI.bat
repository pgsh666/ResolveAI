@echo off
setlocal
title ResolveAI - One-click startup
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" %*
set "resolveai_exit_code=%errorlevel%"
echo.
if not "%resolveai_exit_code%"=="0" echo Startup failed. Read the message above before closing this window.
pause
exit /b %resolveai_exit_code%
