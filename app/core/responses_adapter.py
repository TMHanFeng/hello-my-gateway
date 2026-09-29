"""OpenAI Responses API <-> Chat Completions 请求/响应/流式格式适配（纯函数）。

对外暴露 OpenAI Responses API（POST /v1/responses）调用能力：
- 请求：Responses 格式 -> 内部 Chat Completions 格式（responses_to_openai）
- 响应：Chat Completions -> Responses 格式（openai_to_responses_response）
- 流式：Chat SSE chunk -> Responses SSE 事件流（openai_sse_to_responses）

与 format_adapter.py（Anthropic Messages 适配）同构：内部统一走 chat 枢纽，
路由/计费/决策日志零改动；不支持的 Responses 特性抛 ResponsesFormatError（端点层转 400）。
"""

import json
import time
import uuid

from .models import cached_tokens_of


class ResponsesFormatError(ValueError):
    """请求携带网关不支持的 Responses 特性（端点层统一转 400 带原因）。"""


# ─────────────────────────── 请求方向：Responses -> Chat ───────────────────────────


def _content_part_to_openai(part) -> dict | None:
    """单个 Responses 内容块 -> Chat 内容块。未知块返回 None（跳过）。"""
    if not isinstance(part, dict):
        return None
    ptype = part.get("type")
    if ptype in ("input_text", "output_text", "summary_text", "refusal"):
        text = part.get("text")
        return {"type": "text", "text": text} if isinstance(text, str) else None
    if ptype == "input_image":
        url = part.get("image_url")
        if isinstance(url, str):
            return {"type": "image_url", "image_url": {"url": url}}
        if isinstance(url, dict) and isinstance(url.get("url"), str):
            return {"type": "image_url", "image_url": {"url": url["url"]}}
    return None


def _message_item_to_openai(item: dict) -> dict:
    role = item.get("role") or "user"
    if role == "developer":
        role = "system"
    content = item.get("content")
    if isinstance(content, str):
        return {"role": role, "content": content}
    if isinstance(content, list):
        blocks = [b for b in (_content_part_to_openai(p) for p in content) if b is not None]
        if not blocks:
            return {"role": role, "content": ""}
        if all(b.get("type") == "text" for b in blocks):
            return {"role": role, "content": "".join(b.get("text", "") for b in blocks)}
        return {"role": role, "content": blocks}
    return {"role": role, "content": ""}


def _output_text(output) -> str:
    """function_call_output.output（字符串或内容块数组）-> 纯文本。"""
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        return "".join(
            p.get("text", "") for p in output if isinstance(p, dict) and isinstance(p.get("text"), str)
        )
    if output is None:
        return ""
    return json.dumps(output, ensure_ascii=False)


def _input_items_to_messages(items: list) -> list:
    messages = []
    for it in items:
        if not isinstance(it, dict):
            continue
        itype = it.get("type")
        if itype == "message":
            messages.append(_message_item_to_openai(it))
        elif itype == "function_call":
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": it.get("call_id") or it.get("id") or "",
                    "type": "function",
                    "function": {"name": it.get("name", ""), "arguments": it.get("arguments") or "{}"},
                }],
            })
        elif itype == "function_call_output":
            messages.append({
                "role": "tool",
                "tool_call_id": it.get("call_id") or "",
                "content": _output_text(it.get("output")),
            })
        elif itype == "reasoning":
            continue  # 思考项不回放：chat 无对应结构（官方亦不强制回放推理内容）
        elif itype == "item_reference":
            # 仅在携带 previous_response_id 时由端点层预解析；到这里的都是无法解析的引用
            raise ResponsesFormatError(
                "item_reference 无法解析：必须携带 previous_response_id，且引用项须在所引用响应的 output 内"
            )
        else:
            raise ResponsesFormatError(f"不支持的 input item 类型 '{itype}'")
    return messages


def _convert_tools(tools) -> list | None:
    """Responses flat 工具定义 -> Chat nested function 工具。内置工具类型显式拒绝。"""
    out = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        if t.get("type") != "function":
            raise ResponsesFormatError(
                f"内置工具类型 '{t.get('type')}' 暂不支持（本网关仅支持 function 工具），请使用 Chat Completions 端点的透传能力"
            )
        fn = {"type": "function", "function": {"name": t.get("name", ""), "description": t.get("description", "")}}
        if isinstance(t.get("parameters"), dict):
            fn["function"]["parameters"] = t["parameters"]
        out.append(fn)
    return out or None


def _convert_tool_choice(tc):
    if isinstance(tc, str):
        if tc in ("auto", "none", "required"):
            return tc
        raise ResponsesFormatError(f"不支持的 tool_choice '{tc}'（支持 auto/none/required）")
    if isinstance(tc, dict) and tc.get("type") == "function" and tc.get("name"):
        return {"type": "function", "function": {"name": tc["name"]}}
    raise ResponsesFormatError("不支持的 tool_choice（支持 auto/none/required 或 {\"type\":\"function\",\"name\":...}）")


def _convert_text_format(text) -> dict | None:
    """text.format -> Chat response_format（json_object/json_schema；text 类型不映射）。"""
    if not isinstance(text, dict) or not isinstance(text.get("format"), dict):
        return None
    fmt = text["format"]
    ftype = fmt.get("type")
    if ftype == "json_object":
        return {"type": "json_object"}
    if ftype == "json_schema":
        js = {"name": fmt.get("name") or "response"}
        if fmt.get("schema") is not None:
            js["schema"] = fmt["schema"]
        if fmt.get("strict") is not None:
            js["strict"] = fmt["strict"]
        return {"type": "json_schema", "json_schema": js}
    return None


def responses_to_openai(body: dict) -> dict:
    """Responses 请求 -> Chat Completion 请求。返回新 dict，不修改入参。

    映射：input(str/items)→messages、instructions→首条 system、flat tools→nested、
    reasoning.effort→reasoning_effort（沿用现有档位校验）、max_output_tokens→max_tokens、
    text.format→response_format。store/previous_response_id 由端点层处理，
    metadata/truncation/include/parallel_tool_calls 忽略。
    """
    if not isinstance(body.get("model"), str) or not body.get("model"):
        raise ResponsesFormatError("缺少必填字段 'model'")
    raw_input = body.get("input")
    if raw_input is None:
        raise ResponsesFormatError("缺少必填字段 'input'")
    if isinstance(raw_input, str):
        messages = [{"role": "user", "content": raw_input}]
    elif isinstance(raw_input, list):
        messages = _input_items_to_messages(raw_input)
    else:
        raise ResponsesFormatError("'input' 仅支持字符串或 item 数组")

    instructions = body.get("instructions")
    if isinstance(instructions, str) and instructions:
        messages = [{"role": "system", "content": instructions}] + messages

    out = {"model": body["model"], "messages": messages}
    if body.get("stream"):
        out["stream"] = True
    for src, dst in (("temperature", "temperature"), ("top_p", "top_p"), ("max_output_tokens", "max_tokens")):
        if body.get(src) is not None:
            out[dst] = body[src]
    tools = _convert_tools(body.get("tools") or [])
    if tools:
        out["tools"] = tools
    if body.get("tool_choice") is not None:
        out["tool_choice"] = _convert_tool_choice(body["tool_choice"])
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("effort") is not None:
        out["reasoning_effort"] = str(reasoning["effort"])
    rf = _convert_text_format(body.get("text"))
    if rf:
        out["response_format"] = rf
    return out


# ─────────────────────────── 响应方向：Chat -> Responses ───────────────────────────


def _short_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def _usage_block(input_tokens, output_tokens, total_tokens, cached_tokens=None) -> dict:
    u = {
        "input_tokens": input_tokens or 0,
        "output_tokens": output_tokens or 0,
        "total_tokens": total_tokens or 0,
        "output_tokens_details": {"reasoning_tokens": 0},
    }
    if cached_tokens:
        u["input_tokens_details"] = {"cached_tokens": int(cached_tokens)}
    return u


def _build_response_object(resp_id: str, model: str, created: int, output_items: list,
                           usage: dict, finish_reason) -> dict:
    status = "incomplete" if finish_reason == "length" else "completed"
    return {
        "id": resp_id,
        "object": "response",
        "created_at": created or int(time.time()),
        "status": status,
        "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
        "model": model or "",
        "output": output_items,
        "usage": usage,
        "error": None,
        "metadata": {},
    }


def openai_to_responses_response(response) -> dict:
    """ChatCompletionResponse（pydantic 对象）-> Responses 响应 dict。

    直接取对象属性而非 model_dump：cached_tokens 字段带 exclude=True（仅内部计费用）。
    """
    choice = response.choices[0] if response.choices else None
    message = choice.message if choice else None
    items = []
    reasoning = getattr(message, "reasoning_content", None) if message is not None else None
    if isinstance(reasoning, str) and reasoning:
        items.append({
            "id": _short_id("rs"), "type": "reasoning",
            "summary": [{"type": "summary_text", "text": reasoning}],
        })
    text = (message.content if message is not None else "") or ""
    items.append({
        "id": _short_id("msg"), "type": "message", "role": "assistant", "status": "completed",
        "content": [{"type": "output_text", "text": text if isinstance(text, str) else "", "annotations": []}],
    })
    for tc in (getattr(message, "tool_calls", None) if message is not None else None) or []:
        fn = tc.get("function") or {}
        items.append({
            "id": _short_id("fc"), "type": "function_call", "status": "completed",
            "call_id": tc.get("id", ""), "name": fn.get("name", ""),
            "arguments": fn.get("arguments", "") or "{}",
        })
    u = getattr(response, "usage", None)
    usage = _usage_block(
        getattr(u, "prompt_tokens", 0), getattr(u, "completion_tokens", 0), getattr(u, "total_tokens", 0),
        getattr(u, "cached_tokens", None),
    )
    return _build_response_object(
        _short_id("resp"), response.model, response.created, items, usage,
        choice.finish_reason if choice is not None else "stop",
    )


# ─────────────────────────── 流式：Chat SSE -> Responses SSE ───────────────────────────


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def openai_sse_to_responses(openai_sse_stream, on_complete=None):
    """OpenAI chat SSE chunk 文本流 -> Responses API SSE 事件文本流。

    事件序：response.created → output_item.added → (content_part.added) →
    *_delta… → *_done / output_item.done → response.completed（含完整 response 对象与 usage）。
    on_complete：async 回调（完整 response 对象 dict），在 response.completed 事件前调用
    （状态化落库用；store=false 时传 None）。流中途异常：发 response.failed 后停止（不抛出）。
    """
    seq = 0
    resp_id = _short_id("resp")
    created = int(time.time())
    model = ""
    started = False
    completed_sent = False
    failed_sent = False
    last_usage = None      # 所有 chunk 的 usage（含 usage-only）
    cached = None
    finish_reason = None

    # item 状态机：按创建顺序分配 output_index；open_records 记录待收尾项
    out_idx = -1
    msg = None             # {"id","buf"}
    rs = None              # {"id","buf"}
    tools = {}             # chat tool_calls index -> {"id","call_id","name","args"}
    open_records = []      # ("reasoning"|"message"|"function_call", key) 创建顺序

    def _next_seq() -> int:
        nonlocal seq
        s = seq
        seq += 1
        return s

    def _open_item(kind: str, item: dict):
        nonlocal out_idx
        out_idx += 1
        rec = {"kind": kind, "index": out_idx, "item": item, "buf": ""}
        open_records.append(rec)
        return rec

    def _close_all_records():
        """逐个收尾打开的 item（.done 事件 + 完整 item 对象），返回完整 output items 列表。"""
        events = []
        items = []
        for rec in open_records:
            kind, idx, item = rec["kind"], rec["index"], rec["item"]
            if kind == "reasoning":
                summary = [{"type": "summary_text", "text": rec["buf"]}] if rec["buf"] else []
                item = dict(item, summary=summary)
                events.append(_sse("response.output_item.done", {
                    "type": "response.output_item.done", "sequence_number": _next_seq(),
                    "output_index": idx, "item": item,
                }))
            elif kind == "message":
                events.append(_sse("response.output_text.done", {
                    "type": "response.output_text.done", "sequence_number": _next_seq(),
                    "item_id": item["id"], "output_index": idx, "content_index": 0, "text": rec["buf"],
                }))
                events.append(_sse("response.content_part.done", {
                    "type": "response.content_part.done", "sequence_number": _next_seq(),
                    "item_id": item["id"], "output_index": idx, "content_index": 0,
                    "part": {"type": "output_text", "text": rec["buf"], "annotations": []},
                }))
                item = dict(item, status="completed",
                            content=[{"type": "output_text", "text": rec["buf"], "annotations": []}])
                events.append(_sse("response.output_item.done", {
                    "type": "response.output_item.done", "sequence_number": _next_seq(),
                    "output_index": idx, "item": item,
                }))
            else:  # function_call
                if rec["buf"]:
                    events.append(_sse("response.function_call_arguments.done", {
                        "type": "response.function_call_arguments.done", "sequence_number": _next_seq(),
                        "item_id": item["id"], "output_index": idx, "arguments": rec["buf"],
                    }))
                item = dict(item, status="completed", arguments=rec["buf"] or "{}")
                events.append(_sse("response.output_item.done", {
                    "type": "response.output_item.done", "sequence_number": _next_seq(),
                    "output_index": idx, "item": item,
                }))
            items.append(item)
        open_records.clear()
        return events, items

    async def _emit_completed():
        events, items = _close_all_records()
        for e in events:
            yield e
        usage = _usage_block(
            (last_usage or {}).get("prompt_tokens"), (last_usage or {}).get("completion_tokens"),
            (last_usage or {}).get("total_tokens"), cached,
        )
        resp_obj = _build_response_object(resp_id, model, created, items, usage, finish_reason or "stop")
        if on_complete is not None:
            try:
                await on_complete(resp_obj)
            except Exception:
                pass  # 落库失败不影响事件流收尾（与计费日志同口径）
        yield _sse("response.completed", {
            "type": "response.completed", "sequence_number": _next_seq(), "response": resp_obj,
        })

    try:
        async for chunk in openai_sse_stream:
            if not isinstance(chunk, str):
                continue
            text = chunk.strip()
            if not text.startswith("data: "):
                continue
            payload = text[6:].strip()
            if payload == "[DONE]":
                # 幂等收尾（break：上游 [DONE] 后滞留的 chunk 不再产出事件）
                if started and not completed_sent:
                    completed_sent = True
                    async for e in _emit_completed():
                        yield e
                break
            try:
                obj = json.loads(payload)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue

            model = obj.get("model") or model
            usage = obj.get("usage")
            if isinstance(usage, dict):
                last_usage = usage
                if cached is None:
                    cached = cached_tokens_of(usage)

            if not started:
                started = True
                yield _sse("response.created", {
                    "type": "response.created", "sequence_number": _next_seq(),
                    "response": _build_response_object(resp_id, model, created, [], _usage_block(0, 0, 0), None),
                })

            choices = obj.get("choices")
            if not isinstance(choices, list) or not choices:
                continue  # usage-only chunk
            choice = choices[0]
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if isinstance(delta, dict):
                rpiece = delta.get("reasoning_content")
                if isinstance(rpiece, str) and rpiece:
                    if rs is None:
                        rec = _open_item("reasoning", {"id": _short_id("rs"), "type": "reasoning", "summary": []})
                        rs = rec
                        yield _sse("response.output_item.added", {
                            "type": "response.output_item.added", "sequence_number": _next_seq(),
                            "output_index": rec["index"], "item": rec["item"],
                        })
                    rs["buf"] += rpiece
                    yield _sse("response.reasoning_text.delta", {
                        "type": "response.reasoning_text.delta", "sequence_number": _next_seq(),
                        "item_id": rs["item"]["id"], "output_index": rs["index"], "delta": rpiece,
                    })
                piece = delta.get("content")
                if isinstance(piece, str) and piece:
                    if msg is None:
                        rec = _open_item("message", {
                            "id": _short_id("msg"), "type": "message", "role": "assistant",
                            "status": "in_progress", "content": [],
                        })
                        msg = rec
                        yield _sse("response.output_item.added", {
                            "type": "response.output_item.added", "sequence_number": _next_seq(),
                            "output_index": rec["index"], "item": rec["item"],
                        })
                        yield _sse("response.content_part.added", {
                            "type": "response.content_part.added", "sequence_number": _next_seq(),
                            "item_id": rec["item"]["id"], "output_index": rec["index"], "content_index": 0,
                            "part": {"type": "output_text", "text": "", "annotations": []},
                        })
                    msg["buf"] += piece
                    yield _sse("response.output_text.delta", {
                        "type": "response.output_text.delta", "sequence_number": _next_seq(),
                        "item_id": msg["item"]["id"], "output_index": msg["index"],
                        "content_index": 0, "delta": piece,
                    })
                for tc in delta.get("tool_calls") or []:
                    if not isinstance(tc, dict):
                        continue
                    idx = tc.get("index", 0)
                    rec = tools.get(idx)
                    if rec is None:
                        call_id = tc.get("id") or _short_id("call")
                        rec = _open_item("function_call", {
                            "id": _short_id("fc"), "type": "function_call", "status": "in_progress",
                            "call_id": call_id, "name": "", "arguments": "",
                        })
                        rec["call_id"] = call_id
                        tools[idx] = rec
                        yield _sse("response.output_item.added", {
                            "type": "response.output_item.added", "sequence_number": _next_seq(),
                            "output_index": rec["index"], "item": rec["item"],
                        })
                    fn = tc.get("function") or {}
                    name = fn.get("name")
                    if isinstance(name, str) and name:
                        rec["item"]["name"] = name
                    args_piece = fn.get("arguments")
                    if isinstance(args_piece, str) and args_piece:
                        rec["buf"] += args_piece
                        yield _sse("response.function_call_arguments.delta", {
                            "type": "response.function_call_arguments.delta", "sequence_number": _next_seq(),
                            "item_id": rec["item"]["id"], "output_index": rec["index"], "delta": args_piece,
                        })
            finish = choice.get("finish_reason")
            if finish:
                finish_reason = finish
    except Exception as e:
        # 流中途异常：response.failed 事件后停止（不补发收尾）
        failed_sent = True
        yield _sse("response.failed", {
            "type": "response.failed", "sequence_number": _next_seq(),
            "response": _build_response_object(resp_id, model, created, [], _usage_block(0, 0, 0), None)
                        | {"status": "failed", "error": {"code": None, "message": str(e)}},
        })
        return
    finally:
        # 问题31（v2.12.3）同款：本层被关闭/异常退出时确定性关闭下游流，防连接租约滞留
        try:
            await openai_sse_stream.aclose()
        except Exception:
            pass
    # 收尾兜底（循环正常耗尽但无 [DONE]）：只要没发过 completed 就补发，防客户端挂等
    # （不放 finally：async generator 在 finally 中 yield 遇客户端断开会抛 GeneratorExit）
    if started and not completed_sent and not failed_sent:
        completed_sent = True
        async for e in _emit_completed():
            yield e
