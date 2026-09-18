"""搜索类模型（qianfan_search / qianfan_web_search）统一流式帧形。

目标：两个池对同一请求返回**完全相同的结构**，字段值允许不同（请求方据此判断搜索是否已切换）。

统一后的帧形（与 hp/web_summary 上游现状一致，仅多一个 model 键）：

    {"request_id": "<str>", "model": "<str>",
     "choices": [{"index": 0, "finish_reason": "<str>",
                  "delta": {"role": "<str>", "content": "<str>"}}],
     "references": [ ... ]}        # 仅首帧携带（与 hp 现状一致）

末尾固定：一帧 finish_reason="stop" 的收尾帧 + `data: [DONE]`。

差异只在"值"：model = web_summary / web_search；request_id、references 条数、
delta.content 形态各自不同 —— 请求方据此即可判断走的是哪个搜索上游。
"""
from __future__ import annotations

import json
import uuid

DONE = "data: [DONE]\n\n"


def new_request_id() -> str:
    return str(uuid.uuid4())


def frame(request_id: str, model: str, *, content=None, role: str = "",
          finish_reason: str = "", references=None) -> str:
    """构造一个标准搜索帧。references 传 None 表示本帧不带该键。"""
    obj = {
        "request_id": request_id,
        "model": model,
        "choices": [{
            "index": 0,
            "finish_reason": finish_reason,
            "delta": {"role": role, "content": "" if content is None else content},
        }],
    }
    if references is not None:
        obj["references"] = references
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


def first_frame(request_id: str, model: str, references) -> str:
    """首帧：空内容 + role=assistant，并携带 references（与 hp 上游一致）。"""
    return frame(request_id, model, content="", role="assistant", references=references or [])


def stop_frame(request_id: str, model: str) -> str:
    return frame(request_id, model, content="", role="", finish_reason="stop")


def normalize_upstream_line(line: str, model: str, fallback_request_id: str):
    """hp 分支：上游帧本来就是目标形，只补 model（并保证 request_id 存在）。

    返回 None 表示该行不是 data 行、应丢弃。
    """
    if not line.startswith("data: "):
        return None
    payload = line[6:].strip()
    if payload == "[DONE]":
        return DONE
    try:
        obj = json.loads(payload)
    except Exception:
        return line + "\n\n"
    if isinstance(obj, dict):
        obj["model"] = model
        if not obj.get("request_id"):
            obj["request_id"] = fallback_request_id
        return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"
    return line + "\n\n"


def delta_of(chunk) -> tuple[str, str] | None:
    """从任意 OpenAI 风格 chunk 里取出 (role, content)；无 choices/delta 返回 None。"""
    if not isinstance(chunk, dict):
        return None
    ch = chunk.get("choices") or []
    if not ch or not isinstance(ch[0], dict):
        return None
    d = ch[0].get("delta") or {}
    if not isinstance(d, dict):
        return None
    return (d.get("role") or ""), (d.get("content") or "")
