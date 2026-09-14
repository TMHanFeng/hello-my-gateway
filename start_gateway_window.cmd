@echo off
title Model Gateway - port 8650
cd /d D:\AIcoding\model-gateway
echo ==========================================================
echo  Model Gateway  ^|  port 8650  ^|  %DATE% %TIME%
echo  dir: D:\AIcoding\model-gateway
echo  log: logs\gateway.log  (console output mirrored here)
echo ==========================================================
echo.
D:\miniconda\python.exe main.py
echo.
echo ==========================================================
echo  Gateway process exited with code %ERRORLEVEL%
echo  Window kept open - press any key to close.
echo ==========================================================
pause >nul
