# -*- coding: utf-8 -*-
"""Headroom 上下文压缩插件（原 headroom_plugin.py 的插件化封装，dedicated 配置域）。

定位：对勾选了 headroom 的模型，在其真正接单时（pool.execute*_with_fallback 循环内）
压缩出站 messages，降低上游 token 消耗。未勾选的候选模型收到的仍是原文——同一池内
勾选/不勾选并存即天然 A/B 对照。

非必装三重保障：
  1. 库级：headroom-ai 只装在 .venv-headroom 独立环境（与 miniconda base 隔离）；
     本模块在 worker 线程内懒 import，未安装/导入失败 → 进程生命周期内永久旁路，
     网关在任何解释器下照常启动与转发。
  2. 配置级：config.json "headroom".enabled 总开关默认 false，关闭时只有
     一次 dict 取值 + 一次 bool 判断的开销；改动即时热生效（无需重启/reload）。
  3. 模型级：仅 ModelEntry.headroom=True 的候选触发压缩。

故障旁路：压缩超时/异常/结果回装失败一律返回原请求体，绝不 fail-closed；
dry_run 模式正常计算压缩但只记 headroom_stats、请求照发原文（灰度数据来源）。

插件化（v2.14.0）：dedicated 配置域——设置仍持久化于 config.json "headroom" 节点，
与既有 /admin/headroom 兼容别名共用同一套读写；启停/设置经插件中心 API 即时生效。
"""
import asyncio
import logging
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request

from app.admin.deps import verify_admin
from app.core.config import load_config, save_config
from app.plugins.base import GatewayPlugin

logger = logging.getLogger(__name__)

# worker 线程内首次成功 import 后缓存 compress 可调用；一旦失败永久旁路（装库需重启进程，符合预期）
_compress_fn = None
_import_failed = False
_ccr_patched = False
_import_lock = threading.Lock()


def _ensure_loaded():
    """懒加载 headroom 并关闭 CCR（幂等，可在任意线程调用）。

    import 与压缩分开计费：库首次加载在本机 >10s（litellm/onnx 导入链重），
    不能算进单次压缩超时——预热/首次调用无超时加载，压缩调用才有超时。"""
    global _compress_fn, _import_failed
    if _compress_fn is not None or _import_failed:
        return
    with _import_lock:
        if _compress_fn is not None or _import_failed:
            return
        try:
            from headroom import compress as _c
            _compress_fn = _c
            _disable_ccr()
            logger.info("[headroom] headroom-ai 库已加载，插件就绪（首次启用时懒加载）")
        except Exception as e:
            _import_failed = True
            logger.info(f"[headroom] headroom-ai 未安装或导入失败，插件旁路（非必装，属正常）: {e}")


def warmup_if_enabled():
    """启动时调用：总开关开启则后台线程预热 import + CCR 补丁，
    首笔请求不再承担库加载成本；未启用/未装库时静默（无 headroom 环境里探测失败即旁路）。"""
    def _warm():
        try:
            if headroom_cfg().get("enabled", False):
                _ensure_loaded()
                warm_kompress_if_enabled()
        except Exception:
            pass
    threading.Thread(target=_warm, daemon=True, name="headroom-warmup").start()


# Kompress 纯文本压缩的默认 ML 模型（ModernBERT 双token头；首次使用自动从 HuggingFace 下载权重）
KOMPRESS_DEFAULT_MODEL = "chopratejas/kompress-v2-base"


def ml_text_available() -> bool:
    """Kompress ML 依赖是否可用（torch 或 onnxruntime 二选一 + transformers）。
    find_spec 探测不触发导入（torch 导入链很重），供设置页显示可用性。"""
    try:
        from importlib.util import find_spec
        return find_spec("torch") is not None or find_spec("onnxruntime") is not None
    except Exception:
        return False


def warm_kompress_if_enabled():
    """kompress_model 启用时后台预热 ML 权重（daemon 线程，绝不阻塞调用方）。

    进程启动（warmup_if_enabled）与设置页保存开启时各调一次。下载/加载完成前，
    库对文本内容按"模型未就绪"透传原文，请求零影响；就绪后深压缩路径自动激活。"""
    def _warm():
        try:
            if not headroom_cfg().get("enabled", False):
                return
            _ensure_loaded()
            if _compress_fn is None:
                return
            model = str(headroom_cfg().get("kompress_model", "disabled"))
            if model == "disabled":
                return
            from headroom.transforms.kompress_compressor import (
                ensure_background_download,
                is_kompress_available,
            )
            if not is_kompress_available():
                logger.info("[headroom] Kompress ML 依赖缺失（需 torch 或 onnxruntime+transformers），"
                            "纯文本压缩不生效，仅规则压缩——依赖清单见插件目录内 requirements.txt")
                return
            ensure_background_download(model)
            logger.info(f"[headroom] Kompress ML 权重后台下载已触发 model={model}"
                        "（就绪前自然语言内容照常透传原文）")
        except Exception as e:
            logger.info(f"[headroom] Kompress 预热跳过（不影响转发）: {e}")
    threading.Thread(target=_warm, daemon=True, name="headroom-kompress-warm").start()


def _disable_ccr():
    """关闭 CCR（已在 headroom-ai 0.37.0 实包上差分验证）。

    compress() 的 **kwargs 只接受 CompressConfig 自身字段，ccr_enabled 会被静默忽略；
    真正的开关在全局单例管线的 ContentRouterConfig 里。默认的有损压缩路径会往压缩内容
    尾部注入 "Retrieve more: hash=…" 检索标记（实测：关/开差分确认），诱导模型调用
    headroom_retrieve 工具取回原文——中转站客户端没有该工具，必然落空。必须在首次
    压缩前预建单例并原位修补（config 为可变 dataclass，官方构造函数同样直接改它），
    SmartCrusher 懒构建时即读到关闭态。"""
    global _ccr_patched
    if _ccr_patched:
        return
    import importlib
    hc = importlib.import_module("headroom.compress")
    pipe = hc._get_pipeline()
    for t in (getattr(pipe, "transforms", None) or []):
        cfg = getattr(t, "config", None)
        if cfg is not None and hasattr(cfg, "ccr_enabled"):
            cfg.ccr_enabled = False
            cfg.ccr_inject_marker = False
    _ccr_patched = True
    logger.info("[headroom] CCR 已关闭（网关库模式无检索工具，标记注入一并禁用）")


def headroom_cfg() -> dict:
    """读 config.json "headroom" 节点（load_config 带 mtime 缓存，热路径零 IO）。"""
    h = load_config().get("headroom")
    return h if isinstance(h, dict) else {}


def import_failed() -> bool:
    return _import_failed


def lib_available() -> bool:
    """当前解释器环境是否装有 headroom-ai（find_spec 探测、不触发重导入，供设置页显示可用性）。
    True 不代表已启用，只代表环境具备；False 时开关打开也不产生压缩（自动旁路）。"""
    try:
        from importlib.util import find_spec
        return find_spec("headroom") is not None
    except Exception:
        return False


def _sync_compress(msgs: list, model_id: str, hcfg: dict):
    """同步压缩（跑在 to_thread 工作线程里，不阻塞事件循环）。调用前 _ensure_loaded 已就绪。"""
    if _compress_fn is None:
        _ensure_loaded()
        if _compress_fn is None:
            return None
    from headroom import CompressConfig
    tr = hcfg.get("target_ratio")
    cfg = CompressConfig(
        min_tokens_to_compress=int(hcfg.get("min_tokens_to_compress", 500)),
        protect_recent=int(hcfg.get("protect_recent", 4)),
        compress_user_messages=False,   # 初期只压工具输出等大块，用户消息保持原样
        compress_system_messages=bool(hcfg.get("compress_system_messages", False)),
        kompress_model=str(hcfg.get("kompress_model", "disabled")),  # disabled=纯规则压缩，不下载 ML 权重
        **({"target_ratio": float(tr)} if tr else {}),
    )
    return _compress_fn(msgs, model=model_id, model_limit=int(hcfg.get("model_limit", 200000)),
                        config=cfg)


async def compress_entry(entry, req, pool_name: str, caller: str, cache: dict):
    """池回退循环插点（插件中心 active_compressor 路由到此）：entry 接单时判定是否压缩。
    cache 为每次 execute 调用传入的 dict，同一请求的多个勾选候选复用一次压缩结果；
    压缩失败/不适用时本请求不再重试。"""
    if not getattr(entry, "headroom", False):
        return req                      # 模型未勾选：最快路径
    if cache.get("done"):
        return cache["req"]             # 本请求已改写：复用
    if cache.get("skip"):
        return req                      # 本请求已判定不适用/失败：不重试
    cache["skip"] = True                # 占位：以下任一分支不通过则本请求不再尝试

    hcfg = headroom_cfg()
    if not hcfg.get("enabled", False):
        return req
    mode = hcfg.get("mode", "live")
    if _import_failed:
        return req

    msgs = [m.model_dump(exclude_none=True) for m in req.messages]
    t0 = time.perf_counter()
    result = None
    error = ""
    try:
        # 库加载一次性完成（无超时，通常已被启动预热完成）：避免把 import 成本算进压缩超时
        await asyncio.to_thread(_ensure_loaded)
        if _compress_fn is None:
            return req                      # 未装库：旁路
        result = await asyncio.wait_for(
            asyncio.to_thread(_sync_compress, msgs, entry.id, hcfg),
            timeout=float(hcfg.get("timeout_seconds", 10)))
    except Exception as e:
        error = f"{type(e).__name__}: {str(e)[:200]}"
        logger.warning(f"[headroom] 压缩失败旁路 pool={pool_name} model={entry.id} caller={caller!r}: {error}")
    latency_ms = round((time.perf_counter() - t0) * 1000, 1)

    before = after = saved = 0
    ratio = 0.0
    transforms = ""
    if result is not None:
        before, after = result.tokens_before, result.tokens_after
        saved = result.tokens_saved
        ratio = round(float(result.compression_ratio), 4)
        transforms = ",".join(result.transforms_applied)[:500]
    try:
        from app.core.database import add_headroom_stats
        await add_headroom_stats(caller, pool_name, entry.id, mode, before, after,
                                 saved, ratio, transforms, latency_ms, error)
    except Exception:
        pass                            # 统计绝不影响转发

    if result is None or mode == "dry_run" or not saved:
        return req                      # dry_run 只记统计；零收益时不改写请求
    try:
        from app.core.models import ChatMessage
        req2 = req.model_copy(update={"messages": [ChatMessage(**m) for m in result.messages]})
    except Exception as e:
        logger.warning(f"[headroom] 压缩结果回装失败旁路 model={entry.id}: {type(e).__name__}: {e}")
        return req
    cache["done"] = True
    cache["req"] = req2
    logger.info(f"[headroom] 已压缩 pool={pool_name} model={entry.id} caller={caller!r} "
                f"{before}->{after} tok（省{saved}，压缩率{ratio:.0%}）{latency_ms}ms [{transforms}]")
    return req2


# ================= 设置读写与统计（插件自带 API，挂载于 /admin/plugins/headroom） =================

def settings_payload() -> dict:
    """Headroom 插件当前设置（设置页读取）。available=当前解释器是否装有 headroom-ai
    （find_spec 探测不导入；False 时开关打开也不产生压缩，自动旁路）。"""
    h = headroom_cfg()
    return {"enabled": bool(h.get("enabled", False)), "mode": h.get("mode", "live"),
            "min_tokens_to_compress": int(h.get("min_tokens_to_compress", 500)),
            "protect_recent": int(h.get("protect_recent", 4)),
            "timeout_seconds": int(h.get("timeout_seconds", 10)),
            "kompress_model": str(h.get("kompress_model", "disabled")),
            "kompress_enabled": str(h.get("kompress_model", "disabled")) != "disabled",
            "ml_available": ml_text_available(),
            "available": lib_available()}


def save_settings(body: dict) -> dict:
    """Headroom 插件设置保存：写 config.json headroom 节点并落盘。
    热生效（插件每请求经 load_config 读取，save_config 已刷新缓存），无需 /admin/reload。
    先校验后改：任一字段非法即整体拒绝，不产生半写。"""
    h = headroom_cfg()

    mode = str(body.get("mode", h.get("mode", "live"))).strip().lower()
    if mode not in ("live", "dry_run"):
        raise HTTPException(status_code=400, detail="mode 仅支持 live / dry_run")

    def _int_field(key: str, default: int, lo: int, hi: int) -> int:
        v = body.get(key, h.get(key, default))
        try:
            v = int(v)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"{key} 必须为整数")
        return max(lo, min(hi, v))

    h["enabled"] = bool(body.get("enabled", h.get("enabled", False)))
    h["mode"] = mode
    h["min_tokens_to_compress"] = _int_field("min_tokens_to_compress", 500, 0, 1_000_000)
    h["protect_recent"] = _int_field("protect_recent", 4, 0, 10_000)
    h["timeout_seconds"] = _int_field("timeout_seconds", 10, 1, 600)
    # 纯文本压缩开关：开启=ML 模型压自然语言（已有自定义模型 id 则保留），关闭=纯规则压缩
    kompress_enabled = bool(body.get("kompress_enabled", h.get("kompress_model", "disabled") != "disabled"))
    if kompress_enabled:
        cur = str(h.get("kompress_model", "disabled"))
        h["kompress_model"] = cur if cur != "disabled" else KOMPRESS_DEFAULT_MODEL
    else:
        h["kompress_model"] = "disabled"
    config = dict(load_config())
    config["headroom"] = h
    save_config(config)
    if kompress_enabled:
        warm_kompress_if_enabled()   # 后台触发权重下载，不阻塞保存请求
    return settings_payload()


async def stats_view(days: int = 7) -> dict:
    """Headroom 插件节省统计：近 N 天汇总/今日/按模型分布/最近 50 条明细，
    顺带回传总开关状态。dry_run 与 live 的记录都在（mode 字段区分，灰度决策数据源）。"""
    from app.core.database import get_headroom_summary
    data = await get_headroom_summary(days=max(1, min(int(days or 7), 90)))
    h = headroom_cfg()
    data["enabled"] = bool(h.get("enabled", False))
    data["mode"] = h.get("mode", "live")
    return data


# 插件自带 API 的独立命名空间：/admin/plugin_settings/<插件id>/*
# （不用 /admin/plugins/<id>，避免与插件中心 GET /admin/plugins/{pid} 详情路由撞路径）
router = APIRouter(prefix="/admin/plugin_settings/headroom", tags=["plugin:headroom"])


@router.get("")
@router.get("/")
async def plugin_settings_get(_=Depends(verify_admin)):
    return settings_payload()


@router.post("")
@router.post("/")
async def plugin_settings_set(request: Request, _=Depends(verify_admin)):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    return save_settings(body)


@router.get("/stats")
async def plugin_stats(days: int = 7, _=Depends(verify_admin)):
    return await stats_view(days)


# ================= 插件适配层 =================

class HeadroomPlugin(GatewayPlugin):
    """dedicated 配置域插件：设置持久化在 config.json "headroom" 节点（历史兼容）。"""

    @property
    def router(self):
        return router

    def is_enabled(self) -> bool:
        return bool(headroom_cfg().get("enabled", False))

    def set_enabled(self, flag: bool) -> None:
        # 插件中心总闸与设置页「启用插件」同一落点，语义完全一致
        save_settings({"enabled": bool(flag)})

    def on_startup(self, app) -> None:
        warmup_if_enabled()

    def active_compressor(self):
        # manager 只在 is_enabled 时询问；返回自身表示可提供 compress_entry hook
        return self

    async def compress_entry(self, entry, req, pool_name: str, caller: str, cache: dict):
        # pool.py 在 active_compressor() 的返回值上调用本 hook；实现委托给模块级同名函数
        # （无库旁路/dry_run/live 分支都在那里，类方法只做转发）
        return await compress_entry(entry, req, pool_name, caller, cache)

    def get_config(self) -> dict:
        return settings_payload()

    def set_config(self, cfg: dict) -> dict:
        return save_settings(cfg or {})


def create_plugin(manifest):
    return HeadroomPlugin(manifest)
