from fastapi import APIRouter, HTTPException, Depends, Request, Response
from fastapi.responses import HTMLResponse
from pathlib import Path
import asyncio
import logging
import subprocess
import threading
from app.core.config import load_config, save_config
from app.core import database as db
from app.core.scheduler import restart_scheduler
from app.providers.openai_provider import OpenAIProvider
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.qianfan_search import QianfanSearchProvider
from app.gateway import search_summary

router = APIRouter(prefix="/admin")

from app.core.paths import FRONTEND_PATH, PROJECT_ROOT as REPO_DIR  # 统一路径常量（仓库根 static/index.html）


def _read_git_version() -> tuple[str, str]:
    version = ""
    commit = ""
    try:
        r = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            capture_output=True, text=True, timeout=5, cwd=str(REPO_DIR),
        )
        if r.returncode == 0:
            version = r.stdout.strip()
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=str(REPO_DIR),
        )
        if r.returncode == 0:
            commit = r.stdout.strip()
    except Exception:
        pass
    return version, commit


# 进程启动时缓存一次，反映"真正运行的代码"版本（避免 git pull 后磁盘已更新、
# 但进程仍是旧代码时误报新版本）
GATEWAY_VERSION, GATEWAY_COMMIT = _read_git_version()


def get_gateway_version() -> str:
    """获取网关版本（进程启动时缓存的版本 + commit），失败回退 unknown"""
    if GATEWAY_VERSION and GATEWAY_COMMIT:
        return f"{GATEWAY_VERSION} · {GATEWAY_COMMIT}"
    return GATEWAY_VERSION or GATEWAY_COMMIT or "unknown"


def _sync_pool():
    """Reload in-memory pool after config changes so /v1/models stays current."""
    from app.main import pool
    pool.reload()


def _expand_pool_members(config: dict, pool_name: str, visiting: set | None = None) -> tuple[list, list]:
    """按 config 递归展开池的全部模型成员（支持 pool: 子池引用）。

    返回 (models, broken)：models 为可直接解析的模型 id；broken 为引用了不存在池的断裂引用。"""
    visiting = visiting or set()
    if pool_name in visiting:
        return [], []
    visiting = visiting | {pool_name}
    models, broken = [], []
    for raw in (config.get("pools", {}).get(pool_name, {}) or {}).get("model_ids", []):
        if isinstance(raw, str) and raw.startswith("pool:"):
            if raw[5:] in config.get("pools", {}):
                sub_models, sub_broken = _expand_pool_members(config, raw[5:], visiting)
                models.extend(sub_models)
                broken.extend(sub_broken)
            else:
                broken.append(raw)
        elif raw:
            models.append(raw)
    return models, broken


# verify_admin 已上移 app/admin/deps.py（admin 路由与插件中心路由共用同一套 Bearer 校验）
from app.admin.deps import verify_admin  # noqa: E402,F401


# 面板页面缓存（v2.11.42）：148KB read_text 是同步阻塞 IO，原先每次刷新都在事件循环内全量重读；
# 改 mtime 缓存——文件未变时零 IO，手工改 HTML 后自动失效（no-cache 响应头语义不变）
_admin_html_cache = {"mtime": None, "html": "", "etag": ""}


@router.get("/", response_class=HTMLResponse)
async def admin_page(request: Request):
    try:
        mtime = FRONTEND_PATH.stat().st_mtime
    except OSError:
        mtime = None
    if _admin_html_cache["html"] == "" or mtime != _admin_html_cache["mtime"]:
        import hashlib
        _admin_html_cache["html"] = FRONTEND_PATH.read_text(encoding="utf-8")
        _admin_html_cache["mtime"] = mtime
        _admin_html_cache["etag"] = '"' + hashlib.md5(
            _admin_html_cache["html"].replace("__GATEWAY_VERSION__", get_gateway_version()).encode("utf-8")
        ).hexdigest()[:20] + '"'
    # no-cache：每次再验证但内容未变走 304 零传输（#47，同 /hfadmin 的 ETag 协商）
    etag = _admin_html_cache["etag"]
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
    return HTMLResponse(content=_admin_html_cache["html"].replace("__GATEWAY_VERSION__", get_gateway_version()),
                        headers={"Cache-Control": "no-cache", "ETag": etag})


@router.get("/models")
async def get_models(_=Depends(verify_admin)):
    config = load_config()
    providers_by_id = {p["id"]: p for p in config.get("providers", [])}
    models = []
    for m in config.get("models", []):
        m2 = dict(m)
        m2["gift_refund"] = (m.get("token_type") == "gift")
        if "provider" not in m2:  # 供应商制模型回填解析后的协议：面板模型行徽章按协议显示
            prov = providers_by_id.get(m.get("provider_id", ""))
            m2["provider"] = (prov or {}).get("protocol") or "openai"
        models.append(m2)
    return {"models": models}


# 思考参数探测辅助：查询 / 强制重测（探测结论只进缓存，reasoning_map 是否采用由用户在编辑框决定）

_reasoning_probe_inflight: set = set()


def _probe_key_of(m: dict, providers: dict, protocol_default: str = "openai") -> str:
    """与 probe_reasoning 缓存键同源：协议|base_url|上游模型名"""
    pid = m.get("provider_id", "")
    prov = providers.get(pid) or {}
    protocol = prov.get("protocol") or m.get("provider", protocol_default)
    base_url = (prov.get("base_url") or m.get("base_url", "")).rstrip("/")
    return f"{protocol}|{base_url}|{m.get('name', '')}"


@router.get("/reasoning")
async def get_reasoning(_=Depends(verify_admin)):
    """思考参数查询（纯本地读取，不发上游请求）：每个模型的探测结论 + 当前生效映射 +
    六档实际注入片段。供编辑弹窗辅助填空与调试查询。"""
    from app.core import reasoning as _reasoning
    from app.tools import probe_reasoning
    config = load_config()
    providers_by_id = {p["id"]: p for p in config.get("providers", [])}
    pools_cfg = config.get("pools", {})
    cache = probe_reasoning.load_cache()
    levels = list(_reasoning.LEVELS)
    models: dict = {}
    with_map = 0
    for m in config.get("models", []):
        if m.get("modality") in ("embedding", "rerank"):
            continue
        mid = m.get("id", "")
        key = _probe_key_of(m, providers_by_id)
        rec = cache.get(key) or {}
        rmap = m.get("reasoning_map") or {}
        if rmap:
            with_map += 1
        if not rmap:
            off_behavior = "无映射：reasoning_effort 被忽略（模型走上游默认行为）"
        elif "off" in rmap:
            off_behavior = "可真正关闭思考"
        else:
            off_behavior = "请求 off 时回落到最低配置档"
        models[mid] = {
            "probed": bool(rec),
            "probe_time": rec.get("ts") or None,
            "param_family": rec.get("family") or None,
            "default_thinking": rec.get("default_thinking") or None,
            "levels_effective": bool(rec.get("levels_effective")),
            "notes": rec.get("notes") or [],
            "suggested": rec.get("suggested") or None,
            "current_map": rmap,
            "configured_levels": [lv for lv in levels if lv in rmap],
            "effective": {lv: _reasoning.resolve_fragment(rmap, lv) for lv in levels},
            "off_behavior": off_behavior,
            "upstream_name": m.get("name", ""),
            "protocol": (providers_by_id.get(m.get("provider_id", "")) or {}).get("protocol") or m.get("provider", "openai"),
            "pools": sorted(p for p, pc in pools_cfg.items() if mid in (pc.get("model_ids") or [])),
        }
    return {
        "default_reasoning_effort": config.get("default_reasoning_effort", "low"),
        "levels": levels,
        "summary": {"models": len(models), "with_map": with_map, "without_map": len(models) - with_map},
        "models": models,
    }


@router.post("/gift/calibrate")
async def gift_calibrate(request: Request, _=Depends(verify_admin)):
    """余额返还制人工校准：单独修正 昨日剩余量 / 今日返还量(=昨日使用量) / 今日使用量（未提供项保持现值）。

    校准值同步改写对应自然日统计（人工校准优先于系统统计，自校准时刻起持续生效，不随跨日重算消失）。
    时间感知：
    - 到账时刻（refresh_time，如 14:00）前校准：今日返还未到账，不计入余额（余额 = 昨日剩余 - 今日使用）；
      昨日自然日消耗改写为校准值，14:00 到账时按其发放。
    - 到账时刻后校准：余额 = 昨日剩余 + 今日返还 - 今日使用。
    同步失效该模型的配额预检缓存。"""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    body = await request.json()
    model_id = body.get("model_id")
    config = load_config()
    m = next((x for x in config.get("models", []) if x.get("id") == model_id), None)
    if not m or m.get("token_type") != "gift":
        raise HTTPException(status_code=404, detail="模型不存在或非余额返还制")

    cap = int(m.get("daily_token_limit", 0) or 0)
    grant_cap = int(m.get("gift_grant_cap", 0) or 0) or 5_000_000
    rt = m.get("refresh_time", "")

    # 先走一次读路径（惰性补账到当前时点），保证账本为最新
    await db.get_gift_balance(model_id, cap, rt, grant_cap)
    state = await db.get_gift_state(model_id)
    if not state:
        raise HTTPException(status_code=404, detail="账本尚未初始化（该模型还没有调用记录）")

    now_dt = datetime.now(ZoneInfo("Asia/Shanghai"))
    today = now_dt.date().isoformat()
    yday = (now_dt.date() - timedelta(days=1)).isoformat()
    try:
        hh, mm = (int(x) for x in (rt or "00:00").split(":"))
    except ValueError:
        hh, mm = 0, 0
    after_grant = (now_dt.hour, now_dt.minute) >= (hh, mm)

    st = await db.get_model_daily_stats(model_id)
    u_cur = int(st.get("total_tokens", 0) or 0)
    g_cur = int(state.get("last_grant_amount", 0) or 0)
    y_cur = max(0, int(state.get("yesterday_leftover", 0) or 0))

    y = body.get("yesterday_leftover")
    g = body.get("grant_today")
    u = body.get("usage_today")
    y_new = int(y) if y is not None else y_cur
    g_new = int(g) if g is not None else g_cur
    u_new = int(u) if u is not None else u_cur
    for name, v in (("yesterday_leftover", y_new), ("grant_today", g_new), ("usage_today", u_new)):
        if v < 0:
            raise HTTPException(status_code=400, detail=f"{name} 不能为负数")

    if g is not None:
        # 校准昨日使用量 → 改写昨日自然日统计（ⓘ 展示与到账时刻的发放基数同步）
        await db.set_model_daily_usage(model_id, g_new, yday)
    if u is not None:
        # 校准今日使用量 → 同步改写今日自然日统计（ⓘ 今日已采、明日发放基数、上限预检同源）
        await db.set_model_daily_usage(model_id, u_new, today)

    # 到账时刻前：今日返还未到账不计入余额；到账后：余额 = 昨日剩余 + 今日返还 - 今日使用
    if after_grant:
        new_balance = max(0, y_new + g_new - u_new)
    else:
        new_balance = max(0, y_new - u_new)
    # last_grant_amount 始终写校准值：14:00 前校准的返还量在弹窗注记/账本中即刻可见，
    # 到账时刻 get_gift_balance 按昨日统计（已改写为同一数值）发放，两处自然一致
    await db.set_gift_state(model_id, new_balance, y_new + (g_new if after_grant else 0),
                            g_new, today, y_new,
                            today if after_grant else None, today)
    try:
        from app.main import pool as _pool
        _pool._invalidate_quota_cache(model_id)
    except Exception:
        pass
    return {"model_id": model_id, "yesterday_leftover": y_new, "grant_today": g_new,
            "usage_today": u_new, "balance": new_balance, "grant_applied": bool(after_grant)}


@router.post("/reasoning/probe")
async def probe_reasoning_api(request: Request, _=Depends(verify_admin)):
    """强制重测单个模型的思考参数（后台执行，结果只写探测缓存，不改 reasoning_map）。"""
    from app.tools import probe_reasoning
    body = await request.json()
    model_id = (body.get("model_id") or "").strip()
    if not model_id:
        raise HTTPException(status_code=400, detail="Missing field: model_id")
    config = load_config()
    model = next((m for m in config.get("models", []) if m.get("id") == model_id), None)
    if not model:
        raise HTTPException(status_code=404, detail=f"模型 '{model_id}' 不存在")
    if model.get("modality") in ("embedding", "rerank"):
        raise HTTPException(status_code=400, detail="embedding/rerank 模型不参与思考控制")
    if model_id in _reasoning_probe_inflight:
        raise HTTPException(status_code=409, detail="该模型的探测已在进行中")
    providers = config.get("providers", [])
    if not probe_reasoning.build_targets({"providers": providers, "models": [model]}):
        raise HTTPException(status_code=400, detail="模型缺少连接信息（base_url/api_key），无法探测")
    _reasoning_probe_inflight.add(model_id)
    probe_reasoning.register_main_loop(asyncio.get_running_loop())  # 探测线程的用量记账投回主循环执行

    def _run():
        try:
            probe_reasoning.probe_single(model, providers, force=True)
            logging.getLogger(__name__).info(f"[思考探测] 模型 {model_id} 强制重测完成（结论已写入探测缓存）")
        except Exception:
            logging.getLogger(__name__).exception(f"[思考探测] 模型 {model_id} 强制重测失败")
        finally:
            _reasoning_probe_inflight.discard(model_id)

    threading.Thread(target=_run, daemon=True).start()
    return {"queued": True, "model_id": model_id}


@router.post("/models")
async def add_model(request: Request, _=Depends(verify_admin)):
    body = await request.json()
    pid = (body.get("provider_id") or "").strip()

    config = load_config()
    providers = config.get("providers", [])

    if pid:
        # provider-based model: protocol/base_url/api_key inherited from provider
        if not body.get("id") or not body.get("name"):
            raise HTTPException(status_code=400, detail="Missing field: id/name")
        if not any(p["id"] == pid for p in providers):
            raise HTTPException(status_code=400, detail=f"Provider '{pid}' not found")
    else:
        # legacy inline model: full connection info required
        for field in ["id", "name", "provider", "base_url", "api_key"]:
            if not body.get(field):
                raise HTTPException(status_code=400, detail=f"Missing field: {field}")

    models = config.get("models", [])
    raw_id = (body.get("id") or "").strip()
    if not raw_id:
        raise HTTPException(status_code=400, detail="Missing field: id")
    if "/" in raw_id or "\\" in raw_id:
        raise HTTPException(status_code=400, detail="模型ID中不能包含 / 或 \\ 字符")

    # When adding under a provider, prefix the model ID with provider name
    final_id = f"{pid}/{raw_id}" if pid else raw_id
    for m in models:
        if m["id"] == final_id:
            raise HTTPException(status_code=409, detail=f"模型ID '{final_id}' 已存在")

    entry = {
        "id": final_id,
        "name": body["name"],
        "daily_token_limit": body.get("daily_token_limit", 0),
        "rpm_limit": body.get("rpm_limit", 0),
        "tpm_limit": body.get("tpm_limit", 0),
        "token_type": body.get("token_type", "daily"),
        "refresh_time": body.get("refresh_time", ""),
        "timezone": "Asia/Shanghai",
        "context_window": body.get("context_window", 0),
        "max_concurrency": body.get("max_concurrency", 0),
        "timeout_seconds": (None if body.get("timeout_seconds") in (None, "") else int(body["timeout_seconds"])),
        "billing_mode": body.get("billing_mode", "token"),
        "is_free": bool(body.get("is_free", True)),
        "modality": body.get("modality", "text"),
        "json_output": bool(body.get("json_output", False)),
        "extra_params": body.get("extra_params") or {},
        "reasoning_map": body.get("reasoning_map") or {},
        "smart_estimate": bool(body.get("smart_estimate", False)),
        "no_stream_options": bool(body.get("no_stream_options", False)),
        # 缓存计费统计：勾选后按小时桶记录缓存命中/未命中/输出 token（价格在用量统计「预估费用」弹窗配置）
        "cost_enabled": bool(body.get("cost_enabled", False)),
        # v2.15.0 付费模型·高峰时段（token_type=idle_only 用作闲时判定）：与缓存计费高峰期同源共享
        **({"cost_peak": body["cost_peak"]} if isinstance(body.get("cost_peak"), dict) else {}),
        # v2.13.0 Headroom 选配插件：勾选模型接单时压缩出站 messages（默认 False=不参与）
        "headroom": bool(body.get("headroom", False)),
        # v2.12.3 搜索 AI 总结（仅 qianfan_web_search 协议在面板有 UI 入口）
        "summary_pool": (body.get("summary_pool") or "").strip(),
        "summary_length": str(body.get("summary_length") or "").strip(),
    }
    try:
        entry["valve_pct"] = max(0, min(100, int(body.get("valve_pct", 100))))  # 使用量安全阀 k（%）
    except (TypeError, ValueError):
        entry["valve_pct"] = 100
    if pid:
        entry["provider_id"] = pid
    else:
        entry["provider"] = body["provider"]
        entry["base_url"] = body["base_url"]
        entry["api_key"] = body["api_key"]
    # one-time fields: save whenever any is provided, and auto-switch to one_time
    max_tokens_raw = body.get("max_tokens", 0)
    ttl_raw = body.get("ttl_seconds", 0)
    exp_raw = body.get("expire_date", "")
    has_once_fields = (
        max_tokens_raw not in (None, "", 0)
        or ttl_raw not in (None, "", 0)
        or bool(exp_raw)
    )
    if body.get("token_type") == "one_time" or has_once_fields:
        entry["token_type"] = "one_time"
        entry["max_tokens"] = int(max_tokens_raw or 0)
        entry["ttl_seconds"] = int(ttl_raw or 0)
        if exp_raw:
            entry["expire_date"] = exp_raw

    # v2.12.3 搜索 AI 总结：只有 qianfan_web_search 协议保留这两个键；其余显式剥离避免残留。
    # 校验放在落盘之前（沿用本文件"先校验后改"的既有约定）。
    _proto_new = ((next((p for p in config.get("providers", []) if p.get("id") == pid), None) or {})
                  .get("protocol") if pid else body.get("provider", "openai")) or "openai"
    if _proto_new == "qianfan_web_search":
        if entry.get("summary_pool"):
            from app.main import pool as _pool
            _why = search_summary.pool_ineligible_reason(_pool, entry["summary_pool"])
            if _why:
                raise HTTPException(status_code=400, detail=_why)
    else:
        entry.pop("summary_pool", None)
        entry.pop("summary_length", None)

    models.append(entry)
    config["models"] = models
    save_config(config)
    restart_scheduler()
    _sync_pool()  # #46A：模型新增热生效（与其他改配置端点对齐，前端不再依赖补调 /admin/reload）

    # v2.15.3 起新增模型不再自动探测思考档位（含缓存自动套用）：
    # 探测只在面板「重新探测」按钮人工触发（POST /admin/reasoning/probe），结论只进缓存、
    # reasoning_map 是否采用仍由用户在编辑框决定（🧠 探测条可一键填入）。

    return {"ok": True, "model": entry}


@router.get("/model/{model_id:path}/load")
async def get_model_load(model_id: str, _=Depends(verify_admin)):
    """v2.11.44：查询单个模型（而非模型池）的当前并发负载。

    active = 正在上游处理中（持有 max_concurrency 槽位）；waiting = 排队等槽。
    max_concurrency=0（不限）时 unlimited=True，active/waiting 照常给出。"""
    from app.main import pool as _pool
    entry = _pool.registry.get(model_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
    return {
        "model_id": model_id,
        "max_concurrency": entry.max_concurrency,
        "unlimited": entry.max_concurrency <= 0,
        "active": entry.active_requests,
        "waiting": entry.waiting_requests,
    }


@router.get("/model/{model_id:path}/metrics")
async def get_model_metrics_api(model_id: str, _=Depends(verify_admin)):
    """问题22：返回该模型实测校准数据（吞吐 tok/s、平均输出 token、样本数），供前端 live 区展示。"""
    from app.gateway.pool import ModelPool  # 复用运行中实例的 EMA（含未落盘的增量）
    config = load_config()
    entry = next((m for m in config.get("models", []) if m.get("id") == model_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
    try:
        agg = await db.get_model_metrics(model_id)
    except Exception:
        agg = {"sample_count": 0, "throughput": None, "avg_completion": None}
    live = None
    try:
        from app.main import pool as _pool
        e = _pool.registry.get(model_id)
        if e is not None:
            live = {"throughput": e.throughput_ema, "avg_completion": e.avg_completion_ema,
                    "sample_count": e.sample_count, "smart_estimate": e.smart_estimate}
    except Exception:
        pass
    return {
        "smart_estimate": bool(entry.get("smart_estimate", False)),
        "sample_count": (live or {}).get("sample_count") or agg.get("sample_count") or 0,
        "throughput": (live or {}).get("throughput") or agg.get("throughput"),
        "avg_completion": (live or {}).get("avg_completion") or agg.get("avg_completion"),
    }


@router.get("/model/{model_id:path}/cost")
async def get_model_cost(model_id: str, hours: int = 24 * 30, _=Depends(verify_admin)):
    """缓存计费预估：返回该模型的小时桶聚合（缓存命中/未命中/输出 token）+ 已配置价格。
    费用分段（高峰/平峰）由前端按小时桶与当前价格实时计算——价格改动即可对历史重算。"""
    config = load_config()
    entry = next((m for m in config.get("models", []) if m.get("id") == model_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
    try:
        hours = max(1, min(24 * 366, int(hours)))
    except (TypeError, ValueError):
        hours = 24 * 30
    rows = await db.get_cache_stats(model_id, hours)
    return {
        "enabled": bool(entry.get("cost_enabled", False)),
        # 价格结构（¥/百万 token）：{hit, miss, out}；peak 为 {enabled, windows, hit, miss, out}，
        # windows = 自适应解析的多段高峰时段串（分号分隔，如 "9:00-12:00;14:00-18:00"，段内支持跨零点）
        "prices": entry.get("cost_prices") or {"hit": 0, "miss": 0, "out": 0},
        "peak": entry.get("cost_peak") or {"enabled": False, "windows": "09:00-21:00", "hit": 0, "miss": 0, "out": 0},
        "hours": rows,
    }


@router.put("/models/reorder")
async def reorder_models(request: Request, _=Depends(verify_admin)):
    """Reorder models. Body: {model_ids: [...]} must contain exactly all existing model IDs."""
    body = await request.json()
    new_order = body.get("model_ids", [])
    if not isinstance(new_order, list):
        raise HTTPException(status_code=400, detail="model_ids must be a list")

    config = load_config()
    models = config.get("models", [])
    existing_ids = {m["id"] for m in models}
    if set(new_order) != existing_ids:
        missing = existing_ids - set(new_order)
        extra = set(new_order) - existing_ids
        raise HTTPException(
            status_code=400,
            detail=f"model_ids must contain exactly all existing model IDs. Missing: {sorted(missing)}, Extra: {sorted(extra)}",
        )

    by_id = {m["id"]: m for m in models}
    config["models"] = [by_id[mid] for mid in new_order]
    save_config(config)
    restart_scheduler()
    _sync_pool()
    return {"ok": True}

@router.put("/models/{model_id:path}")
async def update_model(model_id: str, request: Request, _=Depends(verify_admin)):
    body = await request.json()
    config = load_config()
    models = config.get("models", [])

    idx = None
    for i, m in enumerate(models):
        if m["id"] == model_id:
            idx = i
            break
    if idx is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")

    pid = body.get("provider_id")
    if pid and not any(p["id"] == pid for p in config.get("providers", [])):
        raise HTTPException(status_code=400, detail=f"Provider '{pid}' not found")

    # 先校验后改：避免校验失败时被拒绝的字段残留在共享配置缓存中
    if "timeout_seconds" in body and body["timeout_seconds"] not in (None, ""):
        try:
            int(body["timeout_seconds"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="timeout_seconds 必须为数字")
    if "valve_pct" in body:
        try:
            body["valve_pct"] = max(0, min(100, int(body["valve_pct"])))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="valve_pct 必须为 0-100 的整数")

    # v2.12.3 搜索 AI 总结：先校验后改（避免校验失败时被拒绝的字段残留在共享配置缓存中）
    _pid_new = body.get("provider_id", models[idx].get("provider_id"))
    _proto_new = ((next((p for p in config.get("providers", []) if p.get("id") == _pid_new), None) or {})
                  .get("protocol") if _pid_new else body.get("provider")
                  or models[idx].get("provider")) or "openai"
    _sp_new = ((body.get("summary_pool") if "summary_pool" in body else models[idx].get("summary_pool")) or "").strip()
    if _proto_new == "qianfan_web_search" and _sp_new:
        from app.main import pool as _pool
        _why = search_summary.pool_ineligible_reason(_pool, _sp_new)
        if _why:
            raise HTTPException(status_code=400, detail=_why)

    for key, value in body.items():
        if key == "id":
            continue
        if key == "timezone":
            continue
        if key == "is_free":
            value = bool(value)
        if key == "smart_estimate":
            value = bool(value)
        if key == "no_stream_options":
            value = bool(value)
        if key == "cost_enabled":
            value = bool(value)
        if key == "headroom":
            value = bool(value)
        if key == "timeout_seconds":
            value = None if value in (None, "") else int(value)
        # v2.15.0 cost_peak 浅合并：模型编辑表单只提交 windows（闲时判定与缓存计费高峰期同源互通），
        # 不携带的键（enabled/三高峰单价）保留现值，避免整对象覆盖抹掉「预估费用」弹窗里配置的价格
        if key == "cost_peak" and isinstance(value, dict) and isinstance(models[idx].get("cost_peak"), dict):
            _merged = dict(models[idx]["cost_peak"])
            _merged.update(value)
            value = _merged
        models[idx][key] = value
    models[idx]["timezone"] = "Asia/Shanghai"

    # provider-based and inline connection info are mutually exclusive
    if models[idx].get("provider_id"):
        for k in ("provider", "base_url", "api_key"):
            models[idx].pop(k, None)

    # embedding/rerank 模型不参与思考控制：显式剥离映射，避免残留
    if models[idx].get("modality") in ("embedding", "rerank"):
        models[idx].pop("reasoning_map", None)
        models[idx].pop("smart_estimate", None)
        models[idx].pop("headroom", None)  # 压缩只对聊天出站 messages 有意义

    # #46D 跨类型字段剥离：gift 专属/一次性专属字段不残留在其他 token_type 上（防误导配置）
    if models[idx].get("token_type") != "gift":
        models[idx].pop("gift_grant_cap", None)
    if models[idx].get("token_type") != "one_time":
        for _k in ("max_tokens", "ttl_seconds", "expire_date"):
            models[idx].pop(_k, None)

    # v2.12.3 搜索 AI 总结：非千帆网页搜索协议显式剥离，避免切换协议后配置残留
    # （隐藏的表单控件仍会被 FormData 提交旧值，所以清理必须放在服务端）
    if _proto_new == "qianfan_web_search":
        models[idx]["summary_pool"] = _sp_new
        models[idx]["summary_length"] = str(models[idx].get("summary_length") or "").strip()
    else:
        models[idx].pop("summary_pool", None)
        models[idx].pop("summary_length", None)

    # #46 额度调整自动复位粘滞 expired：pool.py 预检中 expired 先于额度判断短路，
    # 用户调高 max_tokens 至超过当前已用后自动翻转 1→0，让模型立即恢复可用
    if models[idx].get("token_type") == "one_time" and int(models[idx].get("max_tokens") or 0) > 0:
        state = await db.get_one_time_state(model_id)
        if state and state.get("expired") and int(state["used_tokens"]) < int(models[idx]["max_tokens"]):
            flipped = await db.clear_one_time_expired(model_id)
            if flipped:
                logging.getLogger(__name__).info(
                    f"[#46] 额度调整自动复位 expired: model={model_id} "
                    f"used={state['used_tokens']} new_max={models[idx]['max_tokens']}"
                )

    config["models"] = models
    save_config(config)
    restart_scheduler()
    _sync_pool()
    return {"ok": True, "model": models[idx]}


@router.delete("/models/{model_id:path}")
async def delete_model(model_id: str, _=Depends(verify_admin)):
    config = load_config()
    models = config.get("models", [])
    new_models = [m for m in models if m["id"] != model_id]
    if len(new_models) == len(models):
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")

    config["models"] = new_models
    for pool_name, pool_cfg in config.get("pools", {}).items():
        pool_cfg["model_ids"] = [mid for mid in pool_cfg.get("model_ids", []) if mid != model_id]

    # Also clear any single_override that points to the deleted model
    if "single_override" in config:
        config["single_override"] = {
            k: v for k, v in config["single_override"].items()
            if v != model_id
        }

    save_config(config)
    restart_scheduler()
    _sync_pool()
    # #46：删除模型时同步清理 one_time 状态行（防同名重建带旧 expired 粘滞位）；行可能不存在，静默失败
    try:
        await db.delete_one_time_state(model_id)
    except Exception:
        pass
    return {"ok": True}


@router.get("/providers")
async def get_providers(_=Depends(verify_admin)):
    config = load_config()
    return {"providers": config.get("providers", [])}


@router.post("/providers")
async def add_provider(request: Request, _=Depends(verify_admin)):
    body = await request.json()
    for field in ["id", "name", "protocol", "base_url", "api_key"]:
        if not body.get(field):
            raise HTTPException(status_code=400, detail=f"Missing field: {field}")
    if body["protocol"] not in ("openai", "anthropic", "openai_responses", "qianfan_search", "qianfan_web_search"):
        raise HTTPException(status_code=400, detail="protocol must be 'openai', 'anthropic', 'openai_responses', 'qianfan_search' or 'qianfan_web_search'")
    # 问题30（v2.12.3）：转发层会在 base_url 后自动拼接 /chat/completions，尾部再带一段会双重路径（稳定 404/400）
    if body["base_url"].rstrip("/").lower().endswith("/chat/completions"):
        raise HTTPException(status_code=400, detail="base_url 不能以 /chat/completions 结尾：转发层会自动拼接该路径")

    config = load_config()
    providers = config.setdefault("providers", [])
    for p in providers:
        if p["id"] == body["id"]:
            raise HTTPException(status_code=409, detail=f"Provider id '{body['id']}' already exists")

    entry = {
        "id": body["id"],
        "name": body["name"],
        "protocol": body["protocol"],
        "base_url": body["base_url"],
        "api_key": body["api_key"],
        "proxy_url": body.get("proxy_url", ""),
    }
    providers.append(entry)
    config["providers"] = providers
    save_config(config)
    restart_scheduler()
    _sync_pool()
    return {"ok": True, "provider": entry}


@router.put("/providers/reorder")
async def reorder_providers(request: Request, _=Depends(verify_admin)):
    """Reorder providers. Body: {provider_ids: ['id1', 'id2', ...]}"""
    body = await request.json()
    new_order = body.get("provider_ids", [])
    if not isinstance(new_order, list):
        raise HTTPException(status_code=400, detail="provider_ids must be a list")

    config = load_config()
    providers = config.get("providers", [])
    existing_ids = {p["id"] for p in providers}
    if set(new_order) != existing_ids:
        raise HTTPException(status_code=400, detail="provider_ids must contain exactly all existing provider IDs")

    # Rebuild providers list in new order
    by_id = {p["id"]: p for p in providers}
    config["providers"] = [by_id[pid] for pid in new_order]
    save_config(config)
    restart_scheduler()
    _sync_pool()  # #47：供应商排序落库即热生效（与其他改配置端点对齐）
    return {"ok": True}

@router.put("/providers/{provider_id:path}")
async def update_provider(provider_id: str, request: Request, _=Depends(verify_admin)):
    body = await request.json()
    config = load_config()
    providers = config.get("providers", [])
    idx = None
    for i, p in enumerate(providers):
        if p["id"] == provider_id:
            idx = i
            break
    if idx is None:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")

    if "protocol" in body and body["protocol"] not in ("openai", "anthropic", "openai_responses", "qianfan_search", "qianfan_web_search"):
        raise HTTPException(status_code=400, detail="protocol must be 'openai', 'anthropic', 'openai_responses', 'qianfan_search' or 'qianfan_web_search'")
    # 问题30（v2.12.3）：同 add_provider，尾缀 /chat/completions 会与转发层拼接双重路径
    if "base_url" in body and (body.get("base_url") or "").rstrip("/").lower().endswith("/chat/completions"):
        raise HTTPException(status_code=400, detail="base_url 不能以 /chat/completions 结尾：转发层会自动拼接该路径")
    for key, value in body.items():
        if key == "id":
            continue
        providers[idx][key] = value

    config["providers"] = providers
    save_config(config)
    restart_scheduler()
    _sync_pool()
    return {"ok": True, "provider": providers[idx]}


@router.delete("/providers/{provider_id:path}")
async def delete_provider(provider_id: str, _=Depends(verify_admin)):
    config = load_config()
    providers = config.get("providers", [])
    new_providers = [p for p in providers if p["id"] != provider_id]
    if len(new_providers) == len(providers):
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")

    refs = [m["id"] for m in config.get("models", []) if m.get("provider_id") == provider_id]
    if refs:
        raise HTTPException(status_code=400, detail=f"供应商下仍有模型：{', '.join(refs)}，请先删除或迁移这些模型")

    config["providers"] = new_providers
    save_config(config)
    restart_scheduler()
    return {"ok": True}


@router.get("/pools")
async def get_pools(_=Depends(verify_admin)):
    from app.main import pool
    # Return from in-memory pool.pools (includes auto-created 兜底池).
    # pool_order 保留 Python dict 的插入顺序：JS 的 Object.keys 会把纯数字池名
    # （如 "123"）按整数键规则提到最前，打破预期顺序，故显式下发有序数组。
    return {"pools": pool.pools, "pool_order": list(pool.pools.keys()),
            # v2.12.3：可作为「搜索 AI 总结」目标的池（排除搜索池、embedding/rerank 池、空池）
            "summary_candidates": search_summary.summary_candidates(pool)}


@router.post("/pools")
async def create_pool(request: Request, _=Depends(verify_admin)):
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Missing pool name")
    if "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="池名称不能包含 / 或 \\ 字符")

    config = load_config()
    pools = config.setdefault("pools", {})
    if name in pools:
        raise HTTPException(status_code=409, detail=f"Pool '{name}' already exists")
    if body.get("owner_key_id"):
        raise HTTPException(status_code=400, detail="专属模型池请在密钥编辑界面创建")

    pools[name] = {
        "strategy": body.get("strategy", "sequential"),
        "model_ids": body.get("model_ids", []),
        "auto_order": bool(body.get("auto_order", False)),
        "load_balance": bool(body.get("load_balance", False)),
    }
    # 互斥：auto_order 与 load_balance 不可同时开启
    if pools[name]["load_balance"]:
        pools[name]["auto_order"] = False
    if body.get("fallback_pool"):
        pools[name]["fallback_pool"] = body["fallback_pool"]
    save_config(config)
    restart_scheduler()
    _sync_pool()
    return {"ok": True, "pool": pools[name]}


@router.delete("/pools/{pool_name}")
async def delete_pool(pool_name: str, _=Depends(verify_admin)):
    if pool_name in ("auto", "兜底池"):
        raise HTTPException(status_code=400, detail=f"Pool '{pool_name}' cannot be deleted")

    config = load_config()
    pools = config.get("pools", {})
    if pool_name not in pools:
        raise HTTPException(status_code=404, detail=f"Pool '{pool_name}' not found")
    if pools[pool_name].get("owner_key_id"):
        raise HTTPException(status_code=400, detail="专属模型池请在密钥编辑界面删除")

    del pools[pool_name]
    save_config(config)
    restart_scheduler()
    _sync_pool()
    return {"ok": True}


@router.put("/pools/reorder")
async def reorder_pools(request: Request, _=Depends(verify_admin)):
    """Reorder pools (body: {pool_names: ["pool1", "pool2", ...]}). auto always first."""
    body = await request.json()
    pool_names = body.get("pool_names", [])
    if not isinstance(pool_names, list):
        raise HTTPException(status_code=400, detail="pool_names must be a list")

    config = load_config()
    pools = config.get("pools", {})

    # 防御：pool_names 必须包含全部现有池（除 auto/兜底池），防止不完整列表误删池
    existing_others = {n for n in pools if n not in ("auto", "兜底池")}
    provided_others = {n for n in pool_names if n in pools and n not in ("auto", "兜底池")}
    missing = existing_others - provided_others
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"pool_names 不完整，缺少 {len(missing)} 个池: {sorted(missing)}；拒绝重排以防误删",
        )

    # auto must always be first; 兜底池 goes last
    ordered = ["auto"]
    for name in pool_names:
        if name not in ("auto", "兜底池") and name in pools:
            ordered.append(name)
    ordered.append("兜底池")

    # Rewrite pools dict in new order
    new_pools = {}
    for name in ordered:
        if name in pools:
            new_pools[name] = pools[name]
    config["pools"] = new_pools
    save_config(config)
    _sync_pool()
    return {"ok": True, "pools": list(new_pools.keys())}


@router.put("/pools/fallback_targets")
async def set_fallback_targets(request: Request, _=Depends(verify_admin)):
    """设置兜底池为哪些池兜底。Body: {pool_names: [...]} — 这些池的 fallback_pool 将指向兜底池；
    未列出的非 auto 池若当前指向兜底池则清空。auto 池强制兜底（不可取消）。"""
    body = await request.json()
    pool_names = body.get("pool_names", [])
    if not isinstance(pool_names, list):
        raise HTTPException(status_code=400, detail="pool_names must be a list")

    config = load_config()
    pools = config.get("pools", {})
    valid = {n for n in pool_names if n in pools and n != "auto" and n != "兜底池"}
    if set(pool_names) - valid - {"auto", "兜底池"}:
        missing = sorted(set(pool_names) - valid - {"auto", "兜底池"})
        raise HTTPException(status_code=400, detail=f"未知池名: {missing}")

    changed = False
    for pname in pools:
        if pname == "auto":
            # auto 池强制兜底
            if pools[pname].get("fallback_pool") != "兜底池":
                pools[pname]["fallback_pool"] = "兜底池"
                changed = True
        elif pname == "兜底池":
            continue
        else:
            target = "兜底池" if pname in valid else None
            cur = pools[pname].get("fallback_pool")
            if target is None and cur == "兜底池":
                pools[pname]["fallback_pool"] = None
                changed = True
            elif target == "兜底池" and cur != "兜底池":
                pools[pname]["fallback_pool"] = "兜底池"
                changed = True

    if changed:
        save_config(config)
        _sync_pool()
    return {"ok": True, "targets": sorted(valid)}


@router.put("/pools/{pool_name}")
async def update_pool(pool_name: str, request: Request, _=Depends(verify_admin)):
    body = await request.json()
    model_ids = body.get("model_ids")
    if model_ids is None:
        raise HTTPException(status_code=400, detail="Missing model_ids")
    # 先做全部校验再改配置：load_config 返回共享缓存，先改后校验会让被拒绝的改动残留在缓存中
    slow_threshold = None
    if "slow_latency_threshold" in body:
        try:
            slow_threshold = int(body["slow_latency_threshold"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="slow_latency_threshold 必须为数字（0=不限）")

    config = load_config()
    pools = config.setdefault("pools", {})
    if pool_name not in pools:
        pools[pool_name] = {}
    # v2.13.3 Switch 切换开关：先校验后改配置（本文件约定：被拒绝的改动不得残留共享缓存）。
    # 开启时按本次提交的 model_ids 校验池内两侧齐全：local=token_type 为 local 的本地模型，net=其余云端模型
    if "switch_enabled" in body and bool(body["switch_enabled"]) and not pools[pool_name].get("switch_enabled"):
        from app.main import pool as _pool
        probe = {"pools": {**config.get("pools", {}), pool_name: {**pools[pool_name], "model_ids": model_ids}}}
        members, broken = _expand_pool_members(probe, pool_name)
        if broken:
            raise HTTPException(status_code=400,
                                detail=f"开启 Switch 前先修复断裂引用: {'、'.join(sorted(set(broken))[:5])}（引用的池不存在）")
        unknown = sorted({m for m in members if m not in _pool.registry})
        if unknown:
            raise HTTPException(status_code=400,
                                detail=f"开启 Switch 前先移除不存在的模型引用: {'、'.join(unknown[:5])}")
        locals_ = sorted({m for m in members if _pool.registry[m].token_type == "local"})
        nets = sorted({m for m in members if m not in unknown and _pool.registry[m].token_type != "local"})
        if not locals_:
            raise HTTPException(status_code=400,
                                detail="开启 Switch 需要池内含本地模型：请先将模型令牌类型设为 local（本地）再加入池")
        if not nets:
            raise HTTPException(status_code=400,
                                detail="开启 Switch 需要池内含云端模型：当前全部为本地模型（local），请加入至少一个云端模型")
    pools[pool_name]["model_ids"] = model_ids
    if "strategy" in body:
        pools[pool_name]["strategy"] = body["strategy"]
    if "auto_order" in body:
        pools[pool_name]["auto_order"] = bool(body["auto_order"])
    if "load_balance" in body:
        pools[pool_name]["load_balance"] = bool(body["load_balance"])
    # 互斥：auto_order 与 load_balance 不可同时开启；本次请求新开启者优先，另一者自动关闭
    # （旧逻辑固定 load_balance 优先，想切回 auto_order 时会被静默丢弃）
    if body.get("load_balance"):
        pools[pool_name]["load_balance"] = True
        pools[pool_name]["auto_order"] = False
    elif body.get("auto_order"):
        pools[pool_name]["auto_order"] = True
        pools[pool_name]["load_balance"] = False
    else:
        if pools[pool_name].get("load_balance"):
            pools[pool_name]["auto_order"] = False
        elif pools[pool_name].get("auto_order"):
            pools[pool_name]["load_balance"] = False
    if slow_threshold is not None:
        pools[pool_name]["slow_latency_threshold"] = slow_threshold
    if "switch_enabled" in body:
        pools[pool_name]["switch_enabled"] = bool(body["switch_enabled"])

    save_config(config)
    restart_scheduler()
    _sync_pool()
    return {"ok": True, "pool": pools[pool_name]}


@router.post("/pools/{pool_name}/rename")
async def rename_pool(pool_name: str, request: Request, _=Depends(verify_admin)):
    if pool_name in ("auto", "兜底池"):
        raise HTTPException(status_code=400, detail=f"Pool '{pool_name}' cannot be renamed")
    body = await request.json()
    new_name = (body.get("new_name") or "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Missing new_name")
    if "/" in new_name or "\\" in new_name:
        raise HTTPException(status_code=400, detail="池名称不能包含 / 或 \\ 字符")
    if new_name == pool_name:
        return {"ok": True, "pool_name": pool_name}

    config = load_config()
    pools = config.get("pools", {})
    if pool_name not in pools:
        raise HTTPException(status_code=404, detail=f"Pool '{pool_name}' not found")
    if pools[pool_name].get("owner_key_id"):
        raise HTTPException(status_code=400, detail="专属模型池不允许重命名")
    if new_name in pools:
        raise HTTPException(status_code=409, detail=f"Pool '{new_name}' already exists")

    # Move pool data to new key
    pools[new_name] = pools.pop(pool_name)

    # Migrate pool:old_name references in all other pools
    old_ref = f"pool:{pool_name}"
    new_ref = f"pool:{new_name}"
    for pname, pcfg in pools.items():
        ids = pcfg.get("model_ids", [])
        if old_ref in ids:
            pcfg["model_ids"] = [new_ref if x == old_ref else x for x in ids]

    save_config(config)
    restart_scheduler()
    _sync_pool()
    return {"ok": True, "pool_name": new_name}


@router.post("/pools/{pool_name}/single_override")
async def set_single_override(pool_name: str, request: Request, _=Depends(verify_admin)):
    """Lock a pool to use only one model. Body: {model_id: "..."}"""
    body = await request.json()
    model_id = (body.get("model_id") or "").strip()
    if not model_id:
        raise HTTPException(status_code=400, detail="Missing model_id")

    from app.main import pool
    if pool_name not in pool.pools:
        raise HTTPException(status_code=404, detail=f"Pool '{pool_name}' not found")
    if model_id not in pool.registry:
        raise HTTPException(status_code=400, detail=f"Model '{model_id}' not found in registry")

    # Verify model is reachable from this pool (directly or via sub-pools)
    reachable = pool._collect_pool_models(pool_name)
    if model_id not in reachable:
        raise HTTPException(status_code=400, detail=f"Model '{model_id}' is not reachable from pool '{pool_name}'")

    pool.set_single_override(pool_name, model_id)
    return {"ok": True, "pool_name": pool_name, "model_id": model_id}


@router.delete("/pools/{pool_name}/single_override")
async def clear_single_override(pool_name: str, _=Depends(verify_admin)):
    """Release single-model lock, return to normal pool behavior."""
    from app.main import pool
    if pool_name not in pool.pools:
        raise HTTPException(status_code=404, detail=f"Pool '{pool_name}' not found")

    model_id = pool.clear_single_override(pool_name)
    return {"ok": True, "pool_name": pool_name, "cleared": model_id is not None}


@router.get("/pools/{pool_name}/single_override")
async def get_single_override(pool_name: str, _=Depends(verify_admin)):
    """Get current single-model override for a pool."""
    from app.main import pool
    if pool_name not in pool.pools:
        raise HTTPException(status_code=404, detail=f"Pool '{pool_name}' not found")

    model_id = pool.single_override.get(pool_name)
    return {"ok": True, "pool_name": pool_name, "model_id": model_id}


@router.post("/reload")
async def reload_pool(request: Request, _=Depends(verify_admin)):
    from app.main import pool
    pool.reload()
    restart_scheduler()
    return {"ok": True}


@router.get("/decisions")
async def get_decisions(pool: str | None = None, limit: int = 100, _=Depends(verify_admin)):
    from app.core import database as db
    rows = await db.get_decisions(pool_name=pool, limit=min(limit, 500))
    return {"decisions": rows}


@router.get("/stats/grouped")
async def stats_grouped(dim: str = "model", days: int = 1, _=Depends(verify_admin)):
    """问题39：用量统计按维度聚合查看。dim ∈ provider|model|pool|key，days=1 今日（自然日）/7/30 近 N 天。

    口径：数据源 decision_log（含失败请求），calls=请求次数、tokens=actual_tokens 求和（失败为 0）。
    pool 维度按请求实际命中的池聚合（父池调用计入父池行，直接调子池单列）；provider 维度经
    config 模型→provider_id 映射换算，独立模型归「独立模型」。"""
    from datetime import datetime, timedelta
    from app.core import database as db

    if dim not in ("provider", "model", "pool", "key"):
        raise HTTPException(status_code=400, detail="dim 必须是 provider/model/pool/key 之一")
    days = 1 if days < 1 else (30 if days > 30 else days)
    base = datetime.now() - timedelta(days=days - 1)
    since = datetime(base.year, base.month, base.day).timestamp()

    if dim == "provider":
        rows = await db.get_usage_grouped("model", since)
        prov_of, by_prov = {}, {}
        for m in load_config().get("models", []):
            prov_of[m["id"]] = m.get("provider_id") or ""
        for r in rows:
            pid = prov_of.get(r["name"]) or "独立模型"
            agg = by_prov.setdefault(pid, {"name": pid, "calls": 0, "tokens": 0})
            agg["calls"] += r["calls"]
            agg["tokens"] += r["tokens"]
        out = sorted(by_prov.values(), key=lambda x: (-x["tokens"], -x["calls"]))
    else:
        out = await db.get_usage_grouped(dim, since)
    return {"dim": dim, "days": days, "since": since, "rows": out}


@router.post("/test_model")
async def test_model(request: Request, _=Depends(verify_admin)):
    """Lightweight connectivity test using the form's current values (not saved)."""
    body = await request.json()
    base_url = (body.get("base_url") or "").strip()
    api_key = (body.get("api_key") or "").strip()
    protocol = body.get("protocol") or body.get("provider") or "openai"
    model_name = (body.get("model_name") or body.get("name") or "").strip()
    modality = (body.get("modality") or "text").strip()
    if not base_url or not api_key or not model_name:
        raise HTTPException(400, "缺少 base_url / api_key / model_name")
    if protocol not in ("openai", "anthropic", "openai_responses", "qianfan_search", "qianfan_web_search"):
        raise HTTPException(400, "protocol 必须为 openai、anthropic、openai_responses、qianfan_search 或 qianfan_web_search")

    if protocol == "anthropic":
        provider = AnthropicProvider(base_url, api_key)
    elif protocol == "openai_responses":
        from app.providers.responses_provider import ResponsesProvider
        provider = ResponsesProvider(base_url, api_key)
    elif protocol in ("qianfan_search", "qianfan_web_search"):
        provider = QianfanSearchProvider(base_url, api_key,
                                         variant=("web_search" if protocol == "qianfan_web_search" else "summary"))
    else:
        provider = OpenAIProvider(base_url, api_key)
    from app.core import database as db
    import time as _time
    try:
        # embedding 模型发 /embeddings 短文本测试（chat/completions 对嵌入模型必然 400）
        if modality == "embedding":
            if protocol != "openai":
                raise HTTPException(400, "embedding 测试仅支持 openai 兼容协议")
            from app.core.models import EmbeddingRequest
            req = EmbeddingRequest(model=model_name, input="connectivity test")
            start = _time.perf_counter()
            try:
                data = await provider.embeddings(req, model_name, {})
                elapsed = _time.perf_counter() - start
                # tokens/tps 与 chat 测速同字段（上游无 usage 时为 0），避免前端显示 undefined
                tokens = int((data.get("usage") or {}).get("total_tokens") or 0)
                return {"ok": True, "result": {"status": "ok", "latency_ms": round(elapsed * 1000),
                                               "tokens": tokens,
                                               "tps": round(tokens / elapsed, 1) if elapsed > 0 and tokens else 0,
                                               "model_id": model_name, "type": "embedding"}}
            except Exception as e:
                elapsed = _time.perf_counter() - start
                return {"ok": True, "result": {"status": "error", "error": str(e)[:300],
                                               "latency_ms": round(elapsed * 1000), "type": "embedding"}}
        elif modality == "rerank":
            # rerank 模型发 /rerank 短文本测试（chat/completions 对重排模型必然 400）
            if protocol != "openai":
                raise HTTPException(400, "rerank 测试仅支持 openai 兼容协议")
            from app.core.models import RerankRequest
            req = RerankRequest(model=model_name, query="connectivity test",
                                documents=["hello world", "gateway rerank test"])
            start = _time.perf_counter()
            try:
                data = await provider.rerank(req, model_name, {})
                elapsed = _time.perf_counter() - start
                tokens = int((data.get("usage") or {}).get("total_tokens") or 0)
                return {"ok": True, "result": {"status": "ok", "latency_ms": round(elapsed * 1000),
                                               "tokens": tokens,
                                               "tps": round(tokens / elapsed, 1) if elapsed > 0 and tokens else 0,
                                               "model_id": model_name, "type": "rerank"}}
            except Exception as e:
                elapsed = _time.perf_counter() - start
                return {"ok": True, "result": {"status": "error", "error": str(e)[:300],
                                               "latency_ms": round(elapsed * 1000), "type": "rerank"}}
        result = await provider.speedtest(model_name)
        # Record usage for stats even on test calls
        if result.get("status") == "ok" and result.get("tokens", 0) > 0:
            test_id = f"__test__{model_name}"
            await db.add_daily_usage(test_id, result["tokens"])
        return {"ok": True, "result": result}
    finally:
        await provider.close()


@router.post("/test_proxy")
async def test_proxy(request: Request, _=Depends(verify_admin)):
    """测试供应商代理连通性 + 拉取 Clash/Mihomo 当前节点。

    Body: {proxy_url, base_url}（都要）。
    通过 proxy 请求 base_url 测延迟；再从 Clash 控制 API (http://127.0.0.1:9090)
    读 GLOBAL.now 获取当前选中节点名。控制 API 不可达时 node 返回 null，不影响延迟结果。
    """
    import httpx as _httpx
    import time as _time

    body = await request.json()
    proxy_url = (body.get("proxy_url") or "").strip()
    base_url = (body.get("base_url") or "").strip()
    if not proxy_url or not base_url:
        raise HTTPException(status_code=400, detail="缺少 proxy_url / base_url")

    result = {"ok": False, "latency_ms": None, "node": None, "error": None}

    # 1. 通过代理拉 base_url 测延迟（不核验 TLS cert，能连通即算通）
    try:
        async with _httpx.AsyncClient(
            proxy=proxy_url,
            verify=False,
            timeout=_httpx.Timeout(15, connect=10),
        ) as client:
            t0 = _time.perf_counter()
            # 请求一个轻量端点判断连通性：/ 或 /v1/models 都行，401 也算通
            resp = await client.get(base_url.rstrip("/") + "/", follow_redirects=True)
            latency_ms = round((_time.perf_counter() - t0) * 1000, 1)
            result["ok"] = resp.status_code < 500  # <500 视为连通（401/403/404 都算代理通）
            result["latency_ms"] = latency_ms
            if resp.status_code >= 500:
                result["error"] = f"上游 HTTP {resp.status_code}"
            # 记录测试请求的 host，用于查真实出口节点
            try:
                from urllib.parse import urlparse
                result["_test_host"] = urlparse(base_url).hostname or ""
            except Exception:
                result["_test_host"] = ""
            # ★ Fix A: 在 client 仍存活时（连接池未关）解析 Clash 当前节点。
            # 延迟到 async with 退出后查 /connections，live chain 已消失，Step A 永远扑空。
            try:
                await _fetch_clash_node(result, _httpx)
            except Exception:
                pass
    except Exception as e:
        result["error"] = type(e).__name__ + ": " + str(e)[:160]
        return result

    return result


async def _fetch_clash_node(result: dict, _httpx, probe_host: str = ""):
    """Resolve the REAL active Clash node.

    1) Query /connections, find the live tunnel whose metadata.host matches our test
       host, take chains[0] (the actual node). This works in rule mode too.
    2) Fallback: prefer the user's master selector (e.g. "🌏 当前选择") — follow its
       `now` recursively (up to 3 hops) until we resolve to a real node, so nested
       groups like "🌏 当前选择 → 🇸🇬 新加坡自动 → TG-SG-1(hysteria)" map to the
       actual exit node instead of the first non-DIRECT group in JSON order
       (which is unreliable when multiple regions are configured).
       Only if the master group is missing do we fall back to the generic
       any-Selector/URLTest/Fallback scan.
    3) Last resort: GLOBAL.now (global mode only).
    """
    # 优先使用显式参数；兼容旧调用方（仅传 result 时回退到 result["_test_host"]）
    probe_host = probe_host or result.get("_test_host") or ""

    for port in (9097, 9090, 9091, 9098, 9898, 6170, 6189):
        base = f"http://127.0.0.1:{port}"
        try:
            async with _httpx.AsyncClient(timeout=1.5) as cc:
                # Step A: live connection match
                r = await cc.get(f"{base}/connections")
                if r.status_code == 200:
                    conns = r.json().get("connections", [])
                    for c in conns:
                        chains = c.get("chains") or []
                        meta = c.get("metadata") or {}
                        if probe_host and meta.get("host") == probe_host and chains:
                            node = chains[0]
                            if node and node.upper() != "DIRECT":
                                result["node"] = node
                                result["node_clash_port"] = port
                                return

                # Step B: 优先解析用户主选择组，下钻至真实节点
                r2 = await cc.get(f"{base}/proxies")
                if r2.status_code == 200:
                    proxies = r2.json().get("proxies", {})
                    master_keys = ("🌏 当前选择", "🌍 当前选择", "当前选择")
                    master_found = False
                    for mk in master_keys:
                        if mk in proxies:
                            master_found = True
                            target = mk
                            visited = set()
                            # 最多下钻 3 层（master → 区域自动组 → 节点）
                            for _hop in range(3):
                                if target in visited:
                                    break
                                visited.add(target)
                                grp = proxies.get(target) or {}
                                now = grp.get("now")
                                if not now or now.upper() == "DIRECT":
                                    break
                                sub = proxies.get(now)
                                if sub and sub.get("type") in ("Selector", "URLTest", "Fallback"):
                                    target = now
                                    continue
                                # now 指向真实节点（或非已知的 Selector）
                                result["node"] = now
                                result["node_clash_port"] = port
                                return
                            # 找到主选择组但解析不出节点 → 不再走原循环兜底，
                            # 避免被同 JSON 顺序中的另一个区域组（如"🇨🇳 台湾自动"）干扰
                            break
                    if not master_found:
                        # 没有任何主选择组 → 走原循环兜底
                        for pname, p in proxies.items():
                            if p.get("type") in ("Selector", "URLTest", "Fallback"):
                                now = p.get("now")
                                if now and now.upper() != "DIRECT" and now != pname:
                                    result["node"] = now
                                    result["node_clash_port"] = port
                                    return
        except Exception:
            continue

    # Step C: GLOBAL.now (only meaningful in global mode)
    try:
        async with _httpx.AsyncClient(timeout=1.5) as cc:
            for port in (9097, 9090, 9091):
                try:
                    r = await cc.get(f"http://127.0.0.1:{port}/proxies")
                    if r.status_code == 200:
                        gl = r.json().get("proxies", {}).get("GLOBAL") or {}
                        if gl.get("now"):
                            result["node"] = gl["now"]
                            result["node_clash_port"] = port
                            return
                except Exception:
                    continue
    except Exception:
        pass


# ── API Key 管理 ─────────────────────────────────────────────────────

@router.get("/keys")
async def list_keys(_=Depends(verify_admin)):
    from app.core import database as db
    from app.core import keyauth as ka
    keys = await db.list_api_keys()
    config = load_config()
    pools = config.get("pools", {})
    pool_by_key = {}
    for pname, pcfg in pools.items():
        oid = pcfg.get("owner_key_id")
        if oid:
            pool_by_key[str(oid)] = pname
    out = []
    for k in keys:
        summary = await ka.key_usage_summary(k)
        out.append({**k, "usage": summary, "own_pool": pool_by_key.get(str(k["id"]))})
    return {"keys": out}


@router.post("/keys")
async def create_key(request: Request, _=Depends(verify_admin)):
    from app.core import database as db
    from app.core import keyauth as ka
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="缺少密钥名称")
    ktype = body.get("type", "user")
    if ktype not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="type 必须为 admin 或 user")
    allowed_pools = body.get("allowed_pools") or []
    if not isinstance(allowed_pools, list):
        raise HTTPException(status_code=400, detail="allowed_pools 必须为数组")
    token_type = body.get("token_type", "")
    if token_type not in ("", "daily", "rolling_5h", "one_time"):
        raise HTTPException(status_code=400, detail="token_type 非法")
    billing_mode = body.get("billing_mode", "token")
    if billing_mode not in ("token", "request"):
        raise HTTPException(status_code=400, detail="billing_mode 必须为 token 或 request")
    try:
        limit_amount = int(body.get("limit_amount") or 0)
    except ValueError:
        raise HTTPException(status_code=400, detail="limit_amount 必须为数字")
    if limit_amount < 0:
        raise HTTPException(status_code=400, detail="limit_amount 不能为负")
    try:
        expire_seconds = int(body.get("expire_seconds") or 0)
    except ValueError:
        raise HTTPException(status_code=400, detail="expire_seconds 必须为数字")
    if expire_seconds < 0:
        raise HTTPException(status_code=400, detail="expire_seconds 不能为负")
    if ktype == "admin":
        token_type, billing_mode, limit_amount, expire_seconds = "", "token", 0, 0
        allowed_pools = []

    secret = ka.generate_key()
    key_id = await db.create_api_key(name, secret, ktype, allowed_pools,
                                     token_type, billing_mode, limit_amount,
                                     expire_seconds=expire_seconds)
    rec = await db.get_api_key_by_id(key_id)
    return {"ok": True, "key": rec}


@router.put("/keys/{key_id}")
async def update_key(key_id: int, request: Request, _=Depends(verify_admin)):
    from app.core import database as db
    body = await request.json()
    rec0 = await db.get_api_key_by_id(key_id)
    if not rec0:
        raise HTTPException(status_code=404, detail=f"API Key #{key_id} 不存在")
    fields = {}
    if "name" in body:
        nm = (body["name"] or "").strip()
        if nm:
            fields["name"] = nm
    if "type" in body:
        if body["type"] not in ("admin", "user"):
            raise HTTPException(status_code=400, detail="type 必须为 admin 或 user")
        fields["type"] = body["type"]
    if "enabled" in body:
        fields["enabled"] = 1 if body["enabled"] else 0
    if "allowed_pools" in body:
        if not isinstance(body["allowed_pools"], list):
            raise HTTPException(status_code=400, detail="allowed_pools 必须为数组")
        fields["allowed_pools"] = body["allowed_pools"]
    if "token_type" in body:
        if body["token_type"] not in ("", "daily", "rolling_5h", "one_time"):
            raise HTTPException(status_code=400, detail="token_type 非法")
        fields["token_type"] = body["token_type"]
    if "billing_mode" in body:
        if body["billing_mode"] not in ("token", "request"):
            raise HTTPException(status_code=400, detail="billing_mode 必须为 token 或 request")
        fields["billing_mode"] = body["billing_mode"]
    if "limit_amount" in body:
        try:
            v = int(body["limit_amount"] or 0)
        except ValueError:
            raise HTTPException(status_code=400, detail="limit_amount 必须为数字")
        if v < 0:
            raise HTTPException(status_code=400, detail="limit_amount 不能为负")
        fields["limit_amount"] = v
    if "expire_seconds" in body:
        try:
            v = int(body["expire_seconds"] or 0)
        except ValueError:
            raise HTTPException(status_code=400, detail="expire_seconds 必须为数字")
        if v < 0:
            raise HTTPException(status_code=400, detail="expire_seconds 不能为负")
        cur_v = rec0.get("expire_seconds") or 0
        if v != cur_v:
            fields["expire_seconds"] = v
            # 过期时长被修改时重置轮换时间，从 created_at 重新计时新周期
            fields["rotated_at"] = None
    # 管理员 Key 强制无限制
    if fields.get("type") == "admin":
        fields["allowed_pools"] = []
        fields["token_type"] = ""
        fields["limit_amount"] = 0
        fields["expire_seconds"] = 0
        fields["rotated_at"] = None
    if not fields:
        raise HTTPException(status_code=400, detail="没有可更新的字段")
    await db.update_api_key(key_id, fields)
    rec = await db.get_api_key_by_id(key_id)
    return {"ok": True, "key": rec}


@router.delete("/keys/{key_id}")
async def delete_key(key_id: int, _=Depends(verify_admin)):
    from app.core import database as db
    rec = await db.get_api_key_by_id(key_id)
    if not rec:
        raise HTTPException(status_code=404, detail=f"API Key #{key_id} 不存在")
    # 同时清理该密钥的专属模型池及引用
    config = load_config()
    pools = config.get("pools", {})
    targets = [n for n, p in pools.items() if p.get("owner_key_id") == str(key_id)]
    for t in targets:
        del pools[t]
        for pcfg in pools.values():
            pcfg["model_ids"] = [x for x in pcfg.get("model_ids", []) if x != t and x != f"pool:{t}"]
    if targets:
        save_config(config)
    await db.delete_api_key(key_id)
    _sync_pool()
    return {"ok": True}


@router.get("/keys/{key_id}/rotations")
async def get_key_rotations(key_id: int, _=Depends(verify_admin)):
    from app.core import database as db
    rec = await db.get_api_key_by_id(key_id)
    if not rec:
        raise HTTPException(status_code=404, detail=f"API Key #{key_id} 不存在")
    return {"rotations": await db.get_key_rotations(key_id)}


# ── 用户专属模型池（仅密钥编辑界面可创建/删除，功能与普通池一致）──────

def _find_key_pool(config: dict, key_id: int) -> tuple[str, dict] | None:
    pools = config.get("pools", {})
    for name, pcfg in pools.items():
        if pcfg.get("owner_key_id") == str(key_id):
            return name, pcfg
    return None


@router.get("/keys/{key_id}/pool")
async def get_key_pool(key_id: int, _=Depends(verify_admin)):
    from app.core import database as db
    rec = await db.get_api_key_by_id(key_id)
    if not rec:
        raise HTTPException(status_code=404, detail=f"API Key #{key_id} 不存在")
    found = _find_key_pool(load_config(), key_id)
    if not found:
        return {"ok": True, "pool": None}
    name, pcfg = found
    return {"ok": True, "pool": {**pcfg, "name": name}}


@router.post("/keys/{key_id}/pool")
async def create_key_pool(key_id: int, request: Request, _=Depends(verify_admin)):
    from app.core import database as db
    rec = await db.get_api_key_by_id(key_id)
    if not rec:
        raise HTTPException(status_code=404, detail=f"API Key #{key_id} 不存在")
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="缺少专属池名称")
    if "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="池名称不能包含 / 或 \\ 字符")

    config = load_config()
    pools = config.setdefault("pools", {})
    if _find_key_pool(config, key_id):
        raise HTTPException(status_code=409, detail="该密钥已存在专属模型池")
    if name in pools:
        raise HTTPException(status_code=409, detail=f"池名称 '{name}' 已存在")

    pools[name] = {
        "strategy": "sequential",
        "model_ids": [],
        "auto_order": False,
        "owner_key_id": str(key_id),
    }
    save_config(config)
    restart_scheduler()
    _sync_pool()
    # 自动加入该密钥的授权池，使其可被调用（放在 save_config 之后：配置先落盘，避免异常时缓存与磁盘不一致）
    allowed = list(rec["allowed_pools"])
    if name not in allowed:
        allowed.append(name)
        await db.update_api_key(key_id, {"allowed_pools": allowed})
    return {"ok": True, "pool": pools[name]}


@router.delete("/keys/{key_id}/pool")
async def delete_key_pool(key_id: int, _=Depends(verify_admin)):
    from app.core import database as db
    config = load_config()
    found = _find_key_pool(config, key_id)
    if not found:
        raise HTTPException(status_code=404, detail="该密钥没有专属模型池")
    name, _ = found
    pools = config.get("pools", {})
    del pools[name]
    for pcfg in pools.values():
        pcfg["model_ids"] = [x for x in pcfg.get("model_ids", []) if x != name and x != f"pool:{name}"]
    # 从该密钥授权池移除
    rec = await db.get_api_key_by_id(key_id)
    if rec:
        allowed = [x for x in rec["allowed_pools"] if x != name]
        await db.update_api_key(key_id, {"allowed_pools": allowed})
    save_config(config)
    restart_scheduler()
    _sync_pool()
    return {"ok": True}


@router.get("/keys/{key_id}/usage")
async def key_usage_history(key_id: int, date: str = "", _=Depends(verify_admin)):
    """某 API Key 的用量历史：按 1h 粒度返回指定日期(YYYY-MM-DD，默认今天)的 24 小时用量。"""
    from app.core import database as db
    from datetime import datetime
    from zoneinfo import ZoneInfo
    if not date:
        date = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    if key_id == 0:
        # #51 管理员（服务器密钥）伪 Key：无 api_keys 行，按 decision_log caller='管理员' 小时聚合（同形状返回）
        day_start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        hourly = await db.get_admin_hourly_usage(day_start.timestamp(), day_start.timestamp() + 86400)
        hours = [{"hour": h, "used": int(hourly.get(h, 0))} for h in range(24)]
        return {"key_id": 0, "date": date, "hours": hours, "total": sum(x["used"] for x in hours)}
    rec = await db.get_api_key_by_id(key_id)
    if not rec:
        raise HTTPException(status_code=404, detail=f"API Key #{key_id} 不存在")
    hourly = await db.get_hourly_usage(key_id, date)
    hours = [{"hour": h, "used": hourly.get(f"{h:02d}", 0)} for h in range(24)]
    return {"key_id": key_id, "date": date, "hours": hours, "total": sum(x["used"] for x in hours)}


@router.get("/keys/{key_id}/calls")
async def key_recent_calls(key_id: int, limit: int = 50, _=Depends(verify_admin)):
    """某 API Key 最近调用记录（v2.12.6：decision_log 按 caller=Key 名过滤，供用量历史面板展示）。"""
    from app.core import database as db
    if key_id == 0:
        # #51 管理员（服务器密钥）伪 Key：decision_log 里 caller 固定为「管理员」
        rows = await db.get_decisions(limit=min(limit, 50), caller="管理员")
        return {"key_id": 0, "key_name": "管理员（服务器密钥）", "calls": rows}
    rec = await db.get_api_key_by_id(key_id)
    if not rec:
        raise HTTPException(status_code=404, detail=f"API Key #{key_id} 不存在")
    rows = await db.get_decisions(limit=min(limit, 50), caller=rec["name"])
    return {"key_id": key_id, "key_name": rec["name"], "calls": rows}


@router.get("/ui_groups")
async def ui_groups_get(_=Depends(verify_admin)):
    """#54 分组收纳服务端同步：面板分组数据（providerGroups/poolGroups/settings）存 config.ui_groups，
    所有设备/浏览器共享（原仅存浏览器 localStorage，跨设备/跨源天然隔离——手机看不到电脑配的分组）。
    返回空 dict 表示未配置（前端回退 localStorage 并在首次保存时迁移上移）。"""
    config = load_config()
    g = config.get("ui_groups")
    return {"ui_groups": g if isinstance(g, dict) else {}}


@router.put("/ui_groups")
async def ui_groups_put(request: Request, _=Depends(verify_admin)):
    """保存分组数据（整对象覆盖；纯 UI 收纳数据，不影响路由/配额，无需 _sync_pool）。"""
    import json as _json
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if len(_json.dumps(body, ensure_ascii=False)) > 65536:
        raise HTTPException(status_code=400, detail="分组数据过大（上限 64KB）")
    config = load_config()
    config["ui_groups"] = body
    save_config(config)
    return {"ok": True}


@router.get("/headroom")
async def headroom_get(_=Depends(verify_admin)):
    """兼容别名：Headroom 已插件化，正式端点为 GET /admin/plugins/headroom（响应结构不变）。"""
    from app.plugins.installed.headroom import plugin as _hp
    return _hp.settings_payload()


@router.post("/headroom")
async def headroom_set(request: Request, _=Depends(verify_admin)):
    """兼容别名：Headroom 已插件化，正式端点为 POST /admin/plugins/headroom（请求结构不变）。"""
    from app.plugins.installed.headroom import plugin as _hp
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    return _hp.save_settings(body)


@router.get("/headroom/stats")
async def headroom_stats_view(days: int = 7, _=Depends(verify_admin)):
    """兼容别名：Headroom 已插件化，正式端点为 GET /admin/plugins/headroom/stats。"""
    from app.plugins.installed.headroom import plugin as _hp
    return await _hp.stats_view(days)


# ── 用户（预留：未来普通用户账号体系）──────────────────────────────────

@router.get("/users")
async def list_users(_=Depends(verify_admin)):
    from app.core import database as db
    return {"users": await db.list_users()}


@router.post("/users")
async def create_user(request: Request, _=Depends(verify_admin)):
    """预留接口：创建用户（当前仅管理员账号，普通用户体系待后续启用）"""
    from app.core import database as db
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = str(body.get("password") or "")
    role = body.get("role", "user")
    if not username or not password:
        raise HTTPException(status_code=400, detail="缺少 username / password")
    if role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="role 必须为 admin 或 user")
    import hashlib
    pwd_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
    uid = await db.create_user(username, pwd_hash, role)
    return {"ok": True, "id": uid}


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, _=Depends(verify_admin)):
    from app.core import database as db
    await db.delete_user(user_id)
    return {"ok": True}


# ── 余量监控工位（六平台余量/余额查询：MiniMax/智谱/小米MiMo/百度千帆/DeepSeek/OpenRouter）──
# 绑定 = 平台类型(5选1) × 凭证来源(从供应商选 / 手动填) + 平台专属人工字段（千帆 AK/SK、
# 小米 userId/passToken）。查询规格与刷新编排见 app/core/quota_providers.py；每次刷新后
# 全量状态覆盖写入仓库根 cheackresult.json，独立端口服务见 app/core/quota_server.py。

import json  # noqa: E402  （本区块 extra/payload 均为 JSON 串存储）

_QUOTA_SECRET_KEYS = {"api_key", "pass_token", "secret_key"}
_QUOTA_EXTRA_KEYS = {"cookie", "user_id", "pass_token", "access_key", "secret_key"}


def _quota_mask(v: str) -> str:
    """密钥脱敏：前 4…后 4；短值显示「已设置」。空串返回空。"""
    v = v or ""
    if not v:
        return ""
    if len(v) <= 12:
        return "已设置"
    return f"{v[:4]}…{v[-4:]}"


def _quota_binding_view(row: dict) -> dict:
    """绑定行 → 面板视图：密钥类字段脱敏（编辑弹窗「留空保持不变」），extra 解析回 dict。"""
    try:
        extra = json.loads(row.get("extra") or "{}")
    except Exception:
        extra = {}
    view_extra = {}
    for k, v in extra.items():
        view_extra[k] = _quota_mask(v) if k in _QUOTA_SECRET_KEYS and v else v
    return {
        "id": row["id"], "platform": row["platform"], "name": row["name"],
        "enabled": bool(row["enabled"]), "source": row["source"],
        "provider_id": row["provider_id"] or "",
        "api_key_masked": _quota_mask(row.get("api_key")) if row.get("source") == "manual" else "",
        "base_url": row["base_url"] or "", "extra": view_extra,
        "has_api_key": bool(row.get("api_key")),
        # 自定义脚本绑定：script_id 可回显；params_raw 含凭证不明文回传（留空=保持原值）
        "script_id": row.get("script_id") or "",
        "has_params": bool(row.get("params_raw")),
        "mimo_service_cookie_set": bool(row.get("mimo_service_cookie")),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def _quota_validate(body: dict, config: dict, *, creating: bool, old: dict | None = None) -> dict:
    """创建/更新的校验与规整。返回允许写库的字段 dict（extra 已 JSON 串化）。

    更新为「显式提交才覆盖」：source/provider_id/api_key 未提交时沿用旧行参与校验，
    api_key/extra 空值即保持原值（密码框「留空保持不变」）。"""
    from app.core.quota_providers import PLATFORMS
    out: dict = {}
    platform = str(body.get("platform") or (old or {}).get("platform") or "").strip()
    # 自定义脚本模式：不走内置平台枚举与 requires 校验——
    # script_id 必选，params_raw 原样落库不解析不过滤，由脚本自决取舍（执行器暂不实现）。
    if platform == "custom":
        out["platform"] = platform
        name = str(body.get("name") or "").strip()
        if creating and not name:
            raise HTTPException(status_code=400, detail="缺少绑定名称")
        if name:
            out["name"] = name
        source = str(body.get("source") or (old or {}).get("source") or "provider").strip()
        if source not in ("provider", "manual"):
            raise HTTPException(status_code=400, detail="source 必须为 provider 或 manual")
        if source != (old or {}).get("source", source) or "source" in body:
            out["source"] = source
        if "enabled" in body:
            out["enabled"] = 1 if body.get("enabled") else 0
        if "base_url" in body:
            out["base_url"] = str(body.get("base_url") or "").strip()
        script_id = str(body.get("script_id") or (old or {}).get("script_id") or "").strip()
        if not script_id:
            raise HTTPException(status_code=400, detail="自定义模式必须选择脚本")
        if script_id != (old or {}).get("script_id") or "script_id" in body:
            out["script_id"] = script_id
        if source == "provider":
            pid = str(body.get("provider_id") or (old or {}).get("provider_id") or "").strip()
            if not pid:
                raise HTTPException(status_code=400, detail="缺少 provider_id（请选择供应商）")
            if not any(p.get("id") == pid for p in config.get("providers", [])):
                raise HTTPException(status_code=404, detail=f"供应商 '{pid}' 不存在")
            if pid != (old or {}).get("provider_id") or "provider_id" in body:
                out["provider_id"] = pid
        # params_raw 原样落库：仅显式提交且非空才覆盖（留空 = 保持原值，与密钥框同惯例）
        raw = body.get("params_raw")
        if raw is not None and str(raw).strip() and str(raw) != (old or {}).get("params_raw"):
            out["params_raw"] = str(raw)
        return out
    if platform not in PLATFORMS:
        raise HTTPException(status_code=400,
                            detail=f"非法 platform '{platform}'：仅支持 {'/'.join(PLATFORMS)}")
    out["platform"] = platform
    meta = PLATFORMS[platform]
    name = str(body.get("name") or "").strip()
    if creating and not name:
        raise HTTPException(status_code=400, detail="缺少绑定名称")
    if name:
        out["name"] = name
    source = str(body.get("source") or (old or {}).get("source") or "provider").strip()
    if source not in ("provider", "manual"):
        raise HTTPException(status_code=400, detail="source 必须为 provider 或 manual")
    if source != (old or {}).get("source", source) or "source" in body:
        out["source"] = source
    if "enabled" in body:
        out["enabled"] = 1 if body.get("enabled") else 0
    if "base_url" in body:
        out["base_url"] = str(body.get("base_url") or "").strip()
    # api_key：手动来源必填（千帆例外——余额查询用 AK/SK，api_key 仅存档）；供应商来源忽略该字段
    api_key = str(body.get("api_key") or "").strip() or ((old or {}).get("api_key") or "")
    if source == "provider":
        pid = str(body.get("provider_id") or (old or {}).get("provider_id") or "").strip()
        if not pid:
            raise HTTPException(status_code=400, detail="缺少 provider_id（请选择供应商）")
        if not any(p.get("id") == pid for p in config.get("providers", [])):
            raise HTTPException(status_code=404, detail=f"供应商 '{pid}' 不存在")
        if pid != (old or {}).get("provider_id") or "provider_id" in body:
            out["provider_id"] = pid
    elif not api_key:
        if creating and platform != "qianfan":
            raise HTTPException(status_code=400, detail="手动来源必须填写 api_key")
        if not creating and platform != "qianfan":
            raise HTTPException(status_code=400, detail="手动来源 api_key 为空（原绑定也没有 Key），请填写")
    elif api_key != (old or {}).get("api_key"):
        out["api_key"] = str(body.get("api_key") or "").strip()
    # extra：平台专属人工字段（只接受白名单键；与旧行合并后按 requires 校验必填）
    extra_in = body.get("extra") or {}
    if not isinstance(extra_in, dict):
        raise HTTPException(status_code=400, detail="extra 必须为对象")
    old_extra = {}
    try:
        old_extra = json.loads((old or {}).get("extra") or "{}")
    except Exception:
        pass
    extra = {k: str(v).strip() for k, v in extra_in.items()
             if k in _QUOTA_EXTRA_KEYS and str(v).strip()}
    merged = {**old_extra, **extra}
    missing = [k for k in meta["requires"] if not merged.get(k)]
    if missing:
        raise HTTPException(status_code=400,
                            detail=f"平台 {meta['label']} 必须填写: {'/'.join(missing)}（{meta['note']}）")
    if extra:
        out["extra"] = json.dumps(merged, ensure_ascii=False)
    return out


@router.get("/quota/platforms")
async def quota_platforms(_=Depends(verify_admin)):
    from app.core.quota_providers import PLATFORMS
    return {"platforms": PLATFORMS}


@router.get("/quota/scripts")
async def quota_scripts(_=Depends(verify_admin)):
    """扫描 scripts/quota_providers/*.py 供自定义模式脚本下拉（文件名即 script_id）。
    目录不存在或为空返回空列表——自定义脚本机制落地后由脚本自动出现在下拉里。"""
    scripts_dir = REPO_DIR / "scripts" / "quota_providers"
    items = []
    if scripts_dir.is_dir():
        for f in sorted(scripts_dir.glob("*.py")):
            if f.name.startswith("_"):
                continue
            items.append({"id": f.stem, "file": f.name})
    return {"scripts": items}


@router.get("/quota/bindings")
async def quota_bindings_list(_=Depends(verify_admin)):
    from app.core.quota_providers import PLATFORMS
    config = load_config()
    provs = {p["id"]: p for p in config.get("providers", [])}
    out = []
    for row in await db.list_quota_with_snapshots():
        view = _quota_binding_view(row)
        prov = provs.get(view["provider_id"]) or {}
        view["provider_name"] = prov.get("name", "")
        result = row.get("payload") or {}
        view["snapshot"] = {
            "status": row.get("snap_status") or "pending",
            "plan": result.get("plan") or "",
            "bars": result.get("bars"),
            "balance": result.get("balance"), "symbol": result.get("symbol") or "",
            "sub": result.get("sub") or "", "available": result.get("available"),
            "error": row.get("snap_error") or "",
            "fetched_at": int(row.get("snap_fetched_at") or 0),
        }
        out.append(view)
    return {"bindings": out, "platforms": PLATFORMS,
            "providers": [{"id": p["id"], "name": p.get("name", ""), "protocol": p.get("protocol", "")}
                          for p in config.get("providers", [])]}


@router.post("/quota/bindings")
async def quota_binding_create(request: Request, _=Depends(verify_admin)):
    from app.core.quota_providers import refresh_binding, write_result_file
    body = await request.json()
    fields = _quota_validate(body, load_config(), creating=True)
    bid = await db.create_quota_binding(fields)
    row = await db.get_quota_binding(bid)
    result = await refresh_binding(row)  # 建完即查，所见即所得
    await write_result_file()
    return {"ok": True, "id": bid, "binding": _quota_binding_view(row), "result": result}


@router.put("/quota/bindings/{binding_id}")
async def quota_binding_update(binding_id: int, request: Request, _=Depends(verify_admin)):
    from app.core.quota_providers import refresh_binding, write_result_file
    row = await db.get_quota_binding(binding_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"绑定 #{binding_id} 不存在")
    body = await request.json()
    fields = _quota_validate(body, load_config(), creating=False, old=row)
    await db.update_quota_binding(binding_id, fields)
    row = await db.get_quota_binding(binding_id)
    result = await refresh_binding(row)
    await write_result_file()
    return {"ok": True, "binding": _quota_binding_view(row), "result": result}


@router.delete("/quota/bindings/{binding_id}")
async def quota_binding_delete(binding_id: int, _=Depends(verify_admin)):
    from app.core.quota_providers import write_result_file
    if not await db.delete_quota_binding(binding_id):
        raise HTTPException(status_code=404, detail=f"绑定 #{binding_id} 不存在")
    await write_result_file()
    return {"ok": True}


@router.post("/quota/bindings/{binding_id}/refresh")
async def quota_binding_refresh(binding_id: int, _=Depends(verify_admin)):
    from app.core.quota_providers import refresh_binding, write_result_file
    row = await db.get_quota_binding(binding_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"绑定 #{binding_id} 不存在")
    result = await refresh_binding(row)
    await write_result_file()
    return {"ok": True, "result": result}


@router.post("/quota/refresh")
async def quota_refresh_all(async_mode: str = "1", _=Depends(verify_admin)):
    """全部刷新：默认后台执行受理即返回（面板轮询取结果）；async_mode=0 时等待完成。"""
    from app.core.quota_providers import refresh_all
    if async_mode in ("0", "false"):
        results = await refresh_all(enabled_only=True)
        return {"ok": True, "results": results}
    asyncio.create_task(_quota_refresh_bg())
    return {"ok": True, "started": True}


async def _quota_refresh_bg():
    try:
        from app.core.quota_providers import refresh_all
        await refresh_all(enabled_only=True)
    except Exception:
        logging.getLogger(__name__).exception("[余量] 后台全量刷新失败")


def _quota_settings_view() -> dict:
    """quota_monitor 节点 → 面板设置视图（token 永不明文回传，只给 token_set）。"""
    cur = dict(load_config().get("quota_monitor") or {})
    try:
        port = int(cur.get("port", 8655) or 8655)
    except (TypeError, ValueError):
        port = 8655
    try:
        interval = int(cur.get("interval_minutes", 30) or 30)
    except (TypeError, ValueError):
        interval = 30
    return {"enabled": bool(cur.get("enabled", True)),
            "interval_minutes": interval,
            "circuit_enabled": bool(cur.get("circuit_enabled", True)),
            "server_enabled": bool(cur.get("server_enabled", True)),
            "host": cur.get("host") or "0.0.0.0",
            "port": port, "token_set": bool(cur.get("token") or "")}


@router.get("/quota/settings")
async def quota_settings_get(_=Depends(verify_admin)):
    return {"settings": _quota_settings_view()}


@router.put("/quota/settings")
async def quota_settings_put(request: Request, _=Depends(verify_admin)):
    body = await request.json()
    config = load_config()
    cur = dict(config.get("quota_monitor") or {})
    for k in ("enabled", "interval_minutes", "circuit_enabled", "server_enabled", "host", "port", "token"):
        if k in body:
            cur[k] = body[k]
    config["quota_monitor"] = cur
    save_config(config)
    restart_scheduler()  # 轮询开关/间隔即时生效
    server_changed = any(k in body for k in ("server_enabled", "host", "port", "token"))
    return {"ok": True, "settings": _quota_settings_view(),
            "note": "刷新端口的地址/开关/Token 修改需重启网关生效" if server_changed else ""}
