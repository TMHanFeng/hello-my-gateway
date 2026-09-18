@echo off
title Model Gateway DEV - port 8651 ONLY
rem 开发/测试专用：强制 8651 端口 + 仅监听 127.0.0.1，绝不触碰 8650 生产
cd /d "%~dp0"
set "MODEL_GATEWAY_PORT=8651"
set "MODEL_GATEWAY_HOST=127.0.0.1"
echo ==========================================================
echo  Model Gateway DEV  ^|  port 8651 (127.0.0.1 only)
echo  dir: %CD%
echo  生产 8650 不受影响；停用请勿关窗后运行 scripts\stop_dev_8651.ps1
echo ==========================================================
echo.
D:\miniconda\python.exe -m app.main
echo.
echo DEV instance exited with code %ERRORLEVEL%
pause >nul
