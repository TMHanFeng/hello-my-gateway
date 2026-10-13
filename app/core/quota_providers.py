# -*- coding: utf-8 -*-
"""余量监控工位：六平台余量/余额查询（MiniMax / 智谱 GLM / 小米 MiMo / 百度千帆 / DeepSeek / OpenRouter）。

查询规格逐字移植自 W:\\AIproject\\ai-quota-monitor（app/providers.py），关键口径：
- 每个查询函数返回归一化 dict：status(ok|error|unset) + plan + bars（进度条型）
  或 balance/symbol/sub（余额型），失败带中文 error 文案；
- MiniMax/智谱给的是「剩余百分比」，小米给的是 0~1「已用小数」，统一转为
  used_pct + remaining_pct 双值存储，前端只画不算；
- 时间戳三种形态（毫秒/秒/剩余时长）统一走 _epoch() 判别；
- 百度千帆余额用 BCE v1 签名（AK/SK，与千帆 API Key 无关），已与官方 bce-sdk 交叉验证；
- 小米未开放用 tp- Key 查额度：余量走账号会话（userId+passToken），service cookie
  302 登录链自动续期后由 refresh_binding 写回 quota_bindings 表。

本模块只允许 import app.core.* 与 httpx（仓库红线：不得模块级 import app.main）。
编排入口 refresh_binding / refresh_all 供 admin 路由、调度器与独立刷新端口共用；
每次刷新后 write_result_file() 把全量最新状态覆盖写入 RUNTIME_HOME/cheackresult.json
（固定文件名，覆盖不新建），供外部服务读取。
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from app.core import database as db
from app.core.config import load_config
from app.core.paths import QUOTA_RESULT_PATH

logger = logging.getLogger(__name__)

TIMEOUT = 15.0
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 ai-quota-monitor/1.0")

# 平台元数据：固定 6 个，前端弹窗按 requires/optional/note 渲染条件字段；
# requires 里的键存放在 bindings.extra（api_key 恒为独立字段，支持供应商/手动两种来源）。
PLATFORMS = {
    "minimax": {"label": "MiniMax", "kind": "bars", "requires": [], "optional": ["cookie"],
                "note": "api_key 即查询凭证；可选填网页 Cookie 兜底（Key 失效时使用）"},
    "zhipu": {"label": "智谱 GLM", "kind": "bars", "requires": [], "optional": [],
              "note": "api_key 即查询凭证（BigModel 平台 Key）"},
    "mimo": {"label": "小米 MiMo", "kind": "bars", "requires": ["user_id", "pass_token"], "optional": ["cookie"],
             "note": "小米未开放用 tp- Key 查额度：必须填小米账号 userId + passToken"
                     "（platform.xiaomimimo.com F12 → Cookies → account.xiaomi.com），程序自动续期"},
    "qianfan": {"label": "百度千帆", "kind": "balance", "requires": ["access_key", "secret_key"], "optional": [],
                "note": "查现金余额用百度智能云 AK/SK（控制台右上角 → 安全认证），非千帆 API Key；"
                        "api_key 仅存档不参与查询"},
    "deepseek": {"label": "DeepSeek", "kind": "balance", "requires": [], "optional": [],
                 "note": "api_key 即查询凭证（sk- 开头）"},
    "openrouter": {"label": "OpenRouter", "kind": "balance", "requires": [], "optional": [],
                   "note": "api_key 即查询凭证（sk-or- 开头）"},
}

DEFAULTS = {
    "minimax": {"host": "https://api.minimaxi.com"},
    "zhipu": {"base": "https://open.bigmodel.cn"},
    "mimo": {"base": "https://platform.xiaomimimo.com",
             "api_base": "https://token-plan-cn.xiaomimimo.com"},
    "openrouter": {"base": "https://openrouter.ai"},
}


# ── 通用小工具 ────────────────────────────────────────────────────────

def _res(status: str, **kw) -> dict:
    """统一返回结构（status + fetched_at 必带，其余按平台填充）。"""
    out = {"status": status, "plan": "", "bars": None, "balance": None,
           "symbol": "", "sub": "", "available": None, "error": "",
           "fetched_at": int(time.time())}
    out.update(kw)
    return out


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _epoch(v) -> float:
    """时间戳三形态归一为 unix 秒：>1e12 按毫秒；>1e9 按秒；否则视为「剩余时长」加到现在。"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    if v <= 0:
        return 0.0
    now = time.time()
    if v > 1e12:
        return v / 1000.0
    if v > 1e9:
        return v
    dur = v / 1000.0 if v > 1e6 else v
    return now + dur


def _fmt_credits(n: float) -> str:
    """大数中文缩写（≥1e8 亿 / ≥1e4 万），与参考实现一致。"""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    if n >= 1e8:
        v = n / 1e8
        return f"{v:.1f}".rstrip("0").rstrip(".") + " 亿"
    if n >= 1e4:
        v = n / 1e4
        return f"{v:.1f}".rstrip("0").rstrip(".") + " 万"
    if float(n).is_integer():
        return str(int(n))
    return f"{n:g}"


def _bar(label: str, used_pct: float, remaining_pct: float, total=0, used=0,
         detail: str = "", reset_at: float = 0.0) -> dict:
    return {"label": label, "total": total, "used": used,
            "used_pct": round(_clamp(used_pct, 0, 100), 1),
            "remaining_pct": round(_clamp(remaining_pct, 0, 100), 1),
            "detail": detail, "reset_at": int(reset_at)}


def _find_key(obj, key: str):
    """在任意层级递归查找键（千帆响应结构未完全公开，官方 SDK 也这么兜）。"""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


async def _get_json(client: httpx.AsyncClient, url: str, headers: dict) -> dict:
    """GET + JSON 解析，带统一错误文案（网络错误 / HTTP n / 非 JSON）。"""
    try:
        r = await client.get(url, headers=headers)
    except Exception as e:
        raise RuntimeError(f"网络错误: {e}") from e
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:150]}")
    try:
        return r.json()
    except Exception:
        raise RuntimeError("响应不是 JSON")


# ── MiniMax ───────────────────────────────────────────────────────────

def _mm_bar(label: str, entry: dict, total_key: str, used_key: str,
            pct_key: str, end_key: str, end_key2: str = "") -> dict | None:
    """MiniMax 单条进度条：以 *_remaining_percent 为准（剩余口径），计数仅兜底。"""
    total = int(entry.get(total_key) or 0)
    used = int(entry.get(used_key) or 0)
    pct = entry.get(pct_key)
    if pct is None:
        if total <= 0:
            return None
        used_pct = used / total * 100 if total else 0
    else:
        try:
            remaining = _clamp(float(pct), 0, 100)
        except (TypeError, ValueError):
            return None
        if remaining >= 100 and total == 0:
            return None  # 空占位条目
        used_pct = 100 - remaining
    reset_at = _epoch(entry.get(end_key) or (entry.get(end_key2) if end_key2 else 0))
    detail = f"已用 {used} / {total}" if total > 0 and used <= total else ""
    return _bar(label, used_pct, 100 - used_pct, total, used, detail, reset_at)


def _parse_minimax_plan(data: dict) -> dict:
    plan = ""
    for k in ("current_subscribe_title", "plan_name", "combo_title", "current_plan_title"):
        if data.get(k):
            plan = str(data[k])
            break
    entries = data.get("model_remains") or []
    if isinstance(entries, dict):
        entries = [entries]
    bars = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        name = str(e.get("model_name") or "").lower()
        prefix = "视频赠送" if name == "video" else ""
        for label, keys in (
            ("5 小时窗口", ("current_interval_total_count", "current_interval_usage_count",
                            "current_interval_remaining_percent", "end_time", "remains_time")),
            ("每周额度", ("current_weekly_total_count", "current_weekly_usage_count",
                          "current_weekly_remaining_percent", "weekly_end_time", "weekly_remains_time")),
        ):
            b = _mm_bar(f"{prefix}{label}".strip(), e, *keys)
            if b:
                bars.append(b)
    order = {"5 小时窗口": 0, "每周额度": 1}
    bars.sort(key=lambda b: order.get(b["label"], 9))
    return {"plan": plan or "MiniMax Plan", "bars": bars}


async def fetch_minimax(cred: dict) -> dict:
    api_key = cred.get("api_key") or ""
    cookie = cred.get("extra", {}).get("cookie") or ""
    if not api_key and not cookie:
        return _res("unset", error="未配置 API Key")
    host = (cred.get("base_url") or DEFAULTS["minimax"]["host"]).rstrip("/")
    headers = {"accept": "application/json", "content-type": "application/json",
               "user-agent": UA}
    endpoints = [f"{host}/v1/token_plan/remains",
                 f"{host}/v1/api/openplatform/coding_plan/remains"]
    last_err = ""
    if api_key:
        h = {**headers, "Authorization": f"Bearer {api_key}"}
        async with httpx.AsyncClient(timeout=TIMEOUT, proxy=cred.get("proxy") or None) as client:
            for url in endpoints:
                try:
                    j = await _get_json(client, url, h)
                except RuntimeError as e:
                    last_err = str(e)
                    continue
                base = j.get("base_resp") or {}
                code = base.get("status_code")
                if code not in (None, 0):
                    last_err = f"{code}: {base.get('status_msg', '')}"
                    continue
                data = j.get("data") if isinstance(j.get("data"), dict) else j
                parsed = _parse_minimax_plan(data if isinstance(data, dict) else {})
                return _res("ok", **parsed)
    # Key 缺失或全部端点失败 → Cookie 兜底
    if cookie:
        h = {"accept": "application/json, text/plain, */*", "user-agent": UA,
             "cookie": cookie, "x-requested-with": "XMLHttpRequest", "referer": f"{host}/"}
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, proxy=cred.get("proxy") or None) as client:
                j = await _get_json(client, endpoints[1], h)
            data = j.get("data") if isinstance(j.get("data"), dict) else j
            parsed = _parse_minimax_plan(data if isinstance(data, dict) else {})
            if parsed["bars"]:
                return _res("ok", **parsed)
            return _res("error", error="Cookie 已失效或无数据")
        except RuntimeError as e:
            last_err = f"{e}（Cookie 可能已失效）"
    return _res("error", error=last_err or "查询失败")


# ── 智谱 GLM ──────────────────────────────────────────────────────────

def _zhipu_bars(limits) -> list:
    bars = []
    for item in limits:
        if not isinstance(item, dict):
            continue
        try:
            used_pct = _clamp(float(item.get("percentage", 0) or 0), 0, 100)
        except (TypeError, ValueError):
            continue
        itype = str(item.get("type") or "")
        unit = item.get("unit")
        number = item.get("number")
        label = "周期额度"
        if itype == "CREDIT_LIMIT":
            if unit == 3:
                label = f"{number or 5} 小时窗口"
            elif unit == 6:
                label = "每周额度" if (number is None or number in (0, 1)) else f"{number} 周额度"
        elif itype == "TOKENS_LIMIT":
            label = "5 小时窗口"
        elif itype == "TIME_LIMIT":
            label = "MCP 月度"
        elif "WEEK" in itype.upper():
            label = "每周额度"
        usage = item.get("usage") or 0
        cur = item.get("currentValue") or 0
        detail = f"已用 {cur} / {usage}" if usage else ""
        bars.append(_bar(label, used_pct, 100 - used_pct, usage, cur, detail,
                         _epoch(item.get("nextResetTime"))))
    order = {"5 小时窗口": 0, "每周额度": 1}
    bars.sort(key=lambda b: order.get(b["label"], 9))
    return bars


async def fetch_zhipu(cred: dict) -> dict:
    api_key = cred.get("api_key") or ""
    if not api_key:
        return _res("unset", error="未配置 API Key")
    base = (cred.get("base_url") or DEFAULTS["zhipu"]["base"]).rstrip("/")
    url = f"{base}/api/monitor/usage/quota/limit"
    async with httpx.AsyncClient(timeout=TIMEOUT, proxy=cred.get("proxy") or None) as client:
        j = None
        last = ""
        for auth in (api_key, f"Bearer {api_key}"):  # 裸 Key 优先；401/403 自动带 Bearer 重试一次
            try:
                j = await _get_json(client, url, {"accept": "application/json",
                                                  "user-agent": UA, "Authorization": auth})
                break
            except RuntimeError as e:
                last = str(e)
                if "HTTP 401" not in last and "HTTP 403" not in last:
                    return _res("error", error=last)
                j = None
        if j is None:
            return _res("error", error=last)
    data = j.get("data") if isinstance(j.get("data"), (dict, list)) else j
    limits = None
    if isinstance(data, dict):
        limits = data.get("limits")
        if not limits and isinstance(data.get("data"), dict):
            limits = data["data"].get("limits")
    elif isinstance(data, list):
        limits = data
    bars = _zhipu_bars(limits or [])
    return _res("ok", plan="GLM Coding Plan", bars=bars)


# ── 小米 MiMo ─────────────────────────────────────────────────────────

def _mimo_pct(item: dict) -> tuple[float, float]:
    """小米 percent 是 0~1 已用小数（防御兼容已是百分数的旧值）；优先 used/limit 反推。"""
    used = item.get("used") or 0
    limit = item.get("limit") or 0
    if limit:
        used_pct = used / limit * 100
    else:
        p = item.get("percent") or 0
        try:
            p = float(p)
        except (TypeError, ValueError):
            p = 0
        used_pct = p * 100 if 0 <= p <= 1 else p
    return _clamp(used_pct, 0, 100), 100 - _clamp(used_pct, 0, 100)


def _parse_mimo_usage(data: dict) -> dict:
    plan = data.get("planName") or data.get("plan_name") or data.get("plan_title") or "MiMo Token Plan"
    usage = data.get("usage") or {}
    items = usage.get("items") or []
    name_map = {"plan_total_token": "套餐额度", "compensation_total_token": "补偿额度"}
    bars = []
    for it in items:
        if not isinstance(it, dict):
            continue
        label = name_map.get(it.get("name"), it.get("name") or "额度")
        used_pct, remaining_pct = _mimo_pct(it)
        used, limit = it.get("used") or 0, it.get("limit") or 0
        bars.append(_bar(label, used_pct, remaining_pct, limit, used,
                         f"已用 {_fmt_credits(used)} / {_fmt_credits(limit)} Credits"))
    if not bars and usage.get("percent") is not None:
        try:
            p = float(usage["percent"])
            used_pct = p * 100 if 0 <= p <= 1 else p
            bars.append(_bar("套餐额度", used_pct, 100 - used_pct))
        except (TypeError, ValueError):
            pass
    return {"plan": plan, "bars": bars}


def _mimo_harvest_cookies(session: httpx.AsyncClient) -> str:
    """从 session 收割整组 platform Cookie：api-platform_* 的值带双引号拼回、userId 不带。"""
    parts = []
    user_id = ""
    for cookie in session.cookies.jar:
        domain = cookie.domain or ""
        if "xiaomimimo" not in domain:
            if domain.endswith("xiaomi.com") and cookie.name == "userId":
                user_id = cookie.value or ""
            continue
        if cookie.name.startswith("api-platform_"):
            parts.append(f'{cookie.name}="{(cookie.value or "").strip(chr(34))}"')
        elif cookie.name == "userId":
            user_id = cookie.value or ""
    if user_id:
        parts.append(f"userId={user_id}")
    return "; ".join(parts)


def _mimo_pick_new_pass_token(session: httpx.AsyncClient, old: str) -> str:
    for cookie in session.cookies.jar:
        if (cookie.domain or "").endswith("xiaomi.com") and cookie.name == "passToken":
            if cookie.value and cookie.value != old:
                return cookie.value
    return ""


async def _mimo_query(client: httpx.AsyncClient, base: str, cookie: str) -> dict:
    """查询 usage。返回原始 JSON；会话失效抛 _MimoExpired。"""
    r = await client.get(f"{base}/api/v1/tokenPlan/usage",
                         headers={"accept": "application/json", "user-agent": UA, "cookie": cookie})
    if r.status_code in (401, 403):
        raise _MimoExpired()
    try:
        j = r.json()
    except Exception:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:120]}")
    code = j.get("code")
    if code == 401:
        raise _MimoExpired()
    if code not in (0, 200, None):
        raise RuntimeError(str((j.get("description") or j))[:80])
    return j.get("data") or {}


class _MimoExpired(Exception):
    pass


async def _mimo_renew(base: str, user_id: str, pass_token: str) -> tuple[str, str]:
    """重放 302 登录链换发整组 platform Cookie。返回 (新 service_cookie, 新 passToken)。

    实测要点（照抄参考实现）：必须带 account.xiaomi.com 域的 userId/passToken 走完整链；
    链尾 307 直接落回 usage 接口并在 platform 域 Set-Cookie 整组 api-platform_* 凭证；
    单独 serviceToken 会被 401，必须整组一起用。"""
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True,
                                 proxy=None, headers={"user-agent": UA,
                                                      "accept": "application/json, text/html, */*"}) as s:
        s.cookies.set("userId", str(user_id), domain="account.xiaomi.com")
        s.cookies.set("passToken", pass_token, domain="account.xiaomi.com")
        try:
            r = await s.get(f"{base}/api/v1/tokenPlan/usage",
                            headers={"accept": "application/json", "user-agent": UA})
        except Exception as e:
            raise RuntimeError(f"网络错误: {e}") from e
        if r.status_code in (401, 403):
            login_url = ""
            try:
                login_url = (r.json() or {}).get("loginUrl") or ""
            except Exception:
                pass
            if not login_url:
                raise RuntimeError(f"passToken 已失效或登录被拦截（HTTP {r.status_code}）")
            try:
                await s.get(login_url)  # allow_redirects=True：302/307 链自动落整组 Cookie
            except Exception as e:
                raise RuntimeError(f"登录链跟随失败: {e}") from e
        service_cookie = _mimo_harvest_cookies(s)
        if "api-platform_serviceToken" not in service_cookie:
            raise RuntimeError("passToken 已失效或登录被拦截（未换发到 serviceToken）")
        new_pass = _mimo_pick_new_pass_token(s, pass_token)
        return service_cookie, new_pass


async def fetch_mimo(cred: dict) -> dict:
    """小米 MiMo 余量。凭证优先级：user_id+pass_token（自动续期）→ cookie → 仅 Key（只校验）。

    返回值可能带 new_service_cookie / new_pass_token（由 refresh_binding 写回绑定表）。"""
    extra = cred.get("extra", {})
    api_key = cred.get("api_key") or ""
    user_id = extra.get("user_id") or ""
    pass_token = extra.get("pass_token") or ""
    manual_cookie = (extra.get("cookie") or "").removeprefix("cookie:").strip()
    base = (cred.get("base_url") or DEFAULTS["mimo"]["base"]).rstrip("/")
    service_cookie = cred.get("service_cookie") or ""

    if user_id and pass_token:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True,
                                     proxy=cred.get("proxy") or None) as client:
            try:
                if service_cookie:
                    try:
                        data = await _mimo_query(client, base, service_cookie)
                        return _res("ok", **_parse_mimo_usage(data))
                    except (_MimoExpired, RuntimeError):
                        pass  # 会话失效 → 走续期
                new_cookie, new_pass = await _mimo_renew(base, user_id, pass_token)
                data = await _mimo_query(client, base, new_cookie)
                out = _res("ok", **_parse_mimo_usage(data))
                out["new_service_cookie"] = new_cookie
                if new_pass:
                    out["new_pass_token"] = new_pass
                return out
            except _MimoExpired:
                return _res("error", error="passToken 已失效或登录被拦截")
            except RuntimeError as e:
                # 续期失败时若手动 Cookie 兜底可用则再试一次
                if manual_cookie:
                    try:
                        data = await _mimo_query(client, base, manual_cookie)
                        out = _res("ok", **_parse_mimo_usage(data))
                        out["note"] = "账号自动续期失败，当前为手动 Cookie 兜底"
                        return out
                    except (_MimoExpired, RuntimeError):
                        pass
                return _res("error", error=f"{e}；账号自动续期失败" if "passToken" in str(e) else str(e))
    if manual_cookie:
        async with httpx.AsyncClient(timeout=TIMEOUT, proxy=cred.get("proxy") or None) as client:
            try:
                data = await _mimo_query(client, base, manual_cookie)
                return _res("ok", **_parse_mimo_usage(data))
            except _MimoExpired:
                return _res("error", error="Cookie 已失效，请到控制台重新复制")
            except RuntimeError as e:
                return _res("error", error=str(e))
    # 仅 Key：小米未开放用 Key 查额度，只能校验 Key 有效性
    if api_key:
        api_base = DEFAULTS["mimo"]["api_base"]
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, proxy=cred.get("proxy") or None) as client:
                r = await client.get(f"{api_base}/v1/models",
                                     headers={"accept": "application/json", "user-agent": UA,
                                              "Authorization": f"Bearer {api_key}"})
            if r.status_code == 200:
                return _res("unset", error="Key 有效；查余量请在编辑绑定里填写小米账号 userId + passToken")
            return _res("error", error=f"API Key 无效（HTTP {r.status_code}）")
        except Exception as e:
            return _res("error", error=f"网络错误: {e}")
    return _res("unset", error="未配置凭证：请填写小米账号 userId + passToken（或控制台 Cookie）")


# ── 百度千帆（BCE v1 签名）────────────────────────────────────────────

_BCE_SAFE = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.~-_")


def _bce_normalize(v: str) -> str:
    out = []
    for b in str(v).encode("utf-8"):
        ch = chr(b)
        out.append(ch if ch in _BCE_SAFE else f"%{b:02X}")
    return "".join(out)


def bce_authorization(ak: str, sk: str, method: str, path: str, host: str,
                      ct: str, expire: int = 1800) -> str:
    """BCE v1 签名（与官方 bce-sdk-python 的 bce_v1_signer 交叉验证一致）。

    ct 为 UTC 时间 "%Y-%m-%dT%H:%M:%SZ"（调用方保证与请求头 x-bce-date 完全一致）。"""
    prefix = f"bce-auth-v1/{ak}/{ct}/{expire}"
    sign_key = __import__("hmac").new(sk.encode(), prefix.encode(), "sha256").hexdigest()
    signed = ";".join(sorted(["host", "x-bce-date"]))
    canonical_headers = f"host:{_bce_normalize(host)}\nx-bce-date:{_bce_normalize(ct)}"
    string_to_sign = f"{method.upper()}\n{path}\n\n{canonical_headers}"
    import hmac as _hmac
    signature = _hmac.new(sign_key.encode(), string_to_sign.encode(), "sha256").hexdigest()
    return f"{prefix}/{signed}/{signature}"


async def fetch_qianfan(cred: dict) -> dict:
    extra = cred.get("extra", {})
    ak = extra.get("access_key") or ""
    sk = extra.get("secret_key") or ""
    if not ak or not sk:
        return _res("unset", error="未配置 AK/SK（百度智能云控制台右上角 → 安全认证）")
    host, path = "billing.baidubce.com", "/v1/finance/cash/balance"
    ct = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    auth = bce_authorization(ak, sk, "POST", path, host, ct)
    headers = {"host": host, "x-bce-date": ct,  # x-bce-date 必须与签名的 ct 一致
               "Content-Type": "application/json; charset=utf-8", "Authorization": auth,
               "user-agent": UA}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, proxy=cred.get("proxy") or None) as client:
            r = await client.post(f"https://{host}{path}", content=b"{}", headers=headers)
    except Exception as e:
        return _res("error", error=f"网络错误: {e}")
    if r.status_code != 200:
        hint = "（若 AK/SK 无误请检查服务器时钟偏差，签名有效期 30 分钟）" if r.status_code == 401 else ""
        return _res("error", error=f"HTTP {r.status_code}: {r.text[:120]}{hint}")
    try:
        j = r.json()
    except Exception:
        return _res("error", error="响应不是 JSON")
    val = _find_key(j, "cashBalance")
    if val is None:
        return _res("error", error=f"响应缺少 cashBalance: {r.text[:120]}")
    try:
        balance = float(val)
    except (TypeError, ValueError):
        return _res("error", error=f"cashBalance 不是数字: {val}")
    return _res("ok", balance=balance, symbol="¥", sub="现金余额")


# ── DeepSeek ──────────────────────────────────────────────────────────

async def fetch_deepseek(cred: dict) -> dict:
    api_key = cred.get("api_key") or ""
    if not api_key:
        return _res("unset", error="未配置 API Key")
    base = (cred.get("base_url") or "https://api.deepseek.com").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, proxy=cred.get("proxy") or None) as client:
            j = await _get_json(client, f"{base}/user/balance",
                                {"accept": "application/json", "user-agent": UA,
                                 "Authorization": f"Bearer {api_key}"})
    except RuntimeError as e:
        return _res("error", error=str(e))
    infos = j.get("balance_infos") or []
    if not infos:
        return _res("error", error="响应缺少 balance_infos")
    info = infos[0]
    currency = info.get("currency", "")
    symbol = {"CNY": "¥", "USD": "$"}.get(currency, f"{currency} " if currency else "")
    balance = float(info.get("total_balance") or 0)
    sub_parts = []
    if info.get("granted_balance"):
        sub_parts.append(f"赠送 {symbol}{info['granted_balance']}")
    if info.get("topped_up_balance"):
        sub_parts.append(f"充值 {symbol}{info['topped_up_balance']}")
    return _res("ok", balance=balance, symbol=symbol,
                sub=" · ".join(sub_parts) if sub_parts else "总余额",
                available=bool(j.get("is_available", True)))


# ── OpenRouter ────────────────────────────────────────────────────────

def _fmt_usd(v: float) -> str:
    """美元金额显示：最多 6 位小数并去尾零（0.000017 → 0.000017，1.5 → 1.5）。"""
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s or "0"


def parse_openrouter_credits(j: dict) -> dict:
    """解析 /api/v1/credits 响应（独立函数便于离线回归）：
    {"data":{"total_credits":10,"total_usage":0.000017}} → 余额 = 充值总额 − 已用。"""
    data = j.get("data")
    if not isinstance(data, dict):
        raise ValueError(f"响应缺少 data: {j}")
    if data.get("total_credits") is None:
        raise ValueError(f"响应缺少 total_credits: {j}")
    total = float(data["total_credits"] or 0)
    used = float(data.get("total_usage") or 0)
    balance = round(total - used, 6)
    sub = f"充值 ${_fmt_usd(total)} · 已用 ${_fmt_usd(used)}"
    return _res("ok", plan="OpenRouter Credits", balance=balance, symbol="$", sub=sub)


async def fetch_openrouter(cred: dict) -> dict:
    api_key = cred.get("api_key") or ""
    if not api_key:
        return _res("unset", error="未配置 API Key")
    base = (cred.get("base_url") or DEFAULTS["openrouter"]["base"]).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, proxy=cred.get("proxy") or None) as client:
            j = await _get_json(client, f"{base}/api/v1/credits",
                                {"accept": "application/json", "user-agent": UA,
                                 "Authorization": f"Bearer {api_key}"})
    except RuntimeError as e:
        return _res("error", error=str(e))
    try:
        return parse_openrouter_credits(j)
    except ValueError as e:
        return _res("error", error=str(e))


FETCHERS = {"minimax": fetch_minimax, "zhipu": fetch_zhipu, "mimo": fetch_mimo,
            "qianfan": fetch_qianfan, "deepseek": fetch_deepseek,
            "openrouter": fetch_openrouter}


# ── 编排：刷新一条 / 全部 / 结果文件 ──────────────────────────────────

def resolve_credential(binding: dict, config: dict | None = None) -> tuple[dict, str]:
    """把绑定行解析为查询凭证字典。返回 (cred, error)；provider 来源实时取 config，
    供应商被删报错——Key 与供应商始终同步，不做入库时快照。"""
    config = config or load_config()
    extra = {}
    try:
        extra = json.loads(binding.get("extra") or "{}")
    except Exception:
        pass
    cred = {"api_key": "", "base_url": binding.get("base_url") or "",
            "extra": extra, "proxy": "", "service_cookie": binding.get("mimo_service_cookie") or ""}
    if binding.get("source") == "provider":
        pid = binding.get("provider_id") or ""
        prov = next((p for p in config.get("providers", []) if p.get("id") == pid), None)
        if not prov:
            return cred, f"绑定的供应商 '{pid}' 已删除，请编辑绑定重新选择"
        cred["api_key"] = prov.get("api_key") or ""
        cred["proxy"] = prov.get("proxy_url") or ""  # 余量查询跟随供应商代理（境外端点场景）
    else:
        cred["api_key"] = binding.get("api_key") or ""
    return cred, ""


async def refresh_binding(binding: dict) -> dict:
    """查询单条绑定并落库（快照 UPSERT + 历史 APPEND + 小米凭证回写）。永不抛异常。"""
    bid = binding["id"]
    platform = binding.get("platform") or ""
    fetcher = FETCHERS.get(platform)
    try:
        if platform == "custom":
            # 自定义脚本绑定（v2.22.5）：凭证与参数已就位（resolve_credential 实时取 provider key），
            # 执行器属 M6（spawn 子进程跑 scripts/quota_providers/<script_id>.py），未落地前明确报待启用。
            cred, err = resolve_credential(binding)
            if err:
                result = _res("error", error=err)
            else:
                result = _res("unset", error=f"自定义脚本「{binding.get('script_id') or '?'}」执行器未启用，"
                                             f"请把脚本放入 scripts/quota_providers/ 并等待自定义机制开启")
        elif not fetcher:
            result = _res("error", error=f"未知平台类型: {platform}")
        else:
            cred, err = resolve_credential(binding)
            if err:
                result = _res("error", error=err)
            else:
                result = await fetcher(cred)
    except Exception as e:
        result = _res("error", error=str(e))
    # 小米续期凭证写回（对应参考实现的 _persist_new_state）
    new_cookie = result.pop("new_service_cookie", None)
    new_pass = result.pop("new_pass_token", None)
    if new_cookie or new_pass:
        try:
            await db.set_mimo_credentials(bid, new_cookie or "", new_pass or "")
        except Exception:
            logger.exception("[余量] 小米凭证回写失败")
    try:
        await db.upsert_quota_snapshot(bid, result["status"], result.get("plan") or "",
                                       result, result.get("error") or "")
        await db.append_quota_history(bid, result["status"], result, result["fetched_at"])
    except Exception:
        logger.exception(f"[余量] 绑定 #{bid} 结果落库失败")
    return result


async def refresh_all(enabled_only: bool = False) -> list[dict]:
    """并发刷新全部绑定（单绑定异常已兜底为 error，互不影响）。"""
    bindings = await db.list_quota_bindings()
    if enabled_only:
        bindings = [b for b in bindings if b.get("enabled")]
    if not bindings:
        await write_result_file()
        return []
    results = await asyncio.gather(*(refresh_binding(b) for b in bindings))
    await write_result_file()
    return list(results)


# ── 供应商熔断（v2.22.5）：余量耗尽 → 调度 fail-close，按平台 reset_at 自动恢复 ──

def circuit_enabled(config: dict | None = None) -> bool:
    """熔断联动开关（config quota_monitor.circuit_enabled，默认开）。load_config 带 mtime 缓存，热路径零 IO。"""
    config = config or load_config()
    return bool((config.get("quota_monitor") or {}).get("circuit_enabled", True))


async def provider_circuit(provider_id: str) -> dict | None:
    """惰性推导供应商是否被余量熔断（零状态表：每次从 quota_snapshots 重推，重启自然恢复）。

    判定口径（仅 source=provider 且 enabled=1 的绑定，任一命中即熔断；多绑定取最晚恢复时刻）：
    - bars 型：任一条 used_pct>=100 且 reset_at>now → 熔断到该 reset_at（多条取 max）；
    - balance 型：balance<=0 → 熔断至下次余量刷新回升（轮询即自愈，reset_at=0 表示无固定时刻）；
    - error/unset/pending 不熔断（无证据不误杀，Q5 fail-close 指已确认耗尽时拒绝）。
    返回 {"reset_at", "binding", "bar", "detail"} 或 None=不熔断。"""
    if not provider_id or not circuit_enabled():
        return None
    now = time.time()
    hits: list[tuple[float, str, str, str]] = []
    for row in await db.list_quota_with_snapshots():
        if not row.get("enabled") or row.get("source") != "provider" \
                or (row.get("provider_id") or "") != provider_id \
                or (row.get("snap_status") or "pending") != "ok":
            continue
        payload = row.get("payload")  # list_quota_with_snapshots 已把 snap_payload 解析成 dict
        if not isinstance(payload, dict):
            continue
        name = row.get("name") or ""
        bars = payload.get("bars") or []
        for b in bars:
            try:
                used_pct = float(b.get("used_pct") or 0)
                reset_at = float(b.get("reset_at") or 0)
            except (TypeError, ValueError):
                continue
            if used_pct >= 100 and reset_at > now:
                label = b.get("label") or "额度"
                hits.append((reset_at, name, label, f"「{label}」已用尽"))
        bal = payload.get("balance")
        if bal is not None and not bars:
            try:
                if float(bal) <= 0:
                    hits.append((0.0, name, "", "账户余额已耗尽，等待下次余量刷新后自动恢复"))
            except (TypeError, ValueError):
                continue
    if not hits:
        return None
    hits.sort(key=lambda h: h[0], reverse=True)  # 最晚恢复时刻代表整组（bars 恒排前：reset_at>0）
    reset_at, binding, bar, detail = hits[0]
    return {"reset_at": reset_at, "binding": binding, "bar": bar, "detail": detail}


def _bj_str(ts: float) -> str:
    return datetime.fromtimestamp(ts, ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")


async def build_result_document() -> dict:
    """全量最新状态（绑定 + 快照 join），即 cheackresult.json 的内容。绝不含任何密钥。"""
    rows = await db.list_quota_with_snapshots()
    bindings = []
    for r in rows:
        # list_quota_with_snapshots 返回的 payload 已是解析后的 dict（坑实录 #1）：
        # 二次 json.loads 会 TypeError 被吞成空对象 → 结果文件 bars/balance/plan 全 null
        # （仅 status/error 列幸存）。源仓 v2.22.8 带此 bug，本仓移植版修正。
        payload = r.get("payload")
        result = payload if isinstance(payload, dict) else {}
        item = {
            "id": r["id"], "platform": r["platform"],
            "platform_label": PLATFORMS.get(r["platform"], {}).get("label", r["platform"]),
            "kind": PLATFORMS.get(r["platform"], {}).get("kind", "bars"),
            "name": r["name"], "source": r["source"],
            "provider_id": r["provider_id"] or "", "enabled": bool(r["enabled"]),
            "status": r["snap_status"] or "pending", "plan": result.get("plan") or "",
            "bars": result.get("bars"), "balance": result.get("balance"),
            "symbol": result.get("symbol") or "", "sub": result.get("sub") or "",
            "available": result.get("available"), "error": r["snap_error"] or "",
            "fetched_at": int(r["snap_fetched_at"] or 0),
            "fetched_at_str": _bj_str(r["snap_fetched_at"]) if r["snap_fetched_at"] else "",
        }
        bindings.append(item)
    now = time.time()
    return {"service": "hello-my-gateway quota monitor",
            "generated_at": int(now), "generated_at_str": _bj_str(now),
            "count": len(bindings), "bindings": bindings}


async def write_result_file() -> dict:
    """把全量最新状态覆盖写入 RUNTIME_HOME/cheackresult.json（固定文件名，原子替换，
    每次刷新都覆盖而非新建）。返回写入的文档。"""
    doc = await build_result_document()
    tmp = Path(str(QUOTA_RESULT_PATH) + ".tmp")
    try:
        QUOTA_RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(QUOTA_RESULT_PATH))
    except Exception:
        logger.exception("[余量] cheackresult.json 写入失败")
    return doc


async def read_result_document() -> dict:
    """读取当前结果文件；不存在则先构建落盘再返回。"""
    try:
        return json.loads(QUOTA_RESULT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return await write_result_file()
