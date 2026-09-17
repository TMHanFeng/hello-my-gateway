"""百度千帆「网页搜索」(qianfan_web_search) 结果的 AI 总结 —— 两步式的第 2 步。

只服务 qianfan_web_search：该上游只回 {request_id, references}，网关合成的正文是裸列表。
第 2 步把「用户问题 + references 全部可解析字段 +（可选）抓取到的网页正文」交给一个
LLM 池做一次总结，用总结文本替换 content，references 原样保留。

关键约束：
- 总结调用直接复用 pool.execute_with_fallback / execute_stream_with_fallback，
  因此 429 冷却、切换候选、fallback_pool 链、并发槽、模型用量入账与安全阀全部白拿；
- 内层调用一律传 allow_search_summary=False，结构上杜绝递归；
- 本模块任何失败都只返回 None，由调用方降级为「原始结果列表」，绝不让搜索请求本身失败。
"""
from __future__ import annotations

import json
import logging
import re

import database as db
import keyauth
import search_sse
from web_fetch import fetch_pages

logger = logging.getLogger(__name__)

# 全局默认（config.json 顶层 search_summary 块可覆盖）
DEFAULTS = {
    "max_fetch_urls": 8,              # 0 = 完全不抓 URL（只用 references 自带正文）
    "fetch_concurrency": 5,
    "fetch_timeout_seconds": 8,
    "fetch_total_timeout_seconds": 20,
    "max_bytes": 524288,              # 单页最大下载字节
    "max_chars_per_page": 1200,       # 单页正文截断
    "max_total_chars": 12000,         # 送进 LLM 的资料总量上限
    "fetch_proxy_url": "",
    "block_private_hosts": True,
    # 专门网关 Key（完整 mg-… 值）：内层总结调用的记录（caller）与用量都挂到它名下，
    # 不混入终端用户的「最近调用记录」；空 = 沿用发起搜索请求的原 caller（旧行为）
    "caller_key": "",
}

# references 里对总结无价值、且容易污染提示词的字段
_SKIP_KEYS = {"icon", "web_anchor", "markdown_content", "video", "image", "aladdin",
              "web_extensions", "rerank_score", "is_aladdin", "id", "type", "snippet"}
# 正文候选字段（优先"抓取到的网页正文"，其次 references 自带）
_TEXT_KEYS = ("content", "snippet", "summary", "text", "desc", "abstract")

SYSTEM_PROMPT = (
    "你是一个搜索问答助手。请只依据下方提供的「搜索结果」回答用户问题："
    "不要编造资料中没有的信息，资料不足时明确说明；"
    "引用来源时在句末用 [序号] 标注。直接给出答案，不要复述资料原文。"
)


def cfg_of(pool_obj) -> dict:
    raw = (getattr(pool_obj, "config", None) or {}).get("search_summary") or {}
    out = dict(DEFAULTS)
    for k, v in raw.items():
        if v is not None:
            out[k] = v
    return out


async def dedicated_key(pool_obj) -> dict | None:
    """解析专门记账 Key（config.search_summary.caller_key = 完整 Key 值）。

    命中时内层总结调用的调用记录（decision_log.caller）与 Key 用量都挂到该 Key 名下；
    未配置或 Key 已删除/轮换过期返回 None（沿用原 caller），绝不让记账影响总结本身。
    """
    secret = str(cfg_of(pool_obj).get("caller_key") or "").strip()
    if not secret:
        return None
    try:
        return await db.get_api_key_by_secret_or_previous(secret)
    except Exception as e:
        logger.warning(f"[搜索总结] 专门 Key 解析失败，沿用原 caller: {type(e).__name__}: {str(e)[:120]}")
        return None


async def _charge_dedicated(dk: dict | None, tokens: int):
    """内层总结用量挂账到专门 Key（语义与用户 Key 直连一致：按次计 1 / 按 token 计真实值；
    管理员 Key、未设限额自动忽略——与 charge_key_usage 行为对齐；任何失败只记日志）。"""
    if dk is None:
        return
    try:
        amount = 1 if dk.get("billing_mode") == "request" else int(tokens or 0)
        if amount > 0:
            await keyauth.charge_key_usage(dk, amount)
    except Exception as e:
        logger.warning(f"[搜索总结] 专门 Key 记账失败: {type(e).__name__}: {str(e)[:120]}")


def last_user_text(req) -> str:
    """取最后一条 user 消息文本（项目里没有现成 helper）。"""
    msgs = getattr(req, "messages", None) or []
    for m in reversed(msgs):
        if isinstance(m, dict):
            role, content = m.get("role"), m.get("content")
        else:
            role, content = getattr(m, "role", None), getattr(m, "content", None)
        if role != "user":
            continue
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if content and str(content).strip():
            return str(content).strip()
    return ""


def flatten_refs(refs) -> list[dict]:
    """把 references 里"所有能解析的信息"整理成统一结构。

    url / title / content 都不是必须的：缺谁就少谁；整条只有序号则丢弃。
    """
    out: list[dict] = []
    for i, r in enumerate(refs or [], 1):
        if not isinstance(r, dict):
            continue
        item: dict = {"_no": i}
        for k, v in r.items():
            if k in _SKIP_KEYS or v is None:
                continue
            if isinstance(v, str):
                v = v.strip()
                if not v:
                    continue
            elif isinstance(v, bool):
                v = str(v)
            elif isinstance(v, (int, float)):
                v = str(v)
            elif isinstance(v, list):
                v = " ".join(str(x) for x in v if isinstance(x, (str, int, float))).strip()
                if not v:
                    continue
            else:
                continue
            item[k] = v
        if len(item) <= 1:
            continue
        out.append(item)
    return out


def build_summary_messages(req, refs, pages: dict | None, cfg: dict, summary_length: str = "") -> list[dict]:
    pages = pages or {}
    per_page = int(cfg.get("max_chars_per_page") or DEFAULTS["max_chars_per_page"])
    total_cap = int(cfg.get("max_total_chars") or DEFAULTS["max_total_chars"])

    blocks: list[str] = []
    used = 0
    for item in flatten_refs(refs):
        n = item["_no"]
        url = item.get("url") or ""
        page = pages.get(url) if url else None

        body = "" if page is None else page
        if not body:
            for k in _TEXT_KEYS:
                v = item.get(k)
                if isinstance(v, str) and v.strip():
                    body = v
                    break
        body = re.sub(r"\s+", " ", body).strip()[:per_page]

        lines = [f"[{n}]"]
        if item.get("title"):
            lines.append(f"标题：{item['title']}")
        if url:
            lines.append(f"URL：{url}")
        for k in ("website", "date", "author", "source", "media"):
            if item.get(k):
                lines.append(f"{k}：{item[k]}")
        for k, v in item.items():          # 其余可解析字段全部带上（上游新增字段自动生效）
            if k in ("_no", "title", "url", "website", "date", "author", "source", "media"):
                continue
            if k in _TEXT_KEYS:
                continue
            lines.append(f"{k}：{v}")
        if body:
            lines.append(("网页正文：" if page else "摘要：") + body)
        else:
            lines.append("（无正文/摘要）")

        block = "\n".join(lines)
        remain = total_cap - used
        if remain <= 200:
            break
        if len(block) > remain:
            block = block[:remain] + "…"
        blocks.append(block)
        used += len(block)

    material = "\n\n".join(blocks) if blocks else "（本次搜索没有返回任何可用结果）"
    length_line = ""
    if str(summary_length or "").strip():
        # 需求：字数"其实就是拼接到 prompt 中而已"，不做校验
        length_line = f"\n请将回答控制在约 {str(summary_length).strip()} 字以内。"

    user = (f"【用户问题】\n{last_user_text(req) or '（未提供，请概述搜索结果）'}\n\n"
            f"【搜索结果】\n{material}{length_line}")
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def make_request(messages: list[dict], stream: bool = False):
    """构造一次内部 Chat Completions 请求（走 pool 的完整链路，需要真 req 对象）。"""
    from models import ChatCompletionRequest, ChatMessage
    return ChatCompletionRequest(
        model="__search_summary__",
        messages=[ChatMessage(role=m["role"], content=m["content"]) for m in messages],
        stream=bool(stream),
    )


async def _prepare(pool_obj, entry, req, response):
    refs = getattr(response, "references", None) or []
    cfg = cfg_of(pool_obj)
    pages: dict = {}
    n = int(cfg.get("max_fetch_urls") or 0)
    if n > 0:
        urls = [r.get("url") for r in refs
                if isinstance(r, dict) and isinstance(r.get("url"), str) and r["url"].strip()]
        if urls:
            pages = await fetch_pages(
                urls,
                max_urls=n,
                concurrency=int(cfg.get("fetch_concurrency") or 5),
                timeout_s=float(cfg.get("fetch_timeout_seconds") or 8),
                total_timeout_s=float(cfg.get("fetch_total_timeout_seconds") or 20),
                max_bytes=int(cfg.get("max_bytes") or DEFAULTS["max_bytes"]),
                max_chars=int(cfg.get("max_chars_per_page") or DEFAULTS["max_chars_per_page"]),
                proxy_url=str(cfg.get("fetch_proxy_url") or ""),
                block_private=bool(cfg.get("block_private_hosts", True)),
            )
    messages = build_summary_messages(req, refs, pages, cfg,
                                      summary_length=getattr(entry, "summary_length", "") or "")
    logger.info(f"[搜索总结] 资料准备完成 refs={len(refs)} 抓取成功={len(pages)} "
                f"prompt_chars={len(messages[-1]['content'])}")
    return messages


async def summarize_text(pool_obj, entry, req, response, caller: str = "") -> str | None:
    """非流式第 2 步：返回总结文本；任何失败返回 None（调用方保持原始列表）。"""
    summary_pool = (getattr(entry, "summary_pool", "") or "").strip()
    if not summary_pool:
        return None
    try:
        dk = await dedicated_key(pool_obj)
        messages = await _prepare(pool_obj, entry, req, response)
        sreq = make_request(messages)
        r2, tokens, _steps = await pool_obj.execute_with_fallback(
            summary_pool, sreq, None, (dk.get("name") or caller) if dk else caller,
            allow_search_summary=False)
        if r2 is not None:
            await _charge_dedicated(dk, tokens)
        if r2 is None or not getattr(r2, "choices", None):
            logger.warning(f"[搜索总结] 总结池 '{summary_pool}' 全部候选失败，降级为原始结果列表")
            return None
        txt = (r2.choices[0].message.content or "").strip()
        if not txt:
            logger.warning(f"[搜索总结] 总结池 '{summary_pool}' 返回空文本，降级为原始结果列表")
            return None
        logger.info(f"[搜索总结] 完成 pool={summary_pool} model={getattr(r2, 'model', '')} "
                    f"tokens={getattr(getattr(r2, 'usage', None), 'total_tokens', 0)} chars={len(txt)}")
        return txt
    except Exception as e:
        logger.warning(f"[搜索总结] 失败，降级为原始结果列表: {type(e).__name__}: {str(e)[:200]}")
        return None


async def summarize_stream(pool_obj, entry, req, response, caller: str = ""):
    """流式第 2 步：返回透传总结池 SSE 的异步生成器；不可用时返回 None。

    生成器行为：原样透传总结池分片（丢掉它的 [DONE]），最后补一帧 references + 自己的 [DONE]。
    """
    summary_pool = (getattr(entry, "summary_pool", "") or "").strip()
    if not summary_pool:
        return None
    try:
        dk = await dedicated_key(pool_obj)
        messages = await _prepare(pool_obj, entry, req, response)
        sreq = make_request(messages, stream=True)
        inner, _inner_entry, _steps = await pool_obj.execute_stream_with_fallback(
            summary_pool, sreq, None, (dk.get("name") or caller) if dk else caller,
            allow_search_summary=False)
        if inner is None:
            logger.warning(f"[搜索总结] 总结池 '{summary_pool}' 全部候选失败，降级为原始结果列表")
            return None
    except Exception as e:
        logger.warning(f"[搜索总结] 流式总结失败，降级为原始结果列表: {type(e).__name__}: {str(e)[:200]}")
        return None

    refs = getattr(response, "references", None) or []
    rid = getattr(response, "id", "") or search_sse.new_request_id()
    model_name = getattr(entry, "name", "") or "web_search"

    async def _relay():
        # v2.12.3：把总结池的 OpenAI 帧转成与 hp(web_summary) 完全一致的搜索帧形，
        # 差异只留在"值"上（model=web_search、request_id、content、references 条数）。
        yield search_sse.first_frame(rid, model_name, refs)
        captured = 0
        try:
            try:
                async for chunk in inner:
                    if not isinstance(chunk, str) or not chunk.lstrip().startswith("data: "):
                        continue
                    body = chunk.strip()[6:].strip()
                    if body == "[DONE]":
                        continue
                    try:
                        obj = json.loads(body)
                    except Exception:
                        continue
                    u = obj.get("usage")
                    if isinstance(u, dict) and u.get("total_tokens"):
                        captured = int(u["total_tokens"])
                    d = search_sse.delta_of(obj)
                    if d is None:
                        continue
                    _role, content = d
                    if content:
                        yield search_sse.frame(rid, model_name, content=content, role="")
            except Exception as e:
                logger.warning(f"[搜索总结] 总结流中断: {type(e).__name__}: {str(e)[:200]}")
        finally:
            # 客户端中途断开也要挂账（上游 token 已消耗）；未配置专门 Key 时是空操作
            await _charge_dedicated(dk, captured)
        yield search_sse.stop_frame(rid, model_name)
        yield search_sse.DONE

    return _relay()


def pool_ineligible_reason(pool_obj, name: str) -> str | None:
    """校验某池能否作为总结池：返回 None=可以，否则返回中文原因。"""
    name = (name or "").strip()
    if not name:
        return None
    pools = getattr(pool_obj, "pools", None) or {}
    if name not in pools:
        return f"模型池 '{name}' 不存在"
    ids = pool_obj._collect_pool_models(name) if hasattr(pool_obj, "_collect_pool_models") else []
    if not ids:
        return f"模型池 '{name}' 没有任何可用模型"
    reg = getattr(pool_obj, "registry", None) or {}
    for mid in ids:
        e = reg.get(mid)
        if e is None:
            continue
        if getattr(e, "provider", "") in ("qianfan_search", "qianfan_web_search"):
            return f"模型池 '{name}' 含搜索类模型（{mid}），不能作为总结池（会递归）"
        if getattr(e, "modality", "text") in ("embedding", "rerank"):
            return f"模型池 '{name}' 含 embedding/rerank 模型（{mid}），不能作为总结池"
    return None


def summary_candidates(pool_obj) -> list[str]:
    """可作为总结池的池名列表（排除搜索池、embedding/rerank 池、空池）。"""
    names = []
    for name in (getattr(pool_obj, "pools", None) or {}):
        if pool_ineligible_reason(pool_obj, name) is None:
            names.append(name)
    return names
