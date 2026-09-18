"""百度千帆「智能搜索生成」系列上游适配（协议名 qianfan_search / qianfan_web_search）。

两个变体共用本类：
- summary（qianfan_search）：智能搜索生成高性能版，POST {base_url}/v2/ai_search/web_summary，
  请求是 Chat Completions 的近似子集（messages 进、choices/usage 出），无 model 字段，
  响应多一个 references 引用列表。
- web_search（qianfan_web_search）：百度搜索（裸结果），POST {base_url}/v2/ai_search/web_search，
  响应仅 {request_id, references:[...]}——本类负责合成标准 chat 响应（结果列表渲染为正文）。

计费/安全阀复用既有 billing_mode="request" 按次口径，本类只负责协议形状转换。
"""
import json
import time
import uuid
from typing import AsyncGenerator

import httpx

from app.core.models import ChatCompletionRequest, ChatCompletionResponse, UsageInfo, Choice, ChoiceMessage
from .openai_provider import RateLimitError  # 复用同一异常类：pool 层 except 按此捕获
from app.gateway import search_sse  # v2.12.3 搜索流式帧统一：两变体结构一致、值可区分

# 搜索专属参数（instruction/resource_type_filter/search_match 等）经此注入，核心字段黑名单防覆盖
_RESERVED_KEYS = {"model", "messages", "stream", "stream_options", "input",
                  "tools", "tool_choice", "max_tokens", "system", "temperature"}


class QianfanSearchProvider:
    def __init__(self, base_url: str, api_key: str, proxy_url: str = "",
                 timeout_seconds: int | None = None, extra_params: dict | None = None,
                 variant: str = "summary"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.variant = variant
        self.api_path = ("/v2/ai_search/web_search" if variant == "web_search"
                         else "/v2/ai_search/web_summary")
        # 与 OpenAIProvider 同语义：None=默认120s；0=无限等待；>0=指定秒数。connect 固定 10s。
        _t = 120 if timeout_seconds is None else (None if timeout_seconds == 0 else timeout_seconds)
        kwargs = dict(
            timeout=httpx.Timeout(_t, connect=10),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=300),
        )
        if proxy_url:
            kwargs["proxy"] = proxy_url
        self.client = httpx.AsyncClient(**kwargs)
        self.extra_params = extra_params or {}

    async def close(self):
        await self.client.aclose()

    def _build_payload(self, req: ChatCompletionRequest, stream: bool = False) -> dict:
        messages = []
        for m in req.messages:
            content = m.content
            if isinstance(content, list):  # 搜索服务不收图片：多模态段落降级为纯文本
                content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
            messages.append({"role": m.role, "content": content})
        payload = {"messages": messages}
        if stream:
            payload["stream"] = True
        # 模型级 extra_params 为底、请求级覆盖；不发 model/stream_options/采样参数
        extra = getattr(req, "extra_params", None) or {}
        for k, v in {**self.extra_params, **extra}.items():
            if k not in _RESERVED_KEYS:
                payload[k] = v
        return payload

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:  # 未填 key 不发送 Authorization，避免严格校验 400（同 openai_provider）
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _wrap_search_result(self, data: dict, model_name: str) -> ChatCompletionResponse:
        """web_search 裸结果（{request_id, references}）合成标准 chat 响应：结果列表渲染为正文。"""
        refs = data.get("references") or []
        lines = []
        for i, r in enumerate(refs, 1):
            if not isinstance(r, dict):
                continue
            title = r.get("title") or ""
            url = r.get("url") or ""
            snippet = r.get("snippet") or r.get("content") or ""
            lines.append(f"{i}. {title}\n{url}\n{snippet}".strip())
        return ChatCompletionResponse(
            id=data.get("request_id") or f"chatcmpl-{uuid.uuid4().hex[:8]}",
            created=int(time.time()),
            model=model_name,
            choices=[Choice(index=0,
                            message=ChoiceMessage(role="assistant", content="\n\n".join(lines)),
                            finish_reason="stop")],
            usage=UsageInfo(),
            references=refs,
        )

    async def chat(self, req: ChatCompletionRequest, model_name: str,
                   reasoning_fragment: dict | None = None, timeout: float | None = None) -> ChatCompletionResponse:
        payload = self._build_payload(req)
        _kw = {"timeout": httpx.Timeout(timeout, connect=10)} if timeout else {}
        resp = await self.client.post(
            f"{self.base_url}{self.api_path}",
            json=payload,
            headers=self._headers(),
            **_kw,
        )
        if resp.status_code == 429:
            raise RateLimitError("upstream 429")
        resp.raise_for_status()
        data = resp.json()
        if self.variant == "web_search":
            return self._wrap_search_result(data, model_name)
        usage = data.get("usage") or {}
        # v2.12.3 一致性：choices/id/created/model 等与 references 同样做缺省兜底——
        # 上游字段缺失或为 null 时两变体产出的响应结构保持一致（不得一边报错一边正常）
        choices = []
        for c in (data.get("choices") or []):
            msg = c.get("message") or {}
            choices.append(Choice(
                index=c.get("index") or 0,
                message=ChoiceMessage(
                    role=msg.get("role") or "assistant",
                    content=msg.get("content") or "",
                    reasoning_content=msg.get("reasoning_content"),
                    tool_calls=msg.get("tool_calls"),
                ),
                finish_reason=c.get("finish_reason") or "stop",
            ))
        return ChatCompletionResponse(
            id=data.get("id") or f"chatcmpl-{uuid.uuid4().hex[:8]}",
            created=data.get("created") or int(time.time()),
            model=data.get("model") or model_name,
            choices=choices,
            usage=UsageInfo(
                prompt_tokens=usage.get("prompt_tokens") or 0,
                completion_tokens=usage.get("completion_tokens") or 0,
                total_tokens=usage.get("total_tokens") or 0,
            ),
            references=(data.get("references") or []),  # v2.12.3：统一为 list，避免与 web_search 出现 null/[] 的结构差异
        )

    async def chat_stream(self, req: ChatCompletionRequest, model_name: str,
                          reasoning_fragment: dict | None = None,
                          no_stream_options: bool = False) -> AsyncGenerator[str, None]:
        payload = self._build_payload(req, stream=(self.variant != "web_search"))
        if self.variant == "web_search":
            # web_search 不支持流式：整段取回后按统一帧形合成（首帧带 references + 正文帧 + stop 帧 + [DONE]），计费走 request 预扣
            resp = await self.client.post(f"{self.base_url}{self.api_path}", json=payload,
                                          headers=self._headers())
            if resp.status_code == 429:
                raise RateLimitError("upstream 429")
            resp.raise_for_status()
            wrapped = self._wrap_search_result(resp.json(), model_name)
            # v2.12.3 帧形统一：与 hp(web_summary) 的流式结构完全一致，只留"值"的差异
            _rid = wrapped.id or search_sse.new_request_id()
            yield search_sse.first_frame(_rid, model_name, wrapped.references)
            yield search_sse.frame(_rid, model_name, content=wrapped.choices[0].message.content, role="")
            yield search_sse.stop_frame(_rid, model_name)
            yield search_sse.DONE
            return
        done_sent = False
        async with self.client.stream(
            "POST",
            f"{self.base_url}{self.api_path}",
            json=payload,
            headers=self._headers(),
        ) as resp:
            if resp.status_code == 429:
                raise RateLimitError("upstream 429")
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                # 流式错误体必须在 with 退出前读出：流关闭后 pool 层拿到的为空，
                # 上游真实原因会整体丢失（与 openai_provider 同因同策）
                try:
                    _body = (await resp.aread()).decode("utf-8", "replace")
                except Exception:
                    _body = ""
                if _body:
                    _prefix = e.args[0] if e.args else str(e)
                    e.args = (f"{_prefix} | 上游响应: {_body[:300]}",)
                raise
            _fallback_rid = search_sse.new_request_id()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    # v2.12.3：上游帧已是目标形，只补 model（并保证 request_id 存在）
                    out = search_sse.normalize_upstream_line(line, model_name, _fallback_rid)
                    if out is None:
                        continue
                    if out == search_sse.DONE:
                        done_sent = True
                    yield out
                elif line.strip() == "":
                    continue
            if not done_sent:
                yield search_sse.DONE

    async def speedtest(self, model_name: str) -> dict:
        payload = {"messages": [{"role": "user", "content": "Hi"}]}
        start = time.perf_counter()
        try:
            resp = await self.client.post(
                f"{self.base_url}{self.api_path}",
                json=payload,
                headers=self._headers(),
            )
            elapsed = time.perf_counter() - start
            if resp.status_code == 429:
                return {"model_id": model_name, "status": "rate_limited", "latency_ms": round(elapsed * 1000)}
            resp.raise_for_status()
            data = resp.json()
            tokens = (data.get("usage") or {}).get("total_tokens", 0)
            return {
                "status": "ok",
                "latency_ms": round(elapsed * 1000),
                "tokens": tokens,
                "tps": round(tokens / elapsed, 1) if elapsed > 0 else 0,
            }
        except Exception as e:
            elapsed = time.perf_counter() - start
            return {"status": "error", "error": str(e), "latency_ms": round(elapsed * 1000)}
