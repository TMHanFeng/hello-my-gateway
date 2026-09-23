from pydantic import BaseModel, Field
from typing import Optional, Union, Any


class ChatMessage(BaseModel):
    role: str
    content: Optional[Union[str, list]] = None
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[list[dict]] = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    stream: Optional[bool] = False
    stop: Optional[str | list[str]] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    tools: Optional[list[dict]] = None
    tool_choice: Optional[Union[str, dict]] = None
    response_format: Optional[dict] = None
    # 统一思考档位：off/minimal/low/medium/high/max（按模型 reasoning_map 映射为上游参数）
    reasoning_effort: Optional[str] = None
    # 上游自定义参数透传（千帆搜索 instruction/resource_type_filter 等；openai_provider 黑名单过滤后注入）
    extra_params: Optional[dict[str, Any]] = None


class EmbeddingRequest(BaseModel):
    """OpenAI 兼容 /v1/embeddings 请求体（完全透传上游）。"""
    model: str
    input: Union[str, list[str]]
    encoding_format: Optional[str] = None
    dimensions: Optional[int] = None
    user: Optional[str] = None
    extra_params: Optional[dict[str, Any]] = None


class RerankRequest(BaseModel):
    """Jina/Cohere/SiliconFlow 兼容 /v1/rerank 请求体（完全透传上游）。"""
    model: str
    query: str
    documents: list[Union[str, dict]]
    top_n: Optional[int] = None
    return_documents: Optional[bool] = None
    extra_params: Optional[dict[str, Any]] = None


class UsageInfo(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # 缓存命中 token（上游回报的 prompt 侧缓存命中量）：OpenAI 风格 prompt_tokens_details.cached_tokens /
    # DeepSeek 风格 prompt_cache_hit_tokens / Anthropic 风格 cache_read_input_tokens 归一到这里；
    # None = 上游未回报（缓存计费统计按命中 0 处理）。exclude：网关内部统计字段，不进客户端响应形状
    cached_tokens: Optional[int] = Field(None, exclude=True)


def cached_tokens_of(usage) -> Optional[int]:
    """从 OpenAI 风格 usage dict 里归一提取缓存命中 token。
    兼容各家写法：cached_tokens（本网关流式归一后）/ prompt_tokens_details.cached_tokens（OpenAI）/
    prompt_cache_hit_tokens（DeepSeek）/ cache_hit_tokens（零一 etc.）；未回报返回 None。"""
    if not isinstance(usage, dict):
        return None
    for k in ("cached_tokens", "prompt_cache_hit_tokens", "cache_hit_tokens"):
        v = usage.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
    ptd = usage.get("prompt_tokens_details")
    if isinstance(ptd, dict):
        v = ptd.get("cached_tokens")
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
    return None


class ChoiceMessage(BaseModel):
    role: str = "assistant"
    content: Optional[str] = ""
    reasoning_content: Optional[str] = None
    tool_calls: Optional[list[dict]] = None


class Choice(BaseModel):
    index: int = 0
    message: ChoiceMessage
    finish_reason: str = "stop"


class ChatCompletionResponse(BaseModel):
    id: str = ""
    object: str = "chat.completion"
    created: int = 0
    model: str = ""
    choices: list[Choice] = []
    usage: UsageInfo = UsageInfo()
    # 千帆搜索等上游的引用列表透传（OpenAI 路径直接返回本对象自动带出；Anthropic 转换忽略未知键）
    references: Optional[list] = None
