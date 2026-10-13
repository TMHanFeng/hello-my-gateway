# -*- coding: utf-8 -*-
"""余量刷新独立服务端口：局域网外部服务触发余量刷新 / 读取结果文件。

与主网关(8650)分开监听一个专用端口（config quota_monitor.port，默认 8655，host 默认
0.0.0.0 供局域网访问），只暴露三个只读+触发型端点，不挂任何面板/模型/配置能力——
防火墙只需放通这一个端口，不扩大主网关攻击面：

  POST/GET /refresh  触发刷新（默认全部启用绑定，?binding_id= 单条），返回刷新后的
                     全量结果（即 cheackresult.json 内容）；?async=1 受理即返回，
                     外部轮询 /result 直到 generated_at 变化。
  GET      /result   直接读取当前 cheackresult.json（不触发刷新）。
  GET      /health   健康检查。

鉴权：config quota_monitor.token 非空时要求 ?token= 或 Authorization: Bearer（默认为空不鉴权）。
防抖：3 秒内的连续 /refresh 直接返回最近一次结果，不重复打上游。

进程内与主网关共用同一事件循环（uvicorn.Server 以任务方式拉起，关闭信号处理交由
主服务的 uvicorn 实例，本实例 no-op，避免覆盖 Ctrl+C 处理）。
"""
import asyncio
import logging
import socket
import time

import uvicorn
from fastapi import FastAPI, HTTPException, Request

from app.core.config import load_config
from app.core.quota_providers import read_result_document, refresh_all, refresh_binding, \
    write_result_file

logger = logging.getLogger(__name__)

_DEBOUNCE_SECONDS = 3.0
_refresh_lock = asyncio.Lock()
_last_refresh_at = 0.0

app = FastAPI(title="Gateway Quota Refresh", docs_url=None, redoc_url=None,
              openapi_url=None)


def server_settings() -> dict:
    cfg = load_config().get("quota_monitor") or {}
    try:
        port = int(cfg.get("port", 8655) or 8655)
    except (TypeError, ValueError):
        port = 8655
    return {"enabled": bool(cfg.get("server_enabled", True)),
            "host": cfg.get("host") or "0.0.0.0",
            "port": port, "token": str(cfg.get("token") or "")}


def _check_token(request: Request) -> None:
    token = server_settings()["token"]
    if not token:
        return
    given = request.query_params.get("token") or ""
    if not given:
        auth = request.headers.get("Authorization", "")
        given = auth[7:].strip() if auth.startswith("Bearer ") else ""
    if given != token:
        raise HTTPException(status_code=401, detail="Invalid token")


async def _do_refresh(binding_id: int | None = None) -> dict:
    """串行化刷新 + 3s 防抖：拿不到即等（前一次刷新的结果就是最新数据）。"""
    global _last_refresh_at
    async with _refresh_lock:
        if binding_id is None and time.time() - _last_refresh_at < _DEBOUNCE_SECONDS:
            return await read_result_document()
        if binding_id is None:
            await refresh_all(enabled_only=True)
        else:
            from app.core import database as db
            binding = await db.get_quota_binding(binding_id)
            if not binding:
                raise HTTPException(status_code=404, detail=f"绑定 #{binding_id} 不存在")
            await refresh_binding(binding)
            await write_result_file()
        _last_refresh_at = time.time()
        return await read_result_document()


@app.get("/health")
async def health():
    return {"ok": True, "service": "quota-refresh"}


@app.post("/refresh")
@app.get("/refresh")
async def refresh(request: Request, binding_id: int | None = None, async_mode: str = ""):
    _check_token(request)
    if async_mode in ("1", "true", "yes"):
        asyncio.create_task(_safe_refresh(binding_id))
        return {"ok": True, "started": True,
                "hint": f"轮询 /result 直到 generated_at 大于 {int(time.time())}"}
    return await _do_refresh(binding_id)


async def _safe_refresh(binding_id: int | None):
    try:
        await _do_refresh(binding_id)
    except Exception:
        logger.exception("[余量刷新端口] 异步刷新失败")


@app.get("/result")
async def result(request: Request):
    _check_token(request)
    return await read_result_document()


class _NoSignalServer(uvicorn.Server):
    def install_signal_handlers(self):
        pass  # 信号处理归主网关 uvicorn 实例，避免二次安装覆盖 Ctrl+C 行为


_server: _NoSignalServer | None = None
_task: asyncio.Task | None = None


async def start():
    """lifespan 启动钩子：按 config 拉起独立端口（server_enabled=false 或端口占用时降级为仅主网关）。"""
    global _server, _task
    s = server_settings()
    if not s["enabled"]:
        logger.info("[余量刷新端口] 已停用（quota_monitor.server_enabled=false）")
        return
    # 先探测端口可用性再交给 uvicorn：端口被占（如同机多实例/生产已占用）时优雅降级——
    # 不做这一步，uvicorn startup 失败会 sys.exit(STARTUP_FAILURE)，把整个 lifespan 拖崩
    # （v2.22.5 教训：生产切到 v2.22.5 占住 8655 后，隔离实例/第二实例启动即崩）
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((s["host"] if s["host"] not in ("0.0.0.0", "::") else "", s["port"]))
    except OSError:
        logger.warning(f"[余量刷新端口] 端口 {s['port']} 已被占用，降级为仅主网关（不影响余量轮询与落盘）")
        return
    finally:
        probe.close()
    _server = _NoSignalServer(uvicorn.Config(app, host=s["host"], port=s["port"],
                                             log_level="warning"))
    _task = asyncio.create_task(_server.serve())
    def _serve_done(t: asyncio.Task):
        """兜底：子 uvicorn 任务若异常/SystemExit 退出，只记日志，绝不传染主网关 lifespan。"""
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.warning(f"[余量刷新端口] 子服务退出：{exc!r}")
    _task.add_done_callback(_serve_done)
    # 等 serve() 完成端口绑定（started）或失败（should_exit），最多 2s
    for _ in range(40):
        if _server.started or _server.should_exit:
            break
        await asyncio.sleep(0.05)
    if not _server.started:
        logger.warning(f"[余量刷新端口] 启动失败（端口 {s['port']} 可能被占用）")
        _server = None
        return
    logger.info(f"[余量刷新端口] http://{s['host']}:{s['port']}  "
                f"(POST /refresh 触发刷新 · GET /result 读结果"
                f"{' · 需 token' if s['token'] else ''})")


async def stop():
    global _server, _task
    if _server is not None:
        _server.should_exit = True
        if _task is not None:
            try:
                await asyncio.wait_for(_task, timeout=5)
            except Exception:
                _task.cancel()
    _server, _task = None, None
