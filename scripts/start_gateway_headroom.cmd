@echo off
title Model Gateway + Headroom plugin - port 8650
cd /d "%~dp0.."
echo ==========================================================
echo  Model Gateway + Headroom 选配插件 ^| port 8650
echo  dir: D:\AIcoding\model-gateway
echo  python: .venv-headroom\Scripts\python.exe（独立环境，与 miniconda base 隔离）
echo  未勾选 Headroom 的模型行为与原版完全一致；删除 .venv-headroom 后
echo  请改用 start_gateway_window.cmd（插件自动旁路，非必装）
echo  log: logs\gateway.log  (console output mirrored here)
echo ==========================================================
echo.
.venv-headroom\Scripts\python.exe -m app.main
echo.
echo ==========================================================
echo  Gateway process exited with code %ERRORLEVEL%
echo  Window kept open - press any key to close.
echo ==========================================================
pause >nul
