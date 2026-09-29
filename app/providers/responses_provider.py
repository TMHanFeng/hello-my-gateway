import httpx
import time
from typing import AsyncGenerator
from app.core.models import ChatCompletionRequest, ChatCompletionResponse
from app.core.responses_adapter import (
    openai_to_responses_request,
    responses_to_chat_response,
    responses_sse_to_chat,
)
from .openai_provider import RateLimitError


class ResponsesProvider:
    """openai_responses 协议上游：POST {base_url}/responses（OpenAI Responses API 原生上游）。

    与 AnthropicProvider 同构：chat()/chat_stream() 以网关内部 chat 格式进出，
    转换在本层完成——pool 的路由/计费/流式 usage 提取链路零改动。"""

    def __init__(self, base_url: str, api_key: str, proxy_url: str = "", timeout_seconds: int | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        # timeout_seconds: None=默认120s；0=无限等待（不设读超时）；>0=指定秒数。connect 固定 10s。
        _t = 120 if timeout_seconds is None else (None if timeout_seconds == 0 else timeout_seconds)
        kwargs = dict(
            timeout=httpx.Timeout(_t, connect=10),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=300),
            # 与 OpenAIProvider/AnthropicProvider 同理：禁用环境/注册表代理拾取，只认显式 proxy_url
            trust_env=False,
        )
        if proxy_url:
            kwargs["proxy"] = proxy_url
        self.client = httpx.AsyncClient(**kwargs)

    async def close(self):
        await self.client.aclose()

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def chat(self, req: ChatCompletionRequest, model_name: str,
                   reasoning_fragment: dict | None = None, timeout: float | None = None) -> ChatCompletionResponse:
        payload = openai_to_responses_request(req, model_name, reasoning_fragment)
        payload.pop("stream", None)
        _kw = {"timeout": httpx.Timeout(timeout, connect=10)} if timeout else {}
        resp = await self.client.post(
            f"{self.base_url}/responses",
            json=payload,
            headers=self._headers(),
            **_kw,
        )
        if resp.status_code == 429:
            raise RateLimitError("upstream 429")
        resp.raise_for_status()
        return responses_to_chat_response(resp.json(), model_name)

    async def chat_stream(self, req: ChatCompletionRequest, model_name: str,
                          reasoning_fragment: dict | None = None,
                          no_stream_options: bool = False) -> AsyncGenerator[str, None]:
        # no_stream_options 仅对齐 pool 调用签名（responses 协议无 stream_options 概念，忽略即可）
        payload = openai_to_responses_request(req, model_name, reasoning_fragment)
        payload["stream"] = True
        async with self.client.stream(
            "POST",
            f"{self.base_url}/responses",
            json=payload,
            headers=self._headers(),
        ) as resp:
            if resp.status_code == 429:
                raise RateLimitError("upstream 429")
            resp.raise_for_status()
            async for chunk in responses_sse_to_chat(resp.aiter_lines(), model_name):
                yield chunk

    async def speedtest(self, model_name: str) -> dict:
        payload = {"model": model_name, "input": "Hi", "max_output_tokens": 5}
        start = time.perf_counter()
        try:
            resp = await self.client.post(
                f"{self.base_url}/responses",
                json=payload,
                headers=self._headers(),
            )
            elapsed = time.perf_counter() - start
            if resp.status_code == 429:
                return {"status": "rate_limited", "latency_ms": round(elapsed * 1000)}
            resp.raise_for_status()
            usage = (resp.json() or {}).get("usage", {})
            tokens = (usage.get("input_tokens", 0) or 0) + (usage.get("output_tokens", 0) or 0)
            return {
                "status": "ok",
                "latency_ms": round(elapsed * 1000),
                "tokens": tokens,
                "tps": round(tokens / elapsed, 1) if elapsed > 0 else 0,
            }
        except Exception as e:
            elapsed = time.perf_counter() - start
            return {"status": "error", "error": str(e), "latency_ms": round(elapsed * 1000)}
