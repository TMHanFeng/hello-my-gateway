#!/usr/bin/env bash
# Model Gateway 停止入口（Linux/macOS，与 scripts/stop_gateway.ps1 语义一致）：
# 按 8650 端口定位监听进程，并核验其命令行属于本仓库——绝不误停其他环境的 8650 实例。
cd "$(dirname "$0")" || exit 1
PORT="${MODEL_GATEWAY_PORT:-8650}"
if command -v fuser >/dev/null 2>&1; then
  PIDS="$(fuser "${PORT}/tcp" 2>/dev/null)"
else
  PIDS="$(ss -ltnp 2>/dev/null | grep ":${PORT} " | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u)"
fi
if [ -z "${PIDS//[[:space:]]/}" ]; then
  echo "Model Gateway not running (no listener on ${PORT})."
  exit 0
fi
for pid in $PIDS; do
  cmdline="$(ps -p "$pid" -o command= 2>/dev/null)"
  case "$cmdline" in
    *hello-my-gateway*|"$(pwd)"*) kill "$pid" && echo "Model Gateway (hello-my-gateway) stopped (PID $pid)." ;;
    "")
      echo "REFUSED: PID $pid on ${PORT} disappeared or is not owned by this user. Nothing was stopped."
      exit 1 ;;
    *)
      echo "REFUSED: PID $pid on ${PORT} does not belong to this repo:"
      echo "  $cmdline"
      echo "Nothing was stopped."
      exit 1 ;;
  esac
done
