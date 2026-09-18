#!/usr/bin/env bash
# Model Gateway Linux/macOS 启动入口（与 start_gateway.cmd 语义一致）：
# 位置自适应（脚本所在即仓库根）+ 解释器自动选择（.venv-headroom > venv > python3）+ 已运行自检。
# 生产 8650 仅由用户本人启动；开发/测试实例见 scripts/start_dev_8651.ps1 或 MODEL_GATEWAY_PORT=8651。
cd "$(dirname "$0")" || exit 1
PORT="${MODEL_GATEWAY_PORT:-8650}"
PY="python3"
if [ -x ".venv-headroom/bin/python" ]; then
  PY=".venv-headroom/bin/python"      # Headroom 压缩就绪的独立环境（未装则回退）
elif [ -x "venv/bin/python" ]; then
  PY="venv/bin/python"
fi
if curl -s -m 2 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
  echo "[Model Gateway] Already running on ${PORT}. Nothing to do."
  exit 0
fi
echo "=========================================================="
echo " Model Gateway v2.14.0  |  port ${PORT}"
echo " python : ${PY}"
echo " logs   : logs/gateway.log"
echo "=========================================================="
exec "$PY" -m app.main
