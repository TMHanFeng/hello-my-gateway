@echo off
title Model Gateway v2.14.6 - port 8650
cd /d "%~dp0"
rem ==== auto pick interpreter: prefer .venv-headroom (Headroom compression ready), fallback miniconda ====
set "PY=D:\miniconda\python.exe"
if exist "%~dp0.venv-headroom\Scripts\python.exe" set "PY=%~dp0.venv-headroom\Scripts\python.exe"
rem ==== already running? skip ====
curl -s -m 2 http://127.0.0.1:8650/health >nul 2>&1
if %errorlevel%==0 (
  echo [Model Gateway] Already running on 8650. Nothing to do.
  pause
  exit /b 0
)
echo ==========================================================
echo  Model Gateway v2.14.6  ^|  port 8650
echo  python : %PY%
echo  logs   : logs\gateway.log
echo ==========================================================
echo.
"%PY%" -m app.main
echo.
echo Gateway exited (code %ERRORLEVEL%). Press any key to close.
pause >nul
