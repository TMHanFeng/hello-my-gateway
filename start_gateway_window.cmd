@echo off
title Model Gateway - port 8650
cd /d "%~dp0"
echo ==========================================================
echo  Model Gateway  ^|  port 8650  ^|  %DATE% %TIME%
echo  dir: D:\AIcoding\model-gateway
echo  log: logs\gateway.log  (console output mirrored here)
echo ==========================================================
echo.
D:\miniconda\python.exe -m app.main
echo.
echo ==========================================================
echo  Gateway process exited with code %ERRORLEVEL%
echo  Window kept open - press any key to close.
echo ==========================================================
pause >nul
