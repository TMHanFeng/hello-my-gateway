# -*- coding: utf-8 -*-
"""计费回归套件(问题24 不变量固化)+ 思考/估算回归。

用法:python test_billing_regression.py
- 自行启动隔离实例(端口 8678,共享 data/gateway.db,结束清理测试数据),不影响 8650 生产。
- mock 上游(127.0.0.1:8125):流式 SSE / 非流式 JSON,usage 可控,记录收到的请求体。
- 全部断言通过 exit 0;任何失败 exit 1(优化阶段必须全绿才继续)。
"""
import asyncio
import io
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

BASE = "http://127.0.0.1:8678"
MOCK_PORT = 8125
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 本文件在 tests/ 下，REPO 取仓库根
sys.path.insert(0, REPO)  # 使测试进程可直接 import app.*（脚本方式运行时 sys.path[0] 是 tests/）
USAGE = {"prompt_tokens": 100, "completion_tokens": 33, "total_tokens": 133}
REFS = [{"title": "参考1", "url": "https://example.com/a"}, {"title": "参考2", "url": "https://example.com/b"}]

captured_bodies = []      # mock 收到的请求体(供思考映射断言)
mock_mode = {"usage": True, "think": False, "cached": False}


def cur_usage():
    """T26 缓存计费：cached 开关打开时上游按 OpenAI 风格回报缓存命中（40/100 命中）"""
    if mock_mode["cached"]:
        return {**USAGE, "prompt_tokens_details": {"cached_tokens": 40}}
    return USAGE


class MockHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        captured_bodies.append(body)
        content = "答案"
        if mock_mode["think"]:
            content = "<think>思考过程</think>答案"
        # T15（问题30）：模拟 DashScope 对 <10×10 图片的确定性拒绝
        if str(body.get("model", "")).startswith("mock-bad400"):
            out_b = json.dumps({"error": {"code": "InternalError.Algo.InvalidParameter",
                                          "message": "[height:1 or width:1 must be larger than 10]"}}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out_b)))
            self.end_headers()
            self.wfile.write(out_b)
            return
        # T35（2026-10-09 书生 image/jpg 694 连拒）：上游对图片 MIME 严格校验的 400（书生真实报错形状）
        if str(body.get("model", "")).startswith("mock-imgbad"):
            out_b = json.dumps({"type": "error",
                                "error": {"type": "invalid_request_error",
                                          "message": "inline image type is not supported: image/jpg"},
                                "request_id": "req_mock_imgbad"}, ensure_ascii=False).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out_b)))
            self.end_headers()
            self.wfile.write(out_b)
            return
        # T17（本地模型切换窗口）：503 qwen_switching + retry_after，要求客户端稍后重试
        if str(body.get("model", "")).startswith("mock-switching"):
            out_b = json.dumps({"error": {"message": "模型切换中（预计 60 秒后可用），请稍后重试",
                                          "type": "service_unavailable", "code": "qwen_switching"},
                                "retry_after": 60}).encode()
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out_b)))
            self.end_headers()
            self.wfile.write(out_b)
            return
        # T18（v2.12.3 结构统一）：千帆搜索两协议按真实上游形状返回
        if self.path == "/v1/embeddings":
            # T27 模态测速：embedding 卡片「⚡测速」走 /embeddings（chat/completions 对嵌入模型必然 400）
            out_b = json.dumps({"data": [{"embedding": [0.1, 0.2], "index": 0}],
                                "usage": {"total_tokens": 5}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out_b)))
            self.end_headers()
            self.wfile.write(out_b)
            return
        if self.path == "/responses":
            # T30f-h（v2.16.1）openai_responses 协议上游：以 Responses 格式收发
            if body.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()

                def rs_sse(evt, obj):
                    return ("event: " + evt + "\ndata: " + json.dumps(
                        dict({"type": evt}, **obj), ensure_ascii=False) + "\n\n").encode()
                _rid = "resp_mock_stream"
                self.wfile.write(rs_sse("response.created", {"response": {"id": _rid, "status": "in_progress"}}))
                self.wfile.flush()
                self.wfile.write(rs_sse("response.output_item.added",
                                        {"output_index": 0, "item": {"id": "msg_m", "type": "message", "role": "assistant"}}))
                self.wfile.flush()
                for _piece in ("答", "案"):
                    self.wfile.write(rs_sse("response.output_text.delta",
                                            {"item_id": "msg_m", "output_index": 0, "delta": _piece}))
                    self.wfile.flush()
                self.wfile.write(rs_sse("response.output_text.done", {"item_id": "msg_m", "text": "答案"}))
                self.wfile.flush()
                self.wfile.write(rs_sse("response.completed", {"response": {
                    "id": _rid, "status": "completed",
                    "output": [{"id": "msg_m", "type": "message", "role": "assistant", "status": "completed",
                                "content": [{"type": "output_text", "text": "答案", "annotations": []}]}],
                    "usage": {"input_tokens": 100, "output_tokens": 33, "total_tokens": 133}}}))
                self.wfile.flush()
            else:
                out_b = json.dumps({
                    "id": "resp_mock_1", "object": "response", "status": "completed",
                    "model": body.get("model"),
                    "output": [{"id": "msg_m", "type": "message", "role": "assistant", "status": "completed",
                                "content": [{"type": "output_text", "text": "答案", "annotations": []}]}],
                    "usage": {"input_tokens": 100, "output_tokens": 33, "total_tokens": 133,
                              "input_tokens_details": {"cached_tokens": 40}},
                }, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out_b)))
                self.end_headers()
                self.wfile.write(out_b)
            return
        if self.path == "/v1/rerank":
            # T27 模态测速：rerank 卡片「⚡测速」走 /rerank
            out_b = json.dumps({"results": [{"index": 0, "relevance_score": 0.9}],
                                "usage": {"total_tokens": 7}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out_b)))
            self.end_headers()
            self.wfile.write(out_b)
            return
        if self.path == "/v2/ai_search/web_search":
            # qianfan_web_search 真实形状：裸结果 {request_id, references}，无 choices/usage（上游不支持流式）
            out_b = json.dumps({"request_id": "mock-qfws-req-1", "references": REFS}, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out_b)))
            self.end_headers()
            self.wfile.write(out_b)
            return
        if self.path == "/v2/ai_search/web_summary" and body.get("stream"):
            # qianfan_search(web_summary) 真实流式形状：{request_id, choices[delta]} 搜索帧
            # （首帧带 references、无 usage 帧），末尾 finish_reason=stop 收尾帧 + [DONE]
            def qf_sse(obj):
                return ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(qf_sse({"request_id": "mock-qf-req-1", "choices": [
                {"index": 0, "finish_reason": "", "delta": {"role": "assistant", "content": content}}],
                "references": REFS}))
            self.wfile.flush()
            self.wfile.write(qf_sse({"request_id": "mock-qf-req-1", "choices": [
                {"index": 0, "finish_reason": "stop", "delta": {"role": "", "content": ""}}]}))
            self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        # T30（v2.16.0）Responses 工具往返：mock-toolcall 模型按 OpenAI 形状回 tool_calls
        if str(body.get("model", "")).startswith("mock-toolcall"):
            if body.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()

                def sse2(obj):
                    return ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode()
                self.wfile.write(sse2({"id": "m", "choices": [{"index": 0, "delta": {"role": "assistant"}}]}))
                self.wfile.flush()
                self.wfile.write(sse2({"id": "m", "choices": [{"index": 0, "delta": {"tool_calls": [
                    {"index": 0, "id": "call_t1", "type": "function",
                     "function": {"name": "get_weather", "arguments": ""}}]}}]}))
                self.wfile.flush()
                for _frag in ('{"city":', '"北京"}'):
                    self.wfile.write(sse2({"id": "m", "choices": [{"index": 0, "delta": {"tool_calls": [
                        {"index": 0, "function": {"arguments": _frag}}]}}]}))
                    self.wfile.flush()
                self.wfile.write(sse2({"id": "m", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}))
                self.wfile.flush()
                if mock_mode["usage"]:
                    self.wfile.write(sse2({"id": "m", "choices": [], "usage": cur_usage()}))
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            else:
                out_b = json.dumps({"id": "m", "object": "chat.completion", "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "", "tool_calls": [
                        {"id": "call_t1", "type": "function",
                         "function": {"name": "get_weather", "arguments": "{\"city\":\"北京\"}"}}]},
                     "finish_reason": "tool_calls"}], "usage": cur_usage()}, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out_b)))
                self.end_headers()
                self.wfile.write(out_b)
            return
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            def sse(obj):
                return ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode()
            self.wfile.write(sse({"id": "m", "choices": [{"index": 0, "delta": {"content": content, "role": "assistant"}}]}))
            self.wfile.flush()
            if mock_mode["usage"] and "nousage" not in json.dumps(body, ensure_ascii=False):
                self.wfile.write(sse({"id": "m", "choices": [], "usage": cur_usage(), "references": REFS}))
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        else:
            out = {"id": "m", "object": "chat.completion", "choices": [
                {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                "usage": cur_usage(), "references": REFS}
            out_b = json.dumps(out, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out_b)))
            self.end_headers()
            self.wfile.write(out_b)

    def do_GET(self):
        # T39（v2.16.10）余量监控：DeepSeek 平台查询器打 GET {base_url}/user/balance
        # （绑定 base_url 指向本 mock，真实 DeepSeek 余额响应形状）
        if self.path == "/user/balance":
            out_b = json.dumps({"is_available": True, "balance_infos": [
                {"currency": "CNY", "total_balance": "124.11", "granted_balance": "",
                 "topped_up_balance": ""}]}, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out_b)))
            self.end_headers()
            self.wfile.write(out_b)
            return
        out_b = b'{"error": {"message": "mock: no such GET endpoint"}}'
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out_b)))
        self.end_headers()
        self.wfile.write(out_b)

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", MOCK_PORT), MockHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

cfg = json.load(open(os.path.join(REPO, "config.json"), encoding="utf-8"))
ADMIN = {"Authorization": "Bearer " + cfg["server"]["api_key"], "Content-Type": "application/json"}
os.makedirs(os.path.join(REPO, "data"), exist_ok=True)  # 全新克隆无 data/（网关未启动过），先建再连
DB = sqlite3.connect(os.path.join(REPO, "data", "gateway.db"))
DB.row_factory = sqlite3.Row
# T23：生产 search_summary.caller_key 原值（用例内临时改写，结束时/deep_clean 必须原样还原）
CK0 = (cfg.get("search_summary") or {}).get("caller_key") or ""
# T39：生产 quota_monitor 节点原值（用例内改写 port/circuit_enabled，结束时/deep_clean 原样还原）
QM0 = json.loads(json.dumps(cfg.get("quota_monitor") or {}, ensure_ascii=False))

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS | " if cond else "FAIL | ") + name + (" | " + str(detail) if detail else ""), flush=True)


TEST_MODELS = [
    {"id": "zzbt/echo-token", "name": "mock-echo-token", "provider_id": "zzmock", "modality": "text",
     "is_free": True, "daily_token_limit": 1000000000,
     "reasoning_map": {"off": {"thinking": {"type": "disabled"}}, "low": {"reasoning_effort": "low"}}},
    {"id": "zzbt/echo-req", "name": "mock-echo-req", "provider_id": "zzmock", "modality": "text",
     "is_free": True, "daily_token_limit": 1000000000, "billing_mode": "request"},
    {"id": "zzbt/echo-once", "name": "mock-echo-once", "provider_id": "zzmock", "modality": "text",
     "is_free": True, "token_type": "one_time", "max_tokens": 200},
    {"id": "zzbt/echo-5h", "name": "mock-echo-5h", "provider_id": "zzmock", "modality": "text",
     "is_free": True, "token_type": "rolling_5h", "daily_token_limit": 1000000000},
    {"id": "zzbt/echo-smart", "name": "mock-echo-smart", "provider_id": "zzmock", "modality": "text",
     "is_free": True, "daily_token_limit": 1000000000, "smart_estimate": True},
    {"id": "zzbt/echo-nso", "name": "mock-echo-nso", "provider_id": "zzmock", "modality": "text",
     "is_free": True, "daily_token_limit": 1000000000, "no_stream_options": True},
    # T22 Headroom 选配插件：勾选压缩的模型（总开关默认关，不影响其他用例；方案A按模型勾选）
    {"id": "zzbt/echo-hr", "name": "mock-echo-hr", "provider_id": "zzmock", "modality": "text",
     "is_free": True, "daily_token_limit": 1000000000, "headroom": True},
    {"id": "zzbt/echo-gift", "name": "mock-echo-gift", "provider_id": "zzark", "modality": "text",
     "is_free": True, "token_type": "gift", "daily_token_limit": 266},
    {"id": "zzbt/echo-rpm", "name": "mock-echo-rpm", "provider_id": "zzmock", "modality": "text",
     "is_free": True, "daily_token_limit": 1000000000, "rpm_limit": 1},
    # T13 安全阀（v2.11.44）：echo-valve 上限 1000 便于边界运算；echo-nolimit 无上限验证阀门不生效
    {"id": "zzbt/echo-valve", "name": "mock-echo-valve", "provider_id": "zzmock", "modality": "text",
     "is_free": True, "daily_token_limit": 1000},
    {"id": "zzbt/echo-nolimit", "name": "mock-echo-nolimit", "provider_id": "zzmock", "modality": "text",
     "is_free": True},
    # T14 千帆搜索（qianfan_search 协议）：web_summary 形状 + 按次计费 + 安全阀预估=1
    {"id": "zzbt/echo-qf", "name": "mock-echo-qf", "provider_id": "zzqf", "modality": "text",
     "is_free": True, "daily_token_limit": 100, "billing_mode": "request"},
    # T14 千帆网页搜索（qianfan_web_search）：裸结果合成 chat 响应
    {"id": "zzbt/echo-qfws", "name": "mock-echo-qfws", "provider_id": "zzqf2", "modality": "text",
     "is_free": True, "billing_mode": "request"},
    # T23（v2.13.4）搜索总结专门 Key：qfws2 挂总结池 zzsump（caller_key 由用例内动态配置）
    {"id": "zzbt/echo-qfws2", "name": "mock-echo-qfws2", "provider_id": "zzqf2", "modality": "text",
     "is_free": True, "billing_mode": "request", "summary_pool": "zzsump"},
    {"id": "zzbt/echo-sum", "name": "mock-echo-sum", "provider_id": "zzmock", "modality": "text",
     "is_free": True, "daily_token_limit": 1000000000},
    # T15（问题30）确定性缺陷 400：池内两个候选，第一个必 400，验证不切换不冷却
    {"id": "zzbt/bad400-a", "name": "mock-bad400-a", "provider_id": "zzmock", "modality": "text", "is_free": True},
    {"id": "zzbt/bad400-b", "name": "mock-bad400-b", "provider_id": "zzmock", "modality": "text", "is_free": True},
    # T35（书生 image/jpg）图片格式不支持 400：可切换类——归一化后兜底池能接住，而非按请求缺陷直接透传
    {"id": "zzbt/imgbad-a", "name": "mock-imgbad-a", "provider_id": "zzmock", "modality": "text", "is_free": True},
    {"id": "zzbt/imgok-b", "name": "mock-echo-imgok", "provider_id": "zzmock", "modality": "text", "is_free": True},
    # T17 本地模型（local 令牌类型：不计费不限量仅记录）+ 切换窗口透传
    {"id": "zzbt/echo-local", "name": "mock-echo-local", "provider_id": "zzmock", "modality": "text", "is_free": True, "token_type": "local"},
    {"id": "zzbt/switch-a", "name": "mock-switching-a", "provider_id": "zzmock", "modality": "text", "is_free": True},
    {"id": "zzbt/switch-b", "name": "mock-echo-switch-b", "provider_id": "zzmock", "modality": "text", "is_free": True},
    # T24（v2.13.3）Switch 切换：local 侧=token_type local，net 侧=其余；sw-local2 带 RPM=1 验证同侧耗尽不跨侧
    {"id": "zzbt/sw-local", "name": "mock-sw-local", "provider_id": "zzmock", "modality": "text",
     "is_free": True, "token_type": "local"},
    {"id": "zzbt/sw-local2", "name": "mock-sw-local2", "provider_id": "zzmock", "modality": "text",
     "is_free": True, "token_type": "local", "rpm_limit": 1},
    {"id": "zzbt/sw-net", "name": "mock-sw-net", "provider_id": "zzmock", "modality": "text",
     "is_free": True, "daily_token_limit": 1000000000},
    # T26（缓存计费统计）：勾选 cost_enabled 的模型记录缓存命中/未命中/输出 token
    {"id": "zzbt/echo-cost", "name": "mock-echo-cost", "provider_id": "zzmock", "modality": "text",
     "is_free": True, "daily_token_limit": 1000000000, "cost_enabled": True},
    # T27（模态测速）：embedding/rerank 卡片「⚡测速」按模态分流（chat/completions 对其必然 400）
    {"id": "zzbt/echo-emb", "name": "mock-echo-emb", "provider_id": "zzmock", "modality": "embedding",
     "is_free": True},
    {"id": "zzbt/echo-rer", "name": "mock-echo-rer", "provider_id": "zzmock", "modality": "rerank",
     "is_free": True},
    # T31（v2.16.2）缓存亲和：第二个 embedding 条目（模态预筛后候选集 ≥2 才有亲和意义）
    {"id": "zzbt/echo-emb2", "name": "mock-echo-emb2", "provider_id": "zzmock", "modality": "embedding",
     "is_free": True},
    # T29（v2.15.0）付费令牌类型：all_day 全天可用 / idle_only 仅闲时可用（cost_peak.windows 同源互通）。
    # idle-block 高峰段 "0:00-23:59;23:59-0:00" 两段并集覆盖全天 1440 分钟（跨零点段补 23:59 这一分钟），
    # 任何时刻运行均被拦截；paid-allday 带同样的全天段验证 all_day 不受时段约束；idle-open 不设时段=默认全天闲时
    {"id": "zzbt/paid-allday", "name": "mock-paid-allday", "provider_id": "zzmock", "modality": "text",
     "is_free": False, "token_type": "all_day",
     "cost_peak": {"enabled": True, "windows": "0:00-23:59;23:59-0:00", "hit": 1, "miss": 2, "out": 4}},
    {"id": "zzbt/paid-idle-block", "name": "mock-paid-idle-block", "provider_id": "zzmock", "modality": "text",
     "is_free": False, "token_type": "idle_only",
     "cost_peak": {"enabled": False, "windows": "0:00-23:59;23:59-0:00", "hit": 0, "miss": 0, "out": 0}},
    {"id": "zzbt/paid-idle-open", "name": "mock-paid-idle-open", "provider_id": "zzmock", "modality": "text",
     "is_free": False, "token_type": "idle_only"},
    # T30（v2.16.0）Responses 工具往返：mock-toolcall 按 OpenAI 形状回 tool_calls（非流式/流式增量）
    {"id": "zzbt/echo-tool", "name": "mock-toolcall", "provider_id": "zzmock", "modality": "text",
     "is_free": True, "daily_token_limit": 1000000000},
    # T30f-h（v2.16.1）openai_responses 上游协议：mock 以 Responses 格式收发
    {"id": "zzbt/echo-resp", "name": "mock-resp", "provider_id": "zzmockr", "modality": "text",
     "is_free": True, "daily_token_limit": 1000000000},
    # T39（v2.16.10）余量熔断专用：大 daily_token_limit 防既有 token quota_exhausted 抢跑掩盖熔断原因
    {"id": "zzbt/echo-pq", "name": "mock-echo-pq", "provider_id": "zzmock", "modality": "text",
     "is_free": True, "daily_token_limit": 1000000000},
]
TEST_IDS = [m["id"] for m in TEST_MODELS]


def db_exec(sql, args=()):
    cur = DB.execute(sql, args)
    DB.commit()
    return cur


def deep_clean():
    c = json.load(open(os.path.join(REPO, "config.json"), encoding="utf-8"))
    c["providers"] = [p for p in c.get("providers", []) if p["id"] not in ("zzmock", "zzark", "zzqf", "zzqf2", "zzmockr")]
    c["models"] = [m for m in c.get("models", [])
                   if not str(m.get("id", "")).startswith("zzbt/") and str(m.get("id", "")) != "zzmock/probe-noauto"]
    c.get("pools", {}).pop("zzall", None)
    c.get("pools", {}).pop("zzdef", None)  # v2.11.19 曾漏清该测试池残留至生产配置
    for pn in ("zzreq", "zzonce", "zzsmart", "zznso", "zzgift", "zzrpm", "zzvalve", "zzvnl", "zzqfp", "zzqfp2",
               "zzqfws2", "zzsump", "zzbad", "zzlocal", "zzswitch", "zzhr", "zzhrctl", "zzsw", "zzsw2",
               "zzswsubl", "zzswsubn", "zzswnest", "zzswnestn", "zzcost", "zzpaid", "zzpaid2", "zzpaid3",
               "zzresp", "zztool", "zzrespp", "zzaff", "zzaffsw", "zzaffemb", "zzpq"):
        c.get("pools", {}).pop(pn, None)
    # T39：还原 quota_monitor 节点为生产原值（用例内改写 port/circuit_enabled，崩溃时兜底）
    if QM0:
        c["quota_monitor"] = json.loads(json.dumps(QM0, ensure_ascii=False))
    else:
        c.pop("quota_monitor", None)
    # T31：affinity 插件状态还原为未配置（default_enabled=false → 停用；生产用户后续自行开启）
    if isinstance(c.get("plugins"), dict):
        c["plugins"].pop("affinity", None)
    # T23：还原 search_summary 专门 Key 与抓取开关为生产原值（用例中途崩溃时兜底）
    ss = c.get("search_summary")
    if isinstance(ss, dict):
        if CK0:
            ss["caller_key"] = CK0
        else:
            ss.pop("caller_key", None)
        if "max_fetch_urls" in ss:
            ss["max_fetch_urls"] = 8
    # T22：Headroom 总开关还原为缺省（节点不存在=关），并清理统计行
    # （表由新代码实例的 init_db 创建；旧实例未建表时先补建，模式同 gift_state）
    db_exec("""CREATE TABLE IF NOT EXISTS headroom_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                caller TEXT DEFAULT '',
                pool TEXT DEFAULT '',
                model TEXT DEFAULT '',
                mode TEXT DEFAULT 'live',
                tokens_before INTEGER DEFAULT 0,
                tokens_after INTEGER DEFAULT 0,
                tokens_saved INTEGER DEFAULT 0,
                compression_ratio REAL DEFAULT 0,
                transforms TEXT DEFAULT '',
                latency_ms REAL DEFAULT 0,
                error TEXT DEFAULT '')""")
    c.pop("headroom", None)
    db_exec("DELETE FROM headroom_stats WHERE model LIKE 'zzbt/%'")
    json.dump(c, open(os.path.join(REPO, "config.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    q = " OR ".join([f"model_name='{i}'" for i in TEST_IDS])
    db_exec("""CREATE TABLE IF NOT EXISTS gift_state (
                model_name TEXT PRIMARY KEY,
                balance INTEGER DEFAULT 0,
                last_grant_date TEXT DEFAULT '')""")
    # v2.11.40 起 request_log 表退役（RPM/TPM 内存化），不再列入清理
    # 全新环境库中业务表尚未由 init_db 创建（网关从未启动过）：只清理已存在的表，缺表即零数据无可清理
    _tables = {r[0] for r in DB.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ["token_usage", "model_daily_stats", "call_metrics", "gift_state", "model_cache_stats"]:
        if t in _tables:
            db_exec(f"DELETE FROM {t} WHERE {q}")
    if "decision_log" in _tables:
        db_exec("DELETE FROM decision_log WHERE selected IN ({}) OR pool_name IN ('zzall','zzbad','zzlocal','zzswitch','zzsump','zzqfws2','zzsw','zzsw2','zzswsubl','zzswsubn','zzswnest','zzswnestn','zzpaid','zzpaid2','zzpaid3','zzresp','zztool','zzrespp','zzaff','zzaffsw','zzaffemb')".format(
            ",".join(chr(39) + i + chr(39) for i in TEST_IDS)))
    # T30（v2.16.0）：清理测试产生的 Responses 存档（response.model 命中 mock 测试模型名；生产行不受影响）
    if "responses_store" in _tables:
        db_exec("DELETE FROM responses_store WHERE response_json LIKE '%mock-echo-token%' OR response_json LIKE '%mock-toolcall%'")
    if "one_time_state" in _tables:
        db_exec("DELETE FROM one_time_state WHERE model_name='zzbt/echo-once'")
    if "api_keys" in _tables:
        db_exec("DELETE FROM api_keys WHERE name IN ('zzkey','zzkey2','zzkey3','zzkey4','zzkey9','zzkey10')")
        db_exec("DELETE FROM api_key_usage WHERE key_id NOT IN (SELECT id FROM api_keys)")
        db_exec("DELETE FROM api_key_hourly_usage WHERE key_id NOT IN (SELECT id FROM api_keys)")
    # T39（v2.16.10）余量监控：清理测试绑定（zz 前缀命名）及其快照/历史（级联一致性兜底）
    if "quota_bindings" in _tables:
        db_exec("DELETE FROM quota_bindings WHERE name LIKE 'zz-%'")
        db_exec("DELETE FROM quota_snapshots WHERE binding_id NOT IN (SELECT id FROM quota_bindings)")
        db_exec("DELETE FROM quota_history WHERE binding_id NOT IN (SELECT id FROM quota_bindings)")


def chat(model, effort=None, stream=False, content="hi", max_tokens=2000, auth=None, timeout=60):
    body = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": content}]}
    if effort is not None:
        body["reasoning_effort"] = effort
    if stream:
        body["stream"] = True
    h = dict(ADMIN)
    if auth:
        h["Authorization"] = "Bearer " + auth
    return httpx.post(f"{BASE}/v1/chat/completions", headers=h, json=body, timeout=timeout)


def token_used(mid):
    r = DB.execute("SELECT used_tokens FROM token_usage WHERE model_name=?", (mid,)).fetchone()
    return r["used_tokens"] if r else 0


def call_count(mid):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    # 按当天日期过滤（北京时间自然日，对齐 add_model_call 的 _bj_today）：
    # 否则跨天后 fetchone 命中旧行，当日增量恒为 0
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    r = DB.execute("SELECT request_count FROM model_daily_stats WHERE model_name=? AND date=?", (mid, today)).fetchone()
    return r["request_count"] if r else 0


def last_decision(selected):
    r = DB.execute("SELECT actual_tokens, estimated_tokens, id FROM decision_log WHERE selected=? ORDER BY id DESC LIMIT 1", (selected,)).fetchone()
    return dict(r) if r else None


def up_count(mid):
    """T24：mock 上游收到该模型 id 请求体的累计次数（增量断言路由去向）"""
    return sum(1 for b in captured_bodies if b.get("model") == mid)


def sw_chat(pool, switch=None, via="query", stream=False, content="hi", auth=None, timeout=60):
    """T24：POST /{池名} 切换请求。via=query 走 ?switch=，via=body 走 JSON 字段。"""
    body = {"max_tokens": 500, "messages": [{"role": "user", "content": content}]}
    body["model"] = pool  # 模拟 dsh 客户端带 model 字段（网关应改写为池名）
    if switch is not None and via == "body":
        body["switch"] = switch
    if stream:
        body["stream"] = True
    h = dict(ADMIN)
    if auth:
        h["Authorization"] = "Bearer " + auth
    url = f"{BASE}/{pool}" + (f"?switch={switch}" if (switch is not None and via == "query") else "")
    return httpx.post(url, headers=h, json=body, timeout=timeout)


def main():
    deep_clean()
    # 注册 mock provider + 测试模型 + 池
    c = json.load(open(os.path.join(REPO, "config.json"), encoding="utf-8"))
    c["providers"].append({"id": "zzmock", "name": "zzmock", "protocol": "openai",
                           "base_url": f"http://127.0.0.1:{MOCK_PORT}/v1", "api_key": "x"})
    # 余额返还制 v2.11.3+:显式 token_type="gift" 选择,不再做供应商名文字识别
    c["providers"].append({"id": "zzark", "name": "zz-普通供应商", "protocol": "openai",
                           "base_url": f"http://127.0.0.1:{MOCK_PORT}/v1", "api_key": "x"})
    c["providers"].append({"id": "zzmockr", "name": "zz-responses", "protocol": "openai_responses",
                           "base_url": f"http://127.0.0.1:{MOCK_PORT}", "api_key": "x"})
    c["providers"].append({"id": "zzqf", "name": "zz-千帆搜索", "protocol": "qianfan_search",
                           "base_url": f"http://127.0.0.1:{MOCK_PORT}", "api_key": "x"})
    c["providers"].append({"id": "zzqf2", "name": "zz-千帆网页搜索", "protocol": "qianfan_web_search",
                           "base_url": f"http://127.0.0.1:{MOCK_PORT}", "api_key": "x"})
    c["models"].extend(TEST_MODELS)
    c["pools"]["zzall"] = {"model_ids": TEST_IDS, "strategy": "sequential"}
    c["pools"]["zzreq"] = {"model_ids": ["zzbt/echo-req"], "strategy": "sequential"}
    c["pools"]["zzonce"] = {"model_ids": ["zzbt/echo-once"], "strategy": "sequential"}
    c["pools"]["zzsmart"] = {"model_ids": ["zzbt/echo-smart"], "strategy": "sequential"}
    c["pools"]["zznso"] = {"model_ids": ["zzbt/echo-nso"], "strategy": "sequential"}
    c["pools"]["zzgift"] = {"model_ids": ["zzbt/echo-gift"], "strategy": "sequential"}
    c["pools"]["zzrpm"] = {"model_ids": ["zzbt/echo-rpm"], "strategy": "sequential"}
    c["pools"]["zzvalve"] = {"model_ids": ["zzbt/echo-valve"], "strategy": "sequential"}
    c["pools"]["zzvnl"] = {"model_ids": ["zzbt/echo-nolimit"], "strategy": "sequential"}
    c["pools"]["zzqfp"] = {"model_ids": ["zzbt/echo-qf"], "strategy": "sequential"}
    c["pools"]["zzqfp2"] = {"model_ids": ["zzbt/echo-qfws"], "strategy": "sequential"}
    c["pools"]["zzqfws2"] = {"model_ids": ["zzbt/echo-qfws2"], "strategy": "sequential"}
    c["pools"]["zzsump"] = {"model_ids": ["zzbt/echo-sum"], "strategy": "sequential"}
    c["pools"]["zzbad"] = {"model_ids": ["zzbt/bad400-a", "zzbt/bad400-b"], "strategy": "sequential"}
    # T35：zzimgbad 带兜底池（图片格式 400 换候选后接住）；zzimgonly 无兜底（整链失败透传原文）
    c["pools"]["zzimgbad"] = {"model_ids": ["zzbt/imgbad-a"], "strategy": "sequential", "fallback_pool": "zzimgok"}
    c["pools"]["zzimgok"] = {"model_ids": ["zzbt/imgok-b"], "strategy": "sequential"}
    c["pools"]["zzimgonly"] = {"model_ids": ["zzbt/imgbad-a"], "strategy": "sequential"}
    c["pools"]["zzlocal"] = {"model_ids": ["zzbt/echo-local"], "strategy": "sequential"}
    c["pools"]["zzswitch"] = {"model_ids": ["zzbt/switch-a", "zzbt/switch-b"], "strategy": "sequential"}
    # T24 Switch 切换池：switch_enabled 直接写 config（管理端校验在 l 组单独验证）
    c["pools"]["zzsw"] = {"model_ids": ["zzbt/sw-local", "zzbt/sw-net"], "strategy": "sequential", "switch_enabled": True}
    c["pools"]["zzsw2"] = {"model_ids": ["zzbt/sw-local2", "zzbt/sw-net"], "strategy": "sequential", "switch_enabled": True}
    # T24n 子池穿透：default 拓扑——池内只有 pool: 子池引用（一侧纯本地、一侧纯云端），switch 跨子池选侧
    c["pools"]["zzswsubl"] = {"model_ids": ["zzbt/sw-local"], "strategy": "sequential"}
    c["pools"]["zzswsubn"] = {"model_ids": ["zzbt/sw-net"], "strategy": "sequential"}
    c["pools"]["zzswnest"] = {"model_ids": ["pool:zzswsubl", "pool:zzswsubn"], "strategy": "sequential", "switch_enabled": True}
    c["pools"]["zzswnestn"] = {"model_ids": ["pool:zzswsubn"], "strategy": "sequential", "switch_enabled": True}
    c["pools"]["zzhr"] = {"model_ids": ["zzbt/echo-hr"], "strategy": "sequential"}
    c["pools"]["zzhrctl"] = {"model_ids": ["zzbt/echo-token"], "strategy": "sequential"}  # T22 未勾选对照
    c["pools"]["zzcost"] = {"model_ids": ["zzbt/echo-cost"], "strategy": "sequential"}  # T26 缓存计费
    # T29 付费令牌类型：先拦后选（idle-block 高峰全遮蔽 → 落到 all_day）+ 无时段默认全天闲时
    c["pools"]["zzpaid"] = {"model_ids": ["zzbt/paid-idle-block", "zzbt/paid-allday"], "strategy": "sequential"}
    c["pools"]["zzpaid2"] = {"model_ids": ["zzbt/paid-idle-open"], "strategy": "sequential"}
    c["pools"]["zzpaid3"] = {"model_ids": ["zzbt/paid-allday"], "strategy": "sequential"}
    # T30 Responses API：echo-token 通用池 + mock-toolcall 工具池
    c["pools"]["zzresp"] = {"model_ids": ["zzbt/echo-token"], "strategy": "sequential"}
    c["pools"]["zztool"] = {"model_ids": ["zzbt/echo-tool"], "strategy": "sequential"}
    # T30f-h openai_responses 上游协议池
    c["pools"]["zzrespp"] = {"model_ids": ["zzbt/echo-resp"], "strategy": "sequential"}
    # T31（v2.16.2）缓存亲和路由：load_balance 池（无亲和时轮询打散 = 缓存 miss 场景）、
    # switch 池（定向请求旁路验证）、embedding 池（模态预筛后候选 ≥2）
    c["pools"]["zzaff"] = {"model_ids": ["zzbt/echo-token", "zzbt/echo-req", "zzbt/echo-smart"],
                           "strategy": "sequential", "load_balance": True}
    c["pools"]["zzaffsw"] = {"model_ids": ["zzbt/sw-local", "zzbt/sw-local2", "zzbt/sw-net"],
                             "strategy": "sequential", "switch_enabled": True}
    c["pools"]["zzaffemb"] = {"model_ids": ["zzbt/echo-emb2", "zzbt/echo-emb", "zzbt/echo-token"],
                              "strategy": "sequential"}
    # T39（v2.16.10）余量熔断：大限额专用模型池（provider=zzmock 供熔断命中）
    c["pools"]["zzpq"] = {"model_ids": ["zzbt/echo-pq"], "strategy": "sequential"}
    # T39：余量刷新端口用套件专用 18655——8655 可能被同机其他网关实例占用（占用时新实例优雅降级，
    # 套件的 8655 断言就会打到别人家实例上）；host 收敛 127.0.0.1 不对局域网开口
    c["quota_monitor"] = {"enabled": True, "interval_minutes": 30, "circuit_enabled": True,
                          "server_enabled": True, "host": "127.0.0.1", "port": 18655, "token": ""}
    json.dump(c, open(os.path.join(REPO, "config.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 启动隔离实例
    # 端口预清理（v2.16.5 安全化）：先 taskkill 再复扫，仍存活者=外部进程——
    # 报错中止而非误杀（教训：无差别强杀会杀掉 TV 助手类 node 代理，其守护数分钟内
    # 重占回归端口，套件请求全打到代理返回非 JSON，断言连挂——2026-10-09 实证）
    def _port_listeners(port, exclude_pid=None):
        out = []
        if os.name == "nt":
            _ns = subprocess.run(["netstat", "-ano"], capture_output=True).stdout.decode("utf-8", "ignore")
            for _ln in _ns.splitlines():
                if f":{port}" in _ln and "LISTENING" in _ln.upper():
                    _pid = _ln.split()[-1]
                    if exclude_pid is not None and _pid == str(exclude_pid):
                        continue  # 本进程自己的监听（mock 在模块导入即绑定）不进清理名单——否则自 Kill
                    out.append(_pid)
        return out
    try:
        if os.name == "nt":
            for _pid in _port_listeners(8678):
                subprocess.run(["taskkill", "/PID", _pid, "/F"], capture_output=True)
            for _pid in _port_listeners(MOCK_PORT, exclude_pid=os.getpid()):
                subprocess.run(["taskkill", "/PID", _pid, "/F"], capture_output=True)
        else:
            # Linux/macOS：fuser 按端口清理（psmisc；缺失则跳过——干净环境本就无占用）
            subprocess.run(["fuser", "-k", "8678/tcp"], capture_output=True)
            subprocess.run(["fuser", "-k", f"{MOCK_PORT}/tcp"], capture_output=True)
    except FileNotFoundError:
        pass                            # 清理工具缺失不阻塞套件：干净环境端口本就空闲
    time.sleep(1)
    # 复扫 8125（8678 复扫在下方）——孤儿套件进程（上次异常退出未释放）会以 SO_REUSEADDR
    # 与本次 mock 双绑定共存，请求被孤儿 mock 抢走 → captured_bodies 恒空（T8 一类假失败，
    # 见 docs/v2.16.9-问题修复报告.md「回归假失败」条）；外部进程占用则报错中止不误杀
    _left_mock = _port_listeners(MOCK_PORT, exclude_pid=os.getpid())
    if _left_mock:
        print(f"[suite] mock 端口 {MOCK_PORT} 仍被非本套件进程占用 PID={','.join(_left_mock)}，中止（请处理后重跑）", flush=True)
        sys.exit(1)
    time.sleep(1)
    _left = _port_listeners(8678)
    if _left:
        print(f"[suite] 回归端口 8678 仍被非本套件进程占用 PID={','.join(_left)}，中止（不误杀外部进程，请处理占用后重跑）", flush=True)
        sys.exit(1)
    env = dict(os.environ, MODEL_GATEWAY_PORT="8678", PYTHONFAULTHANDLER="1")
    os.makedirs(os.path.join(REPO, "logs"), exist_ok=True)  # 全新克隆无 logs/，子进程 stdout 落盘前先建
    proc = subprocess.Popen([sys.executable, "-m", "app.main"], cwd=REPO, env=env,
                            stdout=open(os.path.join(REPO, "logs", "regression_8678.log"), "ab"),
                            stderr=subprocess.STDOUT)
    try:
        up = False
        for _ in range(40):
            time.sleep(0.5)
            try:
                if httpx.get(f"{BASE}/health", timeout=2).status_code == 200:
                    up = True
                    break
            except Exception:
                pass
        if not up:
            raise RuntimeError("8678 实例未启动")
        httpx.post(f"{BASE}/admin/reload", headers=ADMIN, timeout=30)
        time.sleep(0.5)

        # ===== T1 流式正常 usage =====
        used0 = token_used("zzbt/echo-token")
        r = chat("zzall", stream=True)
        tail_ok = "[DONE]" in r.text
        d = last_decision("zzbt/echo-token")
        check("T1a 流式[DONE]+无think泄漏", r.status_code == 200 and tail_ok and "<think>" not in r.text, r.status_code)
        check("T1b 流式入账=133", token_used("zzbt/echo-token") - used0 == 133, token_used("zzbt/echo-token") - used0)
        check("T1c decision.actual_tokens=133", d and d["actual_tokens"] == 133, d)
        mrow = DB.execute("SELECT total_tokens FROM call_metrics WHERE model_name='zzbt/echo-token' ORDER BY id DESC LIMIT 1").fetchone()
        check("T1d call_metrics样本", mrow and mrow["total_tokens"] == 133, dict(mrow) if mrow else None)

        # ===== T2 流式缺失 usage =====
        used0 = token_used("zzbt/echo-token")
        cnt0 = call_count("zzbt/echo-token")
        r = chat("zzall", stream=True, content="nousage")
        d = last_decision("zzbt/echo-token")
        check("T2a 缺失usage不静默:actual=0", r.status_code == 200 and d and d["actual_tokens"] == 0, d)
        check("T2b 缺失usage仍记调用次数", call_count("zzbt/echo-token") - cnt0 == 1, call_count("zzbt/echo-token") - cnt0)
        check("T2c 缺失usage不计token", token_used("zzbt/echo-token") - used0 == 0, token_used("zzbt/echo-token") - used0)

        # ===== T3 request 计费型流式:预扣1次,settle不重复 =====
        used0 = token_used("zzbt/echo-req")
        r = chat("zzreq", stream=True)
        d = last_decision("zzbt/echo-req")
        delta = token_used("zzbt/echo-req") - used0
        check("T3a request型流式计费=1次", delta == 1, delta)
        check("T3b decision.actual_tokens=真实133", d and d["actual_tokens"] == 133, d)

        # ===== T4 one_time 流式:入账+到顶自动过期 =====
        r = chat("zzonce", stream=True)
        s1 = DB.execute("SELECT used_tokens, expired FROM one_time_state WHERE model_name='zzbt/echo-once'").fetchone()
        check("T4a one_time第1次入账133", s1 and s1["used_tokens"] == 133 and not s1["expired"], dict(s1) if s1 else None)
        r = chat("zzonce", stream=True)
        s2 = DB.execute("SELECT used_tokens, expired FROM one_time_state WHERE model_name='zzbt/echo-once'").fetchone()
        check("T4b 第2次后used=266且自动过期", s2 and s2["used_tokens"] == 266 and s2["expired"] == 1, dict(s2) if s2 else None)
        r = chat("zzonce", stream=True)
        check("T4c 过期后调用被拒(503无可用接口)", r.status_code == 503, r.status_code)

        # ===== T5 非流式:5项写入全部落库 =====
        used0 = token_used("zzbt/echo-token")
        mc0 = call_count("zzbt/echo-token")
        cm0 = DB.execute("SELECT count(*) c FROM call_metrics WHERE model_name='zzbt/echo-token'").fetchone()["c"]
        r = chat("zzall", max_tokens=700)
        d = last_decision("zzbt/echo-token")
        # v2.11.40 起 request_log 表退役，请求滑窗改经 /stats 的 current_rpm 断言（内存实现）
        st = httpx.get(f"{BASE}/stats", headers=ADMIN, timeout=15).json()
        srow = next((x for x in st.get("models", []) if x.get("id") == "zzbt/echo-token"), {})
        check("T5a 非流式200+content", r.status_code == 200 and (r.json().get("choices") or [{}])[0].get("message", {}).get("content"), r.status_code)
        check("T5b token_usage入账", token_used("zzbt/echo-token") - used0 == 133, token_used("zzbt/echo-token") - used0)
        check("T5c model_daily_stats调用+1", call_count("zzbt/echo-token") - mc0 == 1, call_count("zzbt/echo-token") - mc0)
        check("T5d 请求滑窗入账(current_rpm>=1)", srow.get("current_rpm", 0) >= 1, srow.get("current_rpm"))
        check("T5d2 滑窗tokens入账(current_tpm>=133)", srow.get("current_tpm", 0) >= 133, srow.get("current_tpm"))
        check("T5e call_metrics落库", DB.execute("SELECT count(*) c FROM call_metrics WHERE model_name='zzbt/echo-token'").fetchone()["c"] == cm0 + 1, "")
        check("T5f decision.actual_tokens=133", d and d["actual_tokens"] == 133, d)

        # ===== T6 用户 key 计费 =====
        r = httpx.post(f"{BASE}/admin/keys", headers=ADMIN,
                       json={"name": "zzkey", "type": "user", "allowed_pools": ["zzall"],
                             "token_type": "daily", "billing_mode": "token", "limit_amount": 1000000}, timeout=15)
        key_id = r.json().get("key", {}).get("id")
        secret = DB.execute("SELECT secret FROM api_keys WHERE name='zzkey'").fetchone()["secret"]
        u0 = DB.execute("SELECT used_amount FROM api_key_usage WHERE key_id=?", (key_id,)).fetchone()
        u0 = u0["used_amount"] if u0 else 0
        r = chat("zzall", auth=secret, max_tokens=500)
        u1 = DB.execute("SELECT used_amount FROM api_key_usage WHERE key_id=?", (key_id,)).fetchone()
        u1 = u1["used_amount"] if u1 else 0
        h1 = DB.execute("SELECT used_amount FROM api_key_hourly_usage WHERE key_id=?", (key_id,)).fetchone()
        check("T6a 用户key调用200", r.status_code == 200, r.status_code)
        check("T6b key_usage入账133", u1 - u0 == 133, u1 - u0)
        check("T6c hourly入账133", h1 and h1["used_amount"] == 133, dict(h1) if h1 else None)

        # ===== T7 并发10路(5流式+5非流式) =====
        used0 = token_used("zzbt/echo-token")
        import concurrent.futures
        def one(i):
            # 单请求网络层异常（满载机器上 mock backlog 溢出的间歇 RST）返回 0 交由断言判失败，
            # 不让异常炸出整轮套件（崩掉后 finally 半途滞留会毒化下一次运行——见 finally 注释）
            try:
                if i % 2 == 0:
                    return chat("zzall", stream=True, max_tokens=500).status_code
                return chat("zzall", max_tokens=500).status_code
            except Exception as _e:
                print(f"[suite] T7 并发单请求网络异常: {_e!r}", flush=True)
                return 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            codes = list(ex.map(one, range(10)))
        delta = token_used("zzbt/echo-token") - used0
        check("T7a 并发10路全部200", all(c == 200 for c in codes), codes)
        check("T7b 并发总入账=1330(无重复无丢失)", delta == 1330, delta)

        # ===== T8 思考回归(mock 透传) =====
        captured_bodies.clear()
        chat("zzall", effort="off", max_tokens=500)
        b = captured_bodies[-1]
        check("T8a off→thinking disabled", b.get("thinking") == {"type": "disabled"} and "reasoning_effort" not in b, b.get("thinking"))
        captured_bodies.clear()
        chat("zzall", effort="low", max_tokens=500)
        b = captured_bodies[-1]
        check("T8b low→reasoning_effort=low", b.get("reasoning_effort") == "low" and "thinking" not in b, b.get("reasoning_effort"))
        captured_bodies.clear()
        chat("zzall", effort="auto", max_tokens=500)
        b = captured_bodies[-1]
        check("T8c auto→不注入", "reasoning_effort" not in b and "thinking" not in b, {k: b.get(k) for k in ("reasoning_effort", "thinking")})
        captured_bodies.clear()
        mock_mode["think"] = True
        r = chat("zzall", stream=True, max_tokens=500)
        mock_mode["think"] = False
        check("T8d 流式<think>→reasoning_content增量", r.status_code == 200 and '"reasoning_content"' in r.text and "<think>" not in r.text, "")
        r = httpx.post(f"{BASE}/v1/messages", headers={**ADMIN, "anthropic-version": "2023-06-01"},
                       json={"model": "zzall", "max_tokens": 2000, "thinking": {"type": "disabled"},
                             "messages": [{"role": "user", "content": "hi"}]}, timeout=60)
        b = captured_bodies[-1]
        check("T8e anthropic入口disabled→thinking disabled", r.status_code == 200 and b.get("thinking") == {"type": "disabled"}, b.get("thinking"))

        # ===== T9 智能估算 =====
        m0 = httpx.get(f"{BASE}/admin/model/zzbt/echo-smart/metrics", headers=ADMIN, timeout=15).json()
        r = chat("zzsmart", max_tokens=500)
        m1 = httpx.get(f"{BASE}/admin/model/zzbt/echo-smart/metrics", headers=ADMIN, timeout=15).json()
        check("T9a smart模型metrics样本增长", r.status_code == 200 and m1.get("sample_count", 0) > m0.get("sample_count", 0), (m0, m1))
        d = last_decision("zzbt/echo-smart")
        check("T9b decision估算字段存在", d and d["estimated_tokens"] is not None, d)
        r = httpx.get(f"{BASE}/admin/decisions?limit=3", headers=ADMIN, timeout=15)
        j = r.json()
        arr = j.get("decisions") if isinstance(j, dict) else j
        check("T9c 决策API含actual_tokens", r.status_code == 200 and arr and all("actual_tokens" in x for x in arr[:3]), r.status_code)

        # ===== T10 no_stream_options 开关(问题21-B) =====
        captured_bodies.clear()
        r = chat("zznso", stream=True, max_tokens=500)
        b = captured_bodies[-1]
        check("T10a nso模型流式不含stream_options", r.status_code == 200 and "stream_options" not in b, b.get("stream_options", "(无)"))
        captured_bodies.clear()
        r = chat("zzall", stream=True, max_tokens=500)
        b = captured_bodies[-1]
        check("T10b 普通模型流式保留stream_options", r.status_code == 200 and b.get("stream_options") == {"include_usage": True}, b.get("stream_options"))

        # ===== T12 RPM 限速（v2.11.40 内存滑窗：填补 RPM/TPM 触顶从未有回归的空洞）=====
        r = chat("zzrpm", max_tokens=500)
        check("T12a rpm_limit=1 第1次200", r.status_code == 200, r.status_code)
        r = chat("zzrpm", max_tokens=500)
        check("T12b rpm触顶第2次被拒(503)", r.status_code == 503, r.status_code)
        r = httpx.get(f"{BASE}/admin/decisions?limit=5", headers=ADMIN, timeout=15)
        j = r.json()
        arr = j.get("decisions") if isinstance(j, dict) else j
        zz = next((x for x in (arr or []) if x.get("pool_name") == "zzrpm"), None)
        check("T12c 决策记录rpm_limited原因(内存滑窗驱动预检)",
              zz is not None and any(s.get("reason") == "rpm_limited" for s in (zz.get("steps") or [])),
              (zz or {}).get("steps"))

        # ===== T11 余额返还制（火山/Ark 供应商自动识别）=====
        r = httpx.get(f"{BASE}/admin/models", headers=ADMIN, timeout=15)
        mj = {x["id"]: x for x in r.json()["models"]}
        check("T11a 显式token_type=gift生效/普通daily不受影响",
              mj.get("zzbt/echo-gift", {}).get("gift_refund") is True
              and mj.get("zzbt/echo-token", {}).get("gift_refund") is False,
              {k: mj.get(k, {}).get("gift_refund") for k in ("zzbt/echo-gift", "zzbt/echo-token")})

        def gift_bal():
            row = DB.execute("SELECT balance FROM gift_state WHERE model_name='zzbt/echo-gift'").fetchone()
            return row["balance"] if row else None

        r = chat("zzgift", max_tokens=500)  # 首次预检惰性初始化 266 → 扣 133
        check("T11b 首次调用200且余额=266-133", r.status_code == 200 and gift_bal() == 133, gift_bal())
        r = chat("zzgift", max_tokens=500)  # 133 → 0
        check("T11c 第二次调用后余额归零", r.status_code == 200 and gift_bal() == 0, gift_bal())
        r = chat("zzgift", max_tokens=500)  # 余额 0 → 预检拒绝
        check("T11d 余额耗尽调用被拒(503)", r.status_code == 503, r.status_code)
        # 模拟"昨日用量赠还"（火山语义：到账时刻补回 min(昨日自然日用量, 上限)）
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        yday = (datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)).isoformat()
        db_exec("""INSERT INTO model_daily_stats (model_name, date, request_count, total_tokens)
                   VALUES ('zzbt/echo-gift', ?, 2, 133)
                   ON CONFLICT(model_name, date) DO UPDATE SET total_tokens = 133, request_count = 2""", (yday,))
        db_exec("UPDATE gift_state SET balance = 1, last_grant_date = ? WHERE model_name = 'zzbt/echo-gift'", (yday,))
        httpx.post(f"{BASE}/admin/reload", headers=ADMIN, timeout=30)  # 清 5s 配额预检缓存
        time.sleep(0.5)
        # v2.11.19：预检改"今日已采（自然日消耗）≥ 本地上限"口径，余额账本仅作展示不再作为预检依据
        r = chat("zzgift", max_tokens=500)
        check("T11e 今日已采达上限调用被拒(与余额账本无关)", r.status_code == 503, r.status_code)
        # v2.11.30：人工校准（独立窗口后端）——单独修正三项，余额 = 昨日剩余+今日返还-今日使用
        r = httpx.post(f"{BASE}/admin/gift/calibrate", headers=ADMIN,
                       json={"model_id": "zzbt/echo-gift", "yesterday_leftover": 400,
                             "grant_today": 133, "usage_today": 100})
        check("T11e2 人工校准接口生效(余额=400+133-100=433)",
              r.status_code == 200 and r.json().get("balance") == 433, r.text[:80])
        # 诊断转储（临时）
        import json as _json
        _st = httpx.get(f"{BASE}/stats", headers=ADMIN, timeout=15).json()
        _gm = next((x for x in _st.get("models", []) if x.get("id") == "zzbt/echo-gift"), {})
        print("诊断[T11e2后] stats:", {k: _gm.get(k) for k in ("gift_balance", "gift_yesterday_leftover", "gift_last_grant_amount", "gift_usage_today")})
        print("诊断[T11e2后] gift_state:", DB.execute("SELECT balance, yesterday_leftover, last_grant_amount, grant_date, snapshot_date FROM gift_state WHERE model_name='zzbt/echo-gift'").fetchone())
        print("诊断[T11e2后] usage:", DB.execute("SELECT date, total_tokens FROM model_daily_stats WHERE model_name='zzbt/echo-gift' ORDER BY date DESC LIMIT 2").fetchall())
        # v2.11.34：人工校准优先于系统统计——校准值同步改写自然日统计并即刻可见
        _tdy = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        _urow = DB.execute("SELECT total_tokens FROM model_daily_stats WHERE model_name='zzbt/echo-gift' AND date=?", (_tdy,)).fetchone()
        check("T11e2b 校准今日使用量同步改写今日统计(已采/明日发放基数=100)",
              _urow is not None and _urow["total_tokens"] == 100, dict(_urow) if _urow else None)
        check("T11e2c 校准注记即时可见(gift_cal_grant=133/gift_cal_usage=100)",
              _gm.get("gift_cal_grant") == 133 and _gm.get("gift_cal_usage") == 100,
              {k: _gm.get(k) for k in ("gift_cal_grant", "gift_cal_usage")})
        r = chat("zzgift", max_tokens=500)
        check("T11e3 校准后(今日耗100<上限266)调用成功", r.status_code == 200, r.status_code)
        check("T11e4 校准后余额=433-133=300", gift_bal() == 300, gift_bal())
        r = httpx.get(f"{BASE}/stats", headers=ADMIN, timeout=15)
        row = next((x for x in r.json().get("models", []) if x.get("id") == "zzbt/echo-gift"), {})
        check("T11f stats含gift_balance/昨日剩余展示",
              row.get("gift_refund") is True and row.get("gift_balance") == 300
              and row.get("gift_yesterday_leftover") == 400,
              {k: row.get(k) for k in ("gift_balance", "gift_yesterday_leftover")})
        # v2.11.34：真实消耗叠加在校准基数上（自校准时刻起按新值累计，不重算消失）；总额池字段
        check("T11g 校准基数上叠加真实消耗(今日统计=100+133=233)",
              row.get("gift_usage_today") == 233 and row.get("today_tokens") == 233,
              {k: row.get(k) for k in ("gift_usage_today", "today_tokens")})
        check("T11h 当前可用总额池=昨日剩余+今日已补(400+133=533)",
              row.get("gift_pool") == 533, row.get("gift_pool"))

        # ===== T13 使用量安全阀（v2.11.44：已用+预估 ≥ k×最大量限制 → 跳过路由）=====
        def set_used(model, used):
            # 直写 token_usage 并对齐懒重置的 last_reset_date（否则 get_daily_usage 会清零）
            _tdy = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
            db_exec("""INSERT INTO token_usage (model_name, used_tokens, last_reset_date, refresh_time)
                       VALUES (?, ?, ?, '')
                       ON CONFLICT(model_name) DO UPDATE SET used_tokens = excluded.used_tokens,
                                                            last_reset_date = excluded.last_reset_date""",
                    (model, used, _tdy))
            httpx.post(f"{BASE}/admin/reload", headers=ADMIN, timeout=30)  # 清 5s 配额预检缓存
            time.sleep(0.5)

        def reject_steps(pool):
            r = httpx.get(f"{BASE}/admin/decisions?limit=5", headers=ADMIN, timeout=15)
            j = r.json()
            arr = j.get("decisions") if isinstance(j, dict) else j
            zz = next((x for x in (arr or []) if x.get("pool_name") == pool), None)
            return (zz or {}).get("steps") or []

        # T13a k=100（缺省）即计入预估：used=950<1000 旧线不触发，中文 est=101 越线 → 纯阀门拦截
        set_used("zzbt/echo-valve", 950)
        r = chat("zzvalve", content="测" * 100)  # est_input=101 → (950+101)*100 ≥ 1000*100
        check("T13a k=100计入预估(used+est越线503)", r.status_code == 503, r.status_code)
        check("T13a2 决策记录valve_exceeded",
              any(s.get("reason") == "valve_exceeded" for s in reject_steps("zzvalve")),
              [s.get("reason") for s in reject_steps("zzvalve")])

        # T13b 未校准=仅输入口径（防 max_tokens 虚高误杀，v2.10.6 教训）：est=2 不越线 → 放行
        set_used("zzbt/echo-valve", 950)
        r = chat("zzvalve", content="hi")  # (950+2)*100=95200 < 100000；输出 133 不计入预估
        check("T13b 未校准仅输入口径不误杀(200)", r.status_code == 200, r.status_code)

        # T13c k=80 红线语义：线内放行、触线拒绝（used<1000 旧线未触发，纯阀门）
        r = httpx.put(f"{BASE}/admin/models/zzbt/echo-valve", headers=ADMIN, json={"valve_pct": 80}, timeout=15)
        check("T13c0 PUT valve_pct=80 保存生效",
              r.status_code == 200 and r.json().get("model", {}).get("valve_pct") == 80, r.text[:80])
        httpx.post(f"{BASE}/admin/reload", headers=ADMIN, timeout=30)
        time.sleep(0.5)
        set_used("zzbt/echo-valve", 700)
        r = chat("zzvalve", content="hi")  # (700+2)*100=70200 < 80000
        check("T13c1 k=80 线内放行(702<800)", r.status_code == 200, r.status_code)
        r = chat("zzvalve", content="hi")  # used=833 → (833+2)*100=83500 ≥ 80000
        check("T13c2 k=80 触线拒绝(833+est≥800)", r.status_code == 503, r.status_code)
        check("T13c3 触线原因=valve_exceeded(非quota_exhausted)",
              any(s.get("reason") == "valve_exceeded" for s in reject_steps("zzvalve")),
              [s.get("reason") for s in reject_steps("zzvalve")])

        # T13d 校准 EMA×1.1：注入 12 条 completion=50 样本 → est=2+55=57，used=950 时被拦
        # （未校准同状态 95200<100000 会放行——T13b 已证——差异即校准贡献）
        db_exec("DELETE FROM call_metrics WHERE model_name='zzbt/echo-valve'")
        for _ in range(12):
            db_exec("""INSERT INTO call_metrics (model_name, ts, estimated_tokens, prompt_tokens,
                       completion_tokens, total_tokens, max_tokens, latency_ms)
                       VALUES ('zzbt/echo-valve', 0, 0, 0, 50, 150, 0, 100)""")
        httpx.put(f"{BASE}/admin/models/zzbt/echo-valve", headers=ADMIN, json={"valve_pct": 100}, timeout=15)
        httpx.post(f"{BASE}/admin/reload", headers=ADMIN, timeout=30)
        time.sleep(0.5)
        set_used("zzbt/echo-valve", 950)
        r = chat("zzvalve", content="hi")  # (950+2+55)*100=100700 ≥ 100000
        check("T13d 校准EMA×1.1提前拦截(957≥1000)", r.status_code == 503, r.status_code)

        # T13e k=0 = 停用（任何请求都拒绝）
        httpx.put(f"{BASE}/admin/models/zzbt/echo-valve", headers=ADMIN, json={"valve_pct": 0}, timeout=15)
        httpx.post(f"{BASE}/admin/reload", headers=ADMIN, timeout=30)
        time.sleep(0.5)
        set_used("zzbt/echo-valve", 0)
        r = chat("zzvalve", content="hi")
        check("T13e k=0 停用该模型(503)", r.status_code == 503, r.status_code)

        # T13f 上限 0（不限量）阀门不生效：无 snap，valve_pct=0 也不拦（防 k×0=0 全拦误杀）
        r = httpx.put(f"{BASE}/admin/models/zzbt/echo-nolimit", headers=ADMIN, json={"valve_pct": 0}, timeout=15)
        check("T13f0 nolimit PUT生效", r.status_code == 200, r.status_code)
        httpx.post(f"{BASE}/admin/reload", headers=ADMIN, timeout=30)
        time.sleep(0.5)
        r = chat("zzvnl", content="hi")
        check("T13f 上限0不限量阀门不生效(200)", r.status_code == 200, r.status_code)

        # T13g /stats 带出 valve_pct（统计卡片画红线/显示系数的数据源）
        r = httpx.get(f"{BASE}/stats", headers=ADMIN, timeout=15)
        rows = {x.get("id"): x for x in r.json().get("models", [])}
        check("T13g /stats返回valve_pct",
              rows.get("zzbt/echo-valve", {}).get("valve_pct") == 0
              and rows.get("zzbt/echo-nolimit", {}).get("valve_pct") == 0,
              {k: rows.get(k, {}).get("valve_pct") for k in ("zzbt/echo-valve", "zzbt/echo-nolimit")})

        # ===== T14 千帆搜索协议（qianfan_search：web_summary 形状 + 按次计费 + 安全阀预估=1）=====
        _n = len(captured_bodies)
        r = chat("zzqfp", content="今天的新闻")
        check("T14a 千帆搜索非流式200", r.status_code == 200, r.status_code)
        j = r.json()
        check("T14b 非流式content正常",
              (j.get("choices") or [{}])[0].get("message", {}).get("content") == "答案", str(j)[:200])
        check("T14c 非流式references透传",
              isinstance(j.get("references"), list) and j["references"][0].get("title") == "参考1",
              str(j.get("references"))[:120])
        qb = captured_bodies[-1] if len(captured_bodies) > _n else {}
        check("T14d 上游payload无model/stream_options",
              qb.get("messages") and "model" not in qb and "stream_options" not in qb, str(qb)[:200])
        check("T14e 非流式按次计费=1", token_used("zzbt/echo-qf") == 1, token_used("zzbt/echo-qf"))

        # 流式：references 随 usage 块行级透传；request 模式首块预扣、结算不重复计费
        r = chat("zzqfp", stream=True)
        check("T14f 千帆搜索流式200", r.status_code == 200, r.status_code)
        check("T14g 流式references透传", '"references"' in r.text and "参考1" in r.text, r.text[-200:])
        check("T14h 流式按次不重复计费(共2)", token_used("zzbt/echo-qf") == 2, token_used("zzbt/echo-qf"))

        # 安全阀：按次预估记 1 —— used=99 时配额未耗尽（99<100）但 (99+1)×100 ≥ 100×100 触阀门
        set_used("zzbt/echo-qf", 99)
        r = chat("zzqfp", content="hi")
        check("T14i used=99安全阀拒绝(503)", r.status_code == 503, r.status_code)
        check("T14i2 决策记录valve_exceeded",
              any(s.get("reason") == "valve_exceeded" for s in reject_steps("zzqfp")),
              [s.get("reason") for s in reject_steps("zzqfp")])

        # /stats 带出按次单位与计费模式（统计卡片"次"标签数据源）
        r = httpx.get(f"{BASE}/stats", headers=ADMIN, timeout=15)
        rows = {x.get("id"): x for x in r.json().get("models", [])}
        check("T14j /stats单位为次",
              rows.get("zzbt/echo-qf", {}).get("unit") == "次"
              and rows.get("zzbt/echo-qf", {}).get("billing_mode") == "request",
              {k: rows.get("zzbt/echo-qf", {}).get(k) for k in ("unit", "billing_mode")})

        # web_search 变体（百度搜索裸结果 → 合成 chat 响应）
        _n2 = len(captured_bodies)
        r = chat("zzqfp2", content="news")
        check("T14k 网页搜索非流式200", r.status_code == 200, r.status_code)
        j = r.json()
        check("T14l 裸结果合成content含参考",
              "参考1" in ((j.get("choices") or [{}])[0].get("message", {}).get("content") or ""), str(j)[:200])
        check("T14m 合成响应references透传",
              isinstance(j.get("references"), list) and len(j.get("references")) == 2,
              str(j.get("references"))[:120])
        check("T14n 网页搜索按次计费=1", token_used("zzbt/echo-qfws") == 1, token_used("zzbt/echo-qfws"))
        r = chat("zzqfp2", stream=True)
        check("T14o 网页搜索流式200含参考", r.status_code == 200 and "参考1" in r.text and "[DONE]" in r.text,
              r.text[-200:])
        qb2 = captured_bodies[-1] if len(captured_bodies) > _n2 else {}
        check("T14p web_search上游payload无stream/model键",
              qb2.get("messages") and "stream" not in qb2 and "model" not in qb2, str(qb2)[:200])

        # ===== T15（问题30）确定性缺陷400快速失败：不冷却、不切换第二候选、400原文透传 =====
        n_b0 = sum(1 for b in captured_bodies if b.get("model") == "mock-bad400-b")
        r = chat("zzbad", content="hi")
        check("T15a 缺陷400原样透传客户端(400)", r.status_code == 400, r.status_code)
        check("T15b 透传含上游错误码", "InvalidParameter" in r.text, r.text[:200])
        n_b1 = sum(1 for b in captured_bodies if b.get("model") == "mock-bad400-b")
        check("T15c 未切换第二候选(坏请求不整池重演)", n_b1 == n_b0, (n_b0, n_b1))
        r2 = chat("zzbad", content="hi")
        check("T15d 不冷却(立即重试仍达上游拿400而非503)", r2.status_code == 400, r2.status_code)
        steps15 = reject_steps("zzbad")
        check("T15e 决策记录request_defect_400",
              any(s.get("reason") == "request_defect_400" and (s.get("detail") or {}).get("no_cooldown")
                  for s in steps15), [s.get("reason") for s in steps15])

        # ===== T16（问题31）连接池幽灵租约自愈：连续3次 PoolTimeout 重建 client =====
        from app.gateway import pool as _poolmod  # v2.14.0 起 pool.py 归 app/gateway/
        _mp = _poolmod.ModelPool.__new__(_poolmod.ModelPool)  # 跳过 __init__，仅测自愈逻辑
        _mp.providers_cache = {}
        _e = _poolmod.ModelEntry(id="zzbt/echo-token", name="m", provider="openai",
                                 base_url=f"http://127.0.0.1:{MOCK_PORT}/v1", api_key="x")
        _old = _poolmod.OpenAIProvider(_e.base_url, _e.api_key)
        _mp.providers_cache[_e.id] = _old

        async def _t16_scenario():
            await _mp._note_pool_timeout(_e)
            await _mp._note_pool_timeout(_e)
            _mid = _mp.providers_cache.get(_e.id)  # 阈值内：不重建
            await _mp._note_pool_timeout(_e)      # 第3次：触发重建
            return _mid

        _mid = asyncio.run(_t16_scenario())
        check("T16a 连续3次触发重建(新client≠旧client)",
              _mid is _old and _mp.providers_cache.get(_e.id) is not _old,
              ("mid_is_old", _mid is _old))
        check("T16b 触发后计数清零", _e.pool_timeout_streak == 0, _e.pool_timeout_streak)
        check("T16c 重建后cache可用(新client就位)", _e.id in _mp.providers_cache, list(_mp.providers_cache))

        # ===== T17 本地模型（local 令牌类型：不计费不限量仅记录）+ 切换窗口透传 =====
        used17 = token_used("zzbt/echo-local")
        r = chat("zzlocal", content="hi")
        check("T17a 本地模型200放行", r.status_code == 200, r.status_code)
        check("T17b 本地模型usage照记(+133)", token_used("zzbt/echo-local") - used17 == 133,
              token_used("zzbt/echo-local") - used17)
        r2 = chat("zzlocal", content="hi")
        r3 = chat("zzlocal", content="hi")
        check("T17c 本地模型无配额拦截(连续3次200)", r2.status_code == 200 and r3.status_code == 200,
              (r2.status_code, r3.status_code))
        rs = httpx.get(f"{BASE}/stats", headers=ADMIN, timeout=15)
        rows17 = {x.get("id"): x for x in rs.json().get("models", [])}
        check("T17d stats带出local类型与用量",
              rows17.get("zzbt/echo-local", {}).get("token_type") == "local"
              and rows17.get("zzbt/echo-local", {}).get("today_tokens", 0) > 0,
              {k: rows17.get("zzbt/echo-local", {}).get(k) for k in ("token_type", "today_tokens")})
        nb0 = sum(1 for b in captured_bodies if b.get("model") == "mock-echo-switch-b")
        rw = chat("zzswitch", content="hi")
        check("T17e 切换中503透传客户端(qwen_switching)", rw.status_code == 503 and "qwen_switching" in rw.text,
              rw.text[:150])
        check("T17f retry_after保留", "retry_after" in rw.text, rw.text[:150])
        rw2 = chat("zzswitch", content="hi")
        check("T17g 不冷却(立即重试仍透传切换错误)", rw2.status_code == 503 and "qwen_switching" in rw2.text,
              rw2.text[:150])
        nb1 = sum(1 for b in captured_bodies if b.get("model") == "mock-echo-switch-b")
        check("T17h 未切换第二候选", nb1 == nb0, (nb0, nb1))
        steps17 = reject_steps("zzswitch")
        check("T17i 决策记录switching_passthrough",
              any(s.get("reason") == "switching_passthrough"
                  and (s.get("detail") or {}).get("no_cooldown") for s in steps17),
              [s.get("reason") for s in steps17])

        # ===== T18（v2.12.3）千帆搜索两协议结构统一：同一请求下 summary/web_search 变体响应结构完全一致 =====
        # 先清 T14i 遗留的安全阀用量，避免阀门把 T18 请求拦在预检（对两变体一视同仁地清零）
        set_used("zzbt/echo-qf", 0)
        set_used("zzbt/echo-qfws", 0)
        u18qf, u18ws = token_used("zzbt/echo-qf"), token_used("zzbt/echo-qfws")

        def _sse_frames(text):
            """逐帧解析 SSE 文本：返回 (数据帧对象列表, 是否有 data: [DONE])。"""
            frames, done = [], False
            for blk in text.split("\n\n"):
                blk = blk.strip()
                if not blk.startswith("data: "):
                    continue
                payload = blk[6:].strip()
                if payload == "[DONE]":
                    done = True
                    continue
                try:
                    frames.append(json.loads(payload))
                except Exception:
                    pass
            return frames, done

        def _frame_sig(f):
            """帧信封形状签名（忽略值）：顶层键 + choices[0] 键 + delta 键。"""
            ch = f.get("choices") or []
            c0 = ch[0] if ch and isinstance(ch[0], dict) else {}
            d = c0.get("delta") if isinstance(c0.get("delta"), dict) else {}
            return (tuple(sorted(f.keys())), tuple(sorted(c0.keys())), tuple(sorted(d.keys())))

        def _dedup(seq):
            """连续同形帧去重（正文 delta 帧数允许随上游分片不同，帧形序列必须一致）。"""
            out = []
            for s in seq:
                if not out or out[-1] != s:
                    out.append(s)
            return out

        # 非流式：顶层键集合 / references / choices[0] / usage
        r18a = chat("zzqfp", content="今天的新闻")
        r18b = chat("zzqfp2", content="news")
        j18a, j18b = r18a.json(), r18b.json()
        check("T18a 两变体非流式顶层键集合一致(object=chat.completion)",
              r18a.status_code == 200 and r18b.status_code == 200
              and set(j18a) == set(j18b) and j18a.get("object") == j18b.get("object") == "chat.completion",
              (r18a.status_code, r18b.status_code, sorted(j18a), sorted(j18b)))
        check("T18b 两变体非流式references均为list(不得一边null一边[])",
              isinstance(j18a.get("references"), list) and isinstance(j18b.get("references"), list),
              (type(j18a.get("references")).__name__, type(j18b.get("references")).__name__))
        c18a = (j18a.get("choices") or [{}])[0]
        c18b = (j18b.get("choices") or [{}])[0]
        check("T18c 两变体choices[0]/message键集合与finish_reason一致",
              set(c18a) == set(c18b) and set(c18a.get("message") or {}) == set(c18b.get("message") or {})
              and c18a.get("finish_reason") == c18b.get("finish_reason") == "stop",
              (sorted(c18a), sorted(c18b), c18a.get("finish_reason"), c18b.get("finish_reason")))
        check("T18d 两变体usage均存在且键集合一致",
              set(j18a.get("usage") or {}) == set(j18b.get("usage") or {})
              == {"prompt_tokens", "completion_tokens", "total_tokens"},
              (j18a.get("usage"), j18b.get("usage")))

        # 流式：逐帧解析比对帧形（值可不同：model/request_id/content/references 条数）
        r18s1 = chat("zzqfp", stream=True)
        r18s2 = chat("zzqfp2", stream=True)
        f18a, done18a = _sse_frames(r18s1.text)
        f18b, done18b = _sse_frames(r18s2.text)
        check("T18e 两变体流式首帧键集合一致(request_id/model/choices/references)",
              bool(f18a) and bool(f18b)
              and set(f18a[0]) == set(f18b[0]) == {"request_id", "model", "choices", "references"},
              (sorted(f18a[0]) if f18a else None, sorted(f18b[0]) if f18b else None))
        check("T18f 两变体流式帧形序列一致(忽略值/正文帧数,连续同形去重)",
              _dedup([_frame_sig(f) for f in f18a]) == _dedup([_frame_sig(f) for f in f18b]),
              ([_frame_sig(f)[0] for f in f18a], [_frame_sig(f)[0] for f in f18b]))
        check("T18g 两变体流式末帧finish_reason=stop",
              bool(f18a) and bool(f18b)
              and (f18a[-1].get("choices") or [{}])[0].get("finish_reason") == "stop"
              and (f18b[-1].get("choices") or [{}])[0].get("finish_reason") == "stop",
              ((f18a[-1].get("choices") or [{}])[0].get("finish_reason") if f18a else None,
               (f18b[-1].get("choices") or [{}])[0].get("finish_reason") if f18b else None))
        check("T18h 两变体流式均以data: [DONE]收尾", done18a and done18b, (done18a, done18b))
        check("T18i 两变体references均仅首帧携带(统一帧形契约)",
              all("references" not in f for f in f18a[1:]) and all("references" not in f for f in f18b[1:]),
              ([("references" in f) for f in f18a], [("references" in f) for f in f18b]))
        check("T18j 两变体流式均无usage帧(统一契约:搜索流不计token,按次预扣计费)",
              all("usage" not in f for f in f18a) and all("usage" not in f for f in f18b),
              ([("usage" in f) for f in f18a], [("usage" in f) for f in f18b]))

        # 计费路径一致：billing_mode=request 下非流式入账+1、流式建立预扣+1，两变体各共+2
        check("T18k 两变体计费路径一致(非流式+流式各+1,共+2)",
              token_used("zzbt/echo-qf") - u18qf == 2 and token_used("zzbt/echo-qfws") - u18ws == 2,
              (token_used("zzbt/echo-qf") - u18qf, token_used("zzbt/echo-qfws") - u18ws))

        # ===== T19 思考参数探测计量：探测请求消耗计入模型用量账本 =====
        c = json.load(open(os.path.join(REPO, "config.json"), encoding="utf-8"))
        if not any(p["id"] == "zzmock" for p in c["providers"]):
            c["providers"].append({"id": "zzmock", "name": "zzmock", "protocol": "openai",
                                   "base_url": f"http://127.0.0.1:{MOCK_PORT}/v1", "api_key": "x"})
        c["models"] = [m for m in c["models"] if m.get("id") not in ("zzbt/echo-probe", "zzbt/echo-probe-req")]
        c["models"] += [
            # 无 reasoning_map → 探测必发真实上游；mock 非流式每笔 usage total=133
            {"id": "zzbt/echo-probe", "name": "mock-echo-probe", "provider_id": "zzmock",
             "modality": "text", "is_free": True, "token_type": "daily", "daily_token_limit": 1000000000},
            {"id": "zzbt/echo-probe-req", "name": "mock-echo-probe-req", "provider_id": "zzmock",
             "modality": "text", "is_free": True, "daily_token_limit": 1000000000, "billing_mode": "request"},
        ]
        json.dump(c, open(os.path.join(REPO, "config.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        httpx.post(f"{BASE}/admin/reload", headers=ADMIN, timeout=30)
        time.sleep(0.5)

        def _probe_hits(name):  # mock 实收到的该上游名探测请求数（每笔均带 usage=133）
            return sum(1 for b in captured_bodies if b.get("model") == name)

        def _wait_probe_settled(name, t0):
            """探测在后台线程执行：等 mock 收到的探测请求数连续 2 秒不再增长（即已收尾）。"""
            n, stable = _probe_hits(name), 0
            while time.time() - t0 < 90:
                time.sleep(1)
                n2 = _probe_hits(name)
                if n2 > 0 and n2 == n:
                    stable += 1
                    if stable >= 2:
                        return n2
                else:
                    stable = 0
                n = n2
            return _probe_hits(name)

        for _mid, _mname, _per in (("zzbt/echo-probe", "mock-echo-probe", 133),
                                   ("zzbt/echo-probe-req", "mock-echo-probe-req", 1)):
            u0, c0 = token_used(_mid), call_count(_mid)
            t0 = time.time()
            r = httpx.post(f"{BASE}/admin/reasoning/probe", headers=ADMIN, json={"model_id": _mid}, timeout=15)
            check(f"T19a {_mid} 探测受理(queued)", r.status_code == 200 and r.json().get("queued") is True, r.text[:120])
            n = _wait_probe_settled(_mname, t0)
            check(f"T19b {_mid} 探测真实发上游(n={n})", n > 0, n)
            check(f"T19c {_mid} 探测消耗入 token_usage(每笔{_per})", token_used(_mid) - u0 == n * _per,
                  (token_used(_mid) - u0, n * _per))
            check(f"T19d {_mid} 探测同步 model_daily_stats 口径", call_count(_mid) - c0 == n,
                  (call_count(_mid) - c0, n))

        # 收尾：清理探测缓存中的 mock 条目（避免污染断点续跑缓存；zzbt 模型由 deep_clean 清理）
        try:
            _cp = os.path.join(REPO, "data", "reasoning_probe_cache.json")  # v2.14.0 起缓存归 data/
            _cache = json.load(open(_cp, encoding="utf-8"))
            for k in [k for k in _cache if "mock-echo-probe" in k]:
                _cache.pop(k, None)
            json.dump(_cache, open(_cp, "w", encoding="utf-8"), indent=1)
        except Exception:
            pass

        # T19z（v2.15.3）新增模型不再自动探测思考档位：只允许人工触发（/admin/reasoning/probe）
        def _purge_probe_cache(name):
            try:
                _cp2 = os.path.join(REPO, "data", "reasoning_probe_cache.json")
                _c2 = json.load(open(_cp2, encoding="utf-8"))
                for k in [k for k in _c2 if name in k]:
                    _c2.pop(k, None)
                json.dump(_c2, open(_cp2, "w", encoding="utf-8"), indent=1)
                return [k for k in _c2 if name in k]
            except Exception as e:
                return [f"purge异常:{e}"]
        _purge_resid = _purge_probe_cache("mock-probe-noauto")   # 上轮人工探测缓存可能晚于收尾清理落盘
        httpx.delete(f"{BASE}/admin/models/zzmock/probe-noauto", headers=ADMIN, timeout=15)  # 断点续跑幂等
        r = httpx.post(f"{BASE}/admin/models", headers=ADMIN, timeout=15, json={
            "id": "probe-noauto", "name": "mock-probe-noauto", "provider_id": "zzmock",
            "modality": "text", "is_free": True})
        check("T19z0 新增模型成功(无 probe 字段)",
              r.status_code == 200 and "probe" not in r.json(), r.text[:160])
        rd = httpx.get(f"{BASE}/admin/reasoning", headers=ADMIN, timeout=15).json()
        rec = (rd.get("models") or {}).get("zzmock/probe-noauto") or {}
        _cache_keys_now = [k for k in json.load(open(os.path.join(REPO, "data", "reasoning_probe_cache.json"), encoding="utf-8")) if "probe-noauto" in k]
        check("T19z1 新增后即刻未探测", rd.get("summary") is not None and rec.get("probed") is False,
              (rec.get("probed"), _purge_resid, _cache_keys_now))
        time.sleep(3)  # 旧实现后台线程早已发探测请求；新实现应零请求、缓存恒空
        rd = httpx.get(f"{BASE}/admin/reasoning", headers=ADMIN, timeout=15).json()
        rec = (rd.get("models") or {}).get("zzmock/probe-noauto") or {}
        cfg_chk = json.load(open(os.path.join(REPO, "config.json"), encoding="utf-8"))
        m_noauto = next((m for m in cfg_chk["models"] if m.get("id") == "zzbt/probe-noauto"), {})
        check("T19z2 等待后仍零自动探测(无请求/无缓存/未写映射)",
              _probe_hits("mock-probe-noauto") == 0 and rec.get("probed") is False
              and not m_noauto.get("reasoning_map"),
              (_probe_hits("mock-probe-noauto"), rec.get("probed"), bool(m_noauto.get("reasoning_map"))))
        # 人工触发仍可用：受理 queued 且真实发上游
        t0 = time.time()
        r = httpx.post(f"{BASE}/admin/reasoning/probe", headers=ADMIN, json={"model_id": "zzmock/probe-noauto"}, timeout=15)
        n = _wait_probe_settled("mock-probe-noauto", t0)
        check("T19z3 人工探测仍可用(受理+发上游)", r.status_code == 200 and r.json().get("queued") is True and n > 0,
              (r.status_code, n))
        for _ in range(5):  # 探测线程最后一批写可能晚于上游请求收尾：重试清到键消失
            _purge_probe_cache("mock-probe-noauto")
            _c3 = json.load(open(os.path.join(REPO, "data", "reasoning_probe_cache.json"), encoding="utf-8"))
            if not any("mock-probe-noauto" in k for k in _c3):
                break
            time.sleep(1)
        r = httpx.delete(f"{BASE}/admin/models/zzmock/probe-noauto", headers=ADMIN, timeout=15)
        check("T19z4 测试模型清理", r.status_code == 200, r.status_code)

        # ===== T20 每池50条滚动保留 + 密钥最近调用记录端点 =====
        for _ in range(55):
            chat("zzlocal", content="hi")
        n20 = 999
        for _ in range(16):  # 调度器每 60s 裁剪一次，最多等 80s
            time.sleep(5)
            rd = httpx.get(f"{BASE}/admin/decisions", params={"pool": "zzlocal", "limit": 100}, headers=ADMIN, timeout=15)
            n20 = len(rd.json().get("decisions", []))
            if 0 < n20 <= 50:
                break
        check("T20a 每池滚动保留≤50条(裁剪后)", 0 < n20 <= 50, n20)
        r = httpx.post(f"{BASE}/admin/keys", headers=ADMIN,
                       json={"name": "zzkey2", "type": "user", "allowed_pools": ["zzlocal"],
                             "token_type": "daily", "billing_mode": "token", "limit_amount": 1000000}, timeout=15)
        kid = r.json().get("key", {}).get("id")
        secret2 = DB.execute("SELECT secret FROM api_keys WHERE name='zzkey2'").fetchone()["secret"]
        chat("zzlocal", auth=secret2, content="hi")
        chat("zzlocal", auth=secret2, content="hi")
        rc = httpx.get(f"{BASE}/admin/keys/{kid}/calls", headers=ADMIN, timeout=15)
        jc = rc.json()
        calls = jc.get("calls", [])
        check("T20b 密钥调用记录端点200(≥2条)", rc.status_code == 200 and len(calls) >= 2, (rc.status_code, len(calls)))
        check("T20c 记录归属与字段正确",
              jc.get("key_name") == "zzkey2"
              and all(c.get("caller") == "zzkey2" and c.get("pool_name") == "zzlocal"
                      and c.get("selected") == "zzbt/echo-local" for c in calls[:2]),
              [(c.get("caller"), c.get("pool_name"), c.get("selected")) for c in calls[:2]])
        rl = httpx.get(f"{BASE}/admin/keys/{kid}/calls", params={"limit": 1}, headers=ADMIN, timeout=15)
        check("T20d limit参数生效", len(rl.json().get("calls", [])) == 1, len(rl.json().get("calls", [])))

        # ===== T21 面板静态接线：主题滑块与用量历史折线图 =====
        pa = httpx.get(f"{BASE}/admin/", timeout=15).text
        ph = httpx.get(f"{BASE}/hfadmin", timeout=15).text
        check("T21a admin面板滑块接线→hfadmin", "theme-switch" in pa and "location.href='/hfadmin/'" in pa, None)
        check("T21b hfadmin面板滑块接线→admin", "theme-switch" in ph and "location.href='/admin/'" in ph, None)
        check("T21c 双面板折线图与调用记录容器在位",
              all(k in pa for k in ("loadKeyUsage", "usage-calls", "Catmull-Rom"))
              and all(k in ph for k in ("loadKeyUsage", "usage-calls", "Catmull-Rom")), None)

        # ===== T22 Headroom 选配插件（方案A：按模型勾选，接单时判定；非必装自动旁路）=====
        _has_hr = True
        try:
            import headroom  # noqa: F401
        except Exception:
            _has_hr = False
        big = json.dumps([{"id": i, "name": f"item-{i}", "status": "active", "score": 0.95,
                           "tags": ["a", "b"]} for i in range(300)], ensure_ascii=False)

        def _hr_body(model):
            return {"model": model, "max_tokens": 2000, "messages": [
                {"role": "system", "content": "你是助手"},
                {"role": "user", "content": "查询商品列表"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "c1", "type": "function",
                     "function": {"name": "list_items", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "c1", "content": big},
                {"role": "user", "content": "总结一下"}]}

        def _set_hr(h):
            c = json.load(open(os.path.join(REPO, "config.json"), encoding="utf-8"))
            if h is None:
                c.pop("headroom", None)
            else:
                c["headroom"] = h
            json.dump(c, open(os.path.join(REPO, "config.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
            httpx.post(f"{BASE}/admin/reload", headers=ADMIN, timeout=30)

        def _last_hr_stat():
            r = DB.execute("SELECT mode, tokens_before, tokens_after, tokens_saved, error FROM headroom_stats"
                           " WHERE model='zzbt/echo-hr' ORDER BY id DESC LIMIT 1").fetchone()
            return dict(r) if r else None

        def _tool_content(n0):
            b = captured_bodies[-1] if len(captured_bodies) > n0 else {}
            for m in b.get("messages", []):
                if m.get("role") == "tool":
                    return m.get("content")
            return None

        # a) 总开关关闭（缺省）：勾选模型也绝不压缩，请求零改动
        _set_hr(None)
        n0 = len(captured_bodies)
        r = httpx.post(f"{BASE}/v1/chat/completions", headers=ADMIN, json=_hr_body("zzhr"), timeout=90)
        check("T22a 开关关→勾选模型不压缩", r.status_code == 200 and _tool_content(n0) == big,
              (r.status_code, _tool_content(n0) == big))

        # b) dry_run：正常计算压缩但不改写请求，统计入库（灰度数据源）；无库环境验证旁路不炸
        _set_hr({"enabled": True, "mode": "dry_run", "min_tokens_to_compress": 50, "protect_recent": 0})
        n0 = len(captured_bodies)
        r = httpx.post(f"{BASE}/v1/chat/completions", headers=ADMIN, json=_hr_body("zzhr"), timeout=90)
        st = _last_hr_stat()
        if _has_hr:
            check("T22b dry_run请求不改写", r.status_code == 200 and _tool_content(n0) == big, r.status_code)
            check("T22c dry_run统计入库(saved>0)",
                  st and st["mode"] == "dry_run" and st["tokens_saved"] > 0, st)
        else:
            check("T22b' 无库旁路(dry_run不压缩不炸)", r.status_code == 200 and _tool_content(n0) == big,
                  (r.status_code, _has_hr))

        # c) live：勾选模型请求体被压缩，响应与按上游 usage 计费完全正常
        _set_hr({"enabled": True, "mode": "live", "min_tokens_to_compress": 50, "protect_recent": 0})
        u0 = token_used("zzbt/echo-hr")
        n0 = len(captured_bodies)
        r = httpx.post(f"{BASE}/v1/chat/completions", headers=ADMIN, json=_hr_body("zzhr"), timeout=90)
        st = _last_hr_stat()
        if _has_hr:
            check("T22d live请求体已压缩", r.status_code == 200 and _tool_content(n0) not in (None, big),
                  (r.status_code, _tool_content(n0) == big))
            check("T22e live统计saved>0", st and st["mode"] == "live" and st["tokens_saved"] > 0, st)
        else:
            check("T22d' 无库旁路(live不压缩)", r.status_code == 200 and _tool_content(n0) == big,
                  (r.status_code, _has_hr))
        check("T22f 上游usage计费不受压缩影响(Δ=133)", token_used("zzbt/echo-hr") - u0 == 133,
              token_used("zzbt/echo-hr") - u0)

        # d) 方案A核心：总开关开启时，未勾选模型（对照池）仍收原文
        n0 = len(captured_bodies)
        r = httpx.post(f"{BASE}/v1/chat/completions", headers=ADMIN, json=_hr_body("zzhrctl"), timeout=90)
        check("T22g 未勾选模型收原文", r.status_code == 200 and _tool_content(n0) == big,
              (r.status_code, _tool_content(n0) == big))
        _set_hr(None)  # 还原总开关

        # e) 双面板勾选框静态接线（复用 T21 取回的页面）
        check("T22h 双面板headroom勾选框接线", 'id="f-headroom"' in pa and 'id="f-headroom"' in ph, None)

        # f) 节省统计端点：汇总/今日/按模型/明细 + 插件开关回显
        rs = httpx.get(f"{BASE}/admin/headroom/stats?days=7", headers=ADMIN, timeout=15)
        rj = rs.json() if rs.status_code == 200 else {}
        check("T22i 节省统计端点", rs.status_code == 200 and "total" in rj and "today" in rj and "recent" in rj
              and "enabled" in rj and (not _has_hr or any(m["model"] == "zzbt/echo-hr" for m in rj.get("by_model", []))),
              (rs.status_code, rj.get("enabled"), len(rj.get("by_model", []))))

        # g) 设置端点（/admin/headroom）：读取回显 + 保存即热生效（v2.13.0 设置页的数据源）
        r0 = httpx.get(f"{BASE}/admin/headroom", headers=ADMIN, timeout=15).json()
        check("T22j 设置端点读取", all(k in r0 for k in ("enabled", "mode", "available", "min_tokens_to_compress",
                                                          "kompress_enabled", "kompress_model", "ml_available")), r0)
        r1 = httpx.post(f"{BASE}/admin/headroom", headers=ADMIN,
                        json={"enabled": True, "mode": "dry_run", "min_tokens_to_compress": 50,
                              "protect_recent": 0, "timeout_seconds": 10, "kompress_enabled": True}, timeout=15)
        r1j = r1.json() if r1.status_code == 200 else {}
        check("T22k 设置端点保存热生效", r1.status_code == 200 and r1j.get("enabled") is True
              and r1j.get("mode") == "dry_run" and r1j.get("min_tokens_to_compress") == 50, (r1.status_code, r1j))
        check("T22m 纯文本压缩开关回环", r1j.get("kompress_enabled") is True and r1j.get("kompress_model") not in (None, "", "disabled"), r1j)
        r2 = httpx.post(f"{BASE}/admin/headroom", headers=ADMIN,
                        json={"enabled": False, "kompress_enabled": False}, timeout=15).json()
        check("T22n 纯文本压缩关闭还原", r2.get("kompress_enabled") is False and r2.get("kompress_model") == "disabled", r2)
        httpx.post(f"{BASE}/admin/headroom", headers=ADMIN, json={"enabled": False}, timeout=15)  # 还原总开关
        # h) v2.13.1 设置页控件：滑动开关/模式选项卡/高亮 JS 在位（双面板）；v2.13.3 增 hr-kompress 开关
        check("T22l 设置页控件在位", all(k in pa for k in ("hr-switch", "hr-radio", "applyHrModeHighlight", 'data-tab="settings"', "hr-kompress"))
              and all(k in ph for k in ("hr-switch", "hr-radio", "applyHrModeHighlight", 'data-tab="settings"', "hr-kompress")), None)

        # ===== T23（v2.13.4）搜索总结专门 Key：内层总结调用的记录(caller)与 Key 用量挂到 caller_key =====
        # 流程：建专门 Key zzkey3 → config.search_summary.caller_key 指向它（热 reload）→
        # 调 zzqfws2（qianfan_web_search + summary_pool=zzsump）→ 断言内层/外层决策记录归属与用量挂账。
        r = httpx.post(f"{BASE}/admin/keys", headers=ADMIN,
                       json={"name": "zzkey3", "type": "user", "allowed_pools": [],
                             "token_type": "daily", "billing_mode": "token", "limit_amount": 1000000}, timeout=15)
        k3id = r.json().get("key", {}).get("id")
        k3secret = DB.execute("SELECT secret FROM api_keys WHERE name='zzkey3'").fetchone()["secret"]

        def _k3_used():
            row = DB.execute("SELECT used_amount FROM api_key_usage WHERE key_id=?", (k3id,)).fetchone()
            return row["used_amount"] if row else 0

        def _set_ck(secret):
            """临时改写 caller_key（None=还原生产原值 CK0）；max_fetch_urls=0 避免用例真抓 example.com。"""
            c = json.load(open(os.path.join(REPO, "config.json"), encoding="utf-8"))
            ss = c.setdefault("search_summary", {})
            target = secret or CK0
            if target:
                ss["caller_key"] = target
            else:
                ss.pop("caller_key", None)
            ss["max_fetch_urls"] = 0 if secret else 8
            json.dump(c, open(os.path.join(REPO, "config.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
            httpx.post(f"{BASE}/admin/reload", headers=ADMIN, timeout=30)

        try:
            _set_ck(k3secret)
            u3 = _k3_used()
            r = chat("zzqfws2", content="news")
            row = DB.execute("SELECT caller FROM decision_log WHERE pool_name='zzsump' ORDER BY id DESC LIMIT 1").fetchone()
            check("T23a 内层总结记录caller=专门Key", r.status_code == 200 and row and row["caller"] == "zzkey3",
                  (r.status_code, dict(row) if row else None))
            row = DB.execute("SELECT caller FROM decision_log WHERE pool_name='zzqfws2' ORDER BY id DESC LIMIT 1").fetchone()
            check("T23b 外层搜索记录caller=原请求者不变", row and row["caller"] == "管理员", dict(row) if row else None)
            check("T23c 专门Key用量入账133", _k3_used() - u3 == 133, _k3_used() - u3)
            check("T23d 总结模型自身配额照常入账", token_used("zzbt/echo-sum") == 133, token_used("zzbt/echo-sum"))

            u3 = _k3_used()
            r = chat("zzqfws2", stream=True)
            check("T23e 流式总结200", r.status_code == 200, r.status_code)
            check("T23f 流式专门Key入账133(捕获内层usage帧)", _k3_used() - u3 == 133, _k3_used() - u3)

            _set_ck(None)  # 还原生产 caller_key；共享生产库 → baidusearch 真实存在，端到端验证归属
            r = chat("zzqfws2", content="news")
            row = DB.execute("SELECT caller FROM decision_log WHERE pool_name='zzsump' ORDER BY id DESC LIMIT 1").fetchone()
            # 期望值按环境确定：CK0 对应的 Key 在库中 → 归属该 Key 名（生产库=baidusearch）；
            # 全新库（Linux 首跑/新克隆）无此 Key → 网关按语义回退原 caller，两种都算正确
            _ck0_name = DB.execute("SELECT name FROM api_keys WHERE secret=?", (CK0,)).fetchone() if CK0 else None
            _t23g_expect = _ck0_name["name"] if _ck0_name else "管理员"
            check("T23g 生产caller_key还原后记录归属正确(库有该Key归其名下/全新库回退原caller)",
                  r.status_code == 200 and row and row["caller"] == _t23g_expect,
                  (r.status_code, dict(row) if row else None, _t23g_expect))

            _set_ck("mg-ffffffffffffffffffffffffffffffff")  # 不存在的 Key → 回退原 caller，总结绝不因记账失败而炸
            r = chat("zzqfws2", content="news")
            row = DB.execute("SELECT caller FROM decision_log WHERE pool_name='zzsump' ORDER BY id DESC LIMIT 1").fetchone()
            check("T23h Key不存在时回退原caller(降级不炸)", r.status_code == 200 and row and row["caller"] == "管理员",
                  (r.status_code, dict(row) if row else None))
        finally:
            _set_ck(None)

        # ===== T24（v2.13.3）池级 Switch 切换：POST /{池名} + switch=local|net 定向路由 =====
        r = httpx.post(f"{BASE}/admin/keys", headers=ADMIN,
                       json={"name": "zzkey4", "type": "user", "allowed_pools": ["zzsw"],
                             "token_type": "daily", "billing_mode": "token", "limit_amount": 1000000}, timeout=15)
        secret4 = DB.execute("SELECT secret FROM api_keys WHERE name='zzkey4'").fetchone()["secret"]
        # a) 未开 switch 的池拒绝（zzall 普通池）
        r = sw_chat("zzall", switch="local")
        check("T24a 未开Switch的池403", r.status_code == 403 and "未开启" in r.text, (r.status_code, r.text[:120]))
        # b) 非法值 / 缺失值明确报错（列出合法值）
        r = sw_chat("zzsw", switch="cloud")
        check("T24b1 非法switch值422", r.status_code == 422 and "local" in r.text and "net" in r.text,
              (r.status_code, r.text[:150]))
        r = httpx.post(f"{BASE}/zzsw", headers=ADMIN,
                       json={"model": "zzsw", "max_tokens": 100, "messages": [{"role": "user", "content": "hi"}]}, timeout=60)
        check("T24b2 缺失switch422", r.status_code == 422 and "缺失" in r.text, (r.status_code, r.text[:150]))
        # c) 无权限 key / 未知池
        r = sw_chat("zzsw2", switch="local", auth=secret4)
        check("T24c1 无权限key403", r.status_code == 403 and "无权" in r.text, (r.status_code, r.text[:120]))
        r = httpx.post(f"{BASE}/zzpoolxx", headers=ADMIN, json={"model": "zzpoolxx", "messages": []}, timeout=15)
        check("T24c2 未知池404", r.status_code == 404, r.status_code)
        # d) switch:local → 本地侧；e) switch:net（body 传参）→ 云端侧；另一侧零调用
        # （上游请求体的 model=模型接口 name，与既有用例 mock-echo-switch-b 口径一致）
        n_loc, n_net = up_count("mock-sw-local"), up_count("mock-sw-net")
        r = sw_chat("zzsw", switch="local")
        check("T24d switch:local路由local侧", r.status_code == 200
              and up_count("mock-sw-local") == n_loc + 1 and up_count("mock-sw-net") == n_net,
              (r.status_code, r.text[:120]))
        r = sw_chat("zzsw", switch="net", via="body")
        check("T24e switch:net路由net侧(body传参)", r.status_code == 200
              and up_count("mock-sw-net") == n_net + 1 and up_count("mock-sw-local") == n_loc + 1,
              (r.status_code, r.text[:120]))
        # f) 决策日志 requested 记 switch 侧别；g) /admin/decisions 端点可查
        row = DB.execute("SELECT requested, selected FROM decision_log WHERE pool_name='zzsw' ORDER BY id DESC LIMIT 1").fetchone()
        check("T24f 决策日志requested=switch:net", row and row["requested"] == "switch:net" and row["selected"] == "zzbt/sw-net",
              dict(row) if row else None)
        rd = httpx.get(f"{BASE}/admin/decisions", params={"pool": "zzsw", "limit": 10}, headers=ADMIN, timeout=15)
        dec = rd.json().get("decisions", [])
        check("T24g 决策端点可查switch路由", rd.status_code == 200 and any(d.get("requested") == "switch:local" for d in dec),
              [(d.get("requested"), d.get("selected")) for d in dec[:3]])
        # h) 同侧耗尽不跨侧：sw-local2 RPM=1，第二次 local 请求 503 且云端侧零调用（旁路兜底池升级）
        n_loc2 = up_count("mock-sw-local2")
        r = sw_chat("zzsw2", switch="local")
        check("T24h1 首次local路由成功", r.status_code == 200 and up_count("mock-sw-local2") == n_loc2 + 1,
              (r.status_code, r.text[:120]))
        n_net2 = up_count("mock-sw-net")
        r = sw_chat("zzsw2", switch="local")
        check("T24h2 同侧耗尽503不跨侧", r.status_code == 503 and up_count("mock-sw-net") == n_net2,
              (r.status_code, r.text[:150]))
        # i) 授权 key 正常调用并计费（计量路径与 /v1 完全复用）
        u4_0 = DB.execute("SELECT used_amount FROM api_key_usage WHERE key_id=(SELECT id FROM api_keys WHERE name='zzkey4')").fetchone()
        u4_0 = u4_0["used_amount"] if u4_0 else 0
        r = sw_chat("zzsw", switch="net", auth=secret4)
        u4_1 = DB.execute("SELECT used_amount FROM api_key_usage WHERE key_id=(SELECT id FROM api_keys WHERE name='zzkey4')").fetchone()
        check("T24i 授权key调用并计费133", r.status_code == 200 and u4_1 and u4_1["used_amount"] - u4_0 == 133,
              (r.status_code, (u4_1["used_amount"] - u4_0) if u4_1 else None))
        # j) 流式；k) body 缺 model 字段（网关改写/补齐为池名）
        n_net3 = up_count("mock-sw-net")
        r = sw_chat("zzsw", switch="net", stream=True)
        check("T24j 流式switch:net", r.status_code == 200 and "[DONE]" in r.text and up_count("mock-sw-net") == n_net3 + 1,
              (r.status_code, "[DONE]" in r.text))
        n_loc3 = up_count("mock-sw-local")
        r = httpx.post(f"{BASE}/zzsw?switch=local", headers=ADMIN,
                       json={"messages": [{"role": "user", "content": "hi"}]}, timeout=60)
        check("T24k body缺model字段网关补齐", r.status_code == 200 and up_count("mock-sw-local") == n_loc3 + 1,
              (r.status_code, r.text[:120]))
        # l) 开池校验：缺任一侧 400；被拒改动不残留配置；两侧齐全可开
        r = httpx.put(f"{BASE}/admin/pools/zzsw", headers=ADMIN,
                      json={"model_ids": ["zzbt/sw-local", "zzbt/sw-net"], "switch_enabled": False}, timeout=15)
        check("T24l0 关闭Switch200", r.status_code == 200, r.status_code)
        r = httpx.put(f"{BASE}/admin/pools/zzsw", headers=ADMIN,
                      json={"model_ids": ["zzbt/sw-local"], "switch_enabled": True}, timeout=15)
        check("T24l1 只挂local侧开启400", r.status_code == 400 and "云端" in r.text, (r.status_code, r.text[:150]))
        r = httpx.put(f"{BASE}/admin/pools/zzsw", headers=ADMIN,
                      json={"model_ids": ["zzbt/sw-net"], "switch_enabled": True}, timeout=15)
        check("T24l2 只挂net侧开启400", r.status_code == 400 and "本地" in r.text, (r.status_code, r.text[:150]))
        gp = httpx.get(f"{BASE}/admin/pools", headers=ADMIN, timeout=15).json()["pools"]["zzsw"]
        check("T24l3 被拒改动不残留配置", gp.get("model_ids") == ["zzbt/sw-local", "zzbt/sw-net"] and gp.get("switch_enabled") is False,
              gp)
        r = httpx.put(f"{BASE}/admin/pools/zzsw", headers=ADMIN,
                      json={"model_ids": ["zzbt/sw-local", "zzbt/sw-net"], "switch_enabled": True}, timeout=15)
        check("T24l4 两侧齐全开启200并透出", r.status_code == 200 and r.json().get("pool", {}).get("switch_enabled") is True,
              r.status_code)
        # m) 双面板 Switch 控件在位
        ph = open(os.path.join(REPO, "static", "hfadmin.html"), encoding="utf-8").read()
        pa = open(os.path.join(REPO, "static", "index.html"), encoding="utf-8").read()
        check("T24m 双面板Switch控件在位", all(k in ph for k in ("togglePoolSwitch", "switch_enabled"))
              and all(k in pa for k in ("togglePoolSwitch", "switch_enabled")), None)
        # n) v2.14.1 子池穿透：池内只有 pool: 子池引用（default 拓扑）时 switch 跨子池按侧选择，
        #    且决策步骤不再误报 switch_no_match（旧实现只数直挂模型，纯子池池每次都误记一条）
        n_loc4, n_net4 = up_count("mock-sw-local"), up_count("mock-sw-net")
        r = sw_chat("zzswnest", switch="local")
        check("T24n1 子池穿透local命中本地子池", r.status_code == 200
              and up_count("mock-sw-local") == n_loc4 + 1 and up_count("mock-sw-net") == n_net4,
              (r.status_code, r.text[:120]))
        r = sw_chat("zzswnest", switch="net", via="body")
        check("T24n2 子池穿透net跳过本地子池", r.status_code == 200
              and up_count("mock-sw-net") == n_net4 + 1 and up_count("mock-sw-local") == n_loc4 + 1,
              (r.status_code, r.text[:120]))
        rn = httpx.get(f"{BASE}/admin/decisions", params={"pool": "zzswnest", "limit": 10}, headers=ADMIN, timeout=15)
        nest_dec = rn.json().get("decisions", [])
        nest_steps = [s for d in nest_dec for s in (d.get("steps") or [])]
        check("T24n3 成功调用无误报switch_no_match",
              rn.status_code == 200 and len(nest_dec) >= 2
              and all(s.get("reason") != "switch_no_match" for s in nest_steps),
              [s.get("reason") for s in nest_steps])
        r = sw_chat("zzswnestn", switch="local")
        check("T24n4 整树无该侧仍明确报无候选", r.status_code == 503 and "Switch 切换无候选" in r.text,
              (r.status_code, r.text[:150]))
        r = httpx.post(f"{BASE}/zzswnest?switch=local", headers=ADMIN,
                       json={"response_format": {"type": "json_object"},
                             "messages": [{"role": "user", "content": "hi"}]}, timeout=60)
        check("T24n5 该侧存在但不可用不误报无候选",
              r.status_code == 503 and "JSON" in r.text and "Switch 切换无候选" not in r.text,
              (r.status_code, r.text[:200]))

        # T26（缓存计费统计）：勾选 cost_enabled 的模型按小时桶记录 缓存命中/未命中/输出 token；
        # 未勾选模型零写入；价格/高峰期经 PUT 保存、/admin/model/{id}/cost 读出
        mock_mode["cached"] = True
        r = chat("zzcost", stream=True)
        row = DB.execute("SELECT calls, hit_tokens, miss_tokens, out_tokens FROM model_cache_stats "
                         "WHERE model_name='zzbt/echo-cost'").fetchone()
        check("T26a 流式记录命中40/未命中60/输出33",
              r.status_code == 200 and row and (row["calls"], row["hit_tokens"], row["miss_tokens"], row["out_tokens"]) == (1, 40, 60, 33),
              dict(row) if row else None)
        r = chat("zzcost")
        row = DB.execute("SELECT calls, hit_tokens, miss_tokens, out_tokens FROM model_cache_stats "
                         "WHERE model_name='zzbt/echo-cost'").fetchone()
        check("T26b 非流式累计命中80/未命中120/输出66",
              r.status_code == 200 and row and (row["calls"], row["hit_tokens"], row["miss_tokens"], row["out_tokens"]) == (2, 80, 120, 66),
              dict(row) if row else None)
        mock_mode["cached"] = False
        r = chat("zzcost")
        row = DB.execute("SELECT calls, hit_tokens, miss_tokens, out_tokens FROM model_cache_stats "
                         "WHERE model_name='zzbt/echo-cost'").fetchone()
        check("T26c 上游未回报缓存按命中0记(未命中+100)",
              r.status_code == 200 and row and (row["calls"], row["hit_tokens"], row["miss_tokens"], row["out_tokens"]) == (3, 80, 220, 99),
              dict(row) if row else None)
        check("T26d 未勾选模型零写入",
              DB.execute("SELECT count(*) c FROM model_cache_stats WHERE model_name='zzbt/echo-token'").fetchone()["c"] == 0, "")
        st = httpx.get(f"{BASE}/stats", headers=ADMIN, timeout=15).json().get("models", [])
        check("T26e stats透出cost_enabled标记",
              any(m.get("id") == "zzbt/echo-cost" and m.get("cost_enabled") for m in st)
              and any(m.get("id") == "zzbt/echo-token" and not m.get("cost_enabled") for m in st), "")
        r = httpx.get(f"{BASE}/admin/model/zzbt/echo-cost/cost", headers=ADMIN, timeout=15)
        d = r.json()
        agg = {k: sum(h.get(k, 0) for h in d.get("hours", [])) for k in ("calls", "hit_tokens", "miss_tokens", "out_tokens")}
        check("T26f cost端点返回小时桶聚合与默认价格",
              r.status_code == 200 and d.get("enabled") is True
              and agg == {"calls": 3, "hit_tokens": 80, "miss_tokens": 220, "out_tokens": 99}
              and d.get("prices") == {"hit": 0, "miss": 0, "out": 0}, (agg, d.get("prices")))
        r = httpx.put(f"{BASE}/admin/models/zzbt/echo-cost", headers=ADMIN, timeout=15, json={
            "cost_prices": {"hit": 1, "miss": 2, "out": 4},
            "cost_peak": {"enabled": True, "windows": "9:00-12:00;14:00-18:00;21:00-6:00", "hit": 2, "miss": 4, "out": 8}})
        d = httpx.get(f"{BASE}/admin/model/zzbt/echo-cost/cost", headers=ADMIN, timeout=15).json()
        check("T26g 价格与多段高峰期配置持久化",
              r.status_code == 200 and d.get("prices", {}).get("hit") == 1
              and d.get("peak", {}).get("enabled") is True
              and d.get("peak", {}).get("windows") == "9:00-12:00;14:00-18:00;21:00-6:00", d)

        # T27（模态测速）：卡片「⚡测速」(/speedtest→pool.speedtest) 按模型模态分流——
        # 原实现一律发 chat/completions，embedding/rerank 必然 400（编辑界面 test_model 早已分流，两处口径对齐）
        r = httpx.post(f"{BASE}/speedtest", headers=ADMIN, json={"model_ids": ["zzbt/echo-emb"]}, timeout=30)
        re_ = (r.json().get("results") or [{}])[0]
        check("T27a embedding卡片测速ok(走/embeddings)",
              r.status_code == 200 and re_.get("status") == "ok" and re_.get("tokens") == 5, re_)
        check("T27a2 embedding测速usage计量入账", re_.get("usage_recorded") == 5, re_)
        r = httpx.post(f"{BASE}/speedtest", headers=ADMIN, json={"model_ids": ["zzbt/echo-rer"]}, timeout=30)
        rr = (r.json().get("results") or [{}])[0]
        check("T27b rerank卡片测速ok(走/rerank)",
              r.status_code == 200 and rr.get("status") == "ok" and rr.get("tokens") == 7, rr)
        r = httpx.post(f"{BASE}/speedtest", headers=ADMIN, json={"model_ids": ["zzbt/echo-sum"]}, timeout=30)
        rc = (r.json().get("results") or [{}])[0]
        check("T27c chat模型测速不受影响",
              r.status_code == 200 and rc.get("status") == "ok" and rc.get("tokens") == 133, rc)
        r = httpx.post(f"{BASE}/admin/test_model", headers=ADMIN, timeout=15, json={
            "base_url": f"http://127.0.0.1:{MOCK_PORT}/v1", "api_key": "x", "protocol": "openai",
            "model_name": "mock-echo-emb", "modality": "embedding"})
        rt = (r.json().get("result") or {})
        check("T27d 编辑界面embedding测试连接带tokens/tps(不再undefined)",
              r.status_code == 200 and rt.get("status") == "ok"
              and rt.get("tokens") == 5 and rt.get("tps") is not None, rt)

        # ── T28 用量分组查看（#39）：GET /admin/stats/grouped 四维度聚合 ──
        # 先发一笔确定性调用（zzsump 池 → zzbt/echo-sum，mock 回 usage=133），断言不依赖先前用例
        r0 = httpx.post(f"{BASE}/v1/chat/completions", headers=ADMIN,
                        json={"model": "zzsump", "messages": [{"role": "user", "content": "hi"}]}, timeout=30)
        r = httpx.get(f"{BASE}/admin/stats/grouped?dim=model&days=1", headers=ADMIN, timeout=15)
        mrow = next((x for x in r.json().get("rows", []) if x["name"] == "zzbt/echo-sum"), None)
        check("T28a 模型维度聚合(133tok入行)", r0.status_code == 200 and r.status_code == 200
              and mrow and mrow["calls"] >= 1 and mrow["tokens"] >= 133, (r0.status_code, mrow))
        r = httpx.get(f"{BASE}/admin/stats/grouped?dim=provider&days=1", headers=ADMIN, timeout=15)
        prow = next((x for x in r.json().get("rows", []) if x["name"] == "zzmock"), None)
        check("T28b 供应商维度经模型映射", r.status_code == 200 and prow and prow["calls"] >= 1, prow)
        r = httpx.get(f"{BASE}/admin/stats/grouped?dim=pool&days=1", headers=ADMIN, timeout=15)
        plrow = next((x for x in r.json().get("rows", []) if x["name"] == "zzsump"), None)
        check("T28c 池维度按命中池聚合", r.status_code == 200 and plrow and plrow["calls"] >= 1, plrow)
        r = httpx.get(f"{BASE}/admin/stats/grouped?dim=key&days=1", headers=ADMIN, timeout=15)
        krow = next((x for x in r.json().get("rows", []) if x["name"] == "管理员"), None)
        check("T28d Key维度含管理员调用", r.status_code == 200 and krow and krow["calls"] >= 1, krow)
        r = httpx.get(f"{BASE}/admin/stats/grouped?dim=bogus&days=1", headers=ADMIN, timeout=15)
        check("T28e 非法维度返回400", r.status_code == 400, r.status_code)

        # ── T29（v2.15.0）付费令牌类型 all_day/idle_only：高峰时段拦截 + cost_peak.windows 同源互通 ──
        # T29a 全天高峰段的 idle_only 被拦（peak_blocked）、同池 all_day 正常接单
        r = httpx.post(f"{BASE}/v1/chat/completions", headers=ADMIN,
                       json={"model": "zzpaid", "messages": [{"role": "user", "content": "hi"}]}, timeout=30)
        steps_row = DB.execute("SELECT steps, selected FROM decision_log WHERE pool_name='zzpaid' ORDER BY id DESC LIMIT 1").fetchone()
        steps = json.loads(steps_row["steps"]) if steps_row and steps_row["steps"] else []
        idle_steps = [s for s in steps if s.get("model") == "zzbt/paid-idle-block"]
        check("T29a idle_only高峰时段被拦(peak_blocked)且all_day接单",
              r.status_code == 200 and steps_row and steps_row["selected"] == "zzbt/paid-allday"
              and idle_steps and idle_steps[0].get("reason") == "peak_blocked"
              and "0:00-23:59" in (idle_steps[0].get("detail") or {}).get("peak_windows", ""),
              (r.status_code, steps_row["selected"] if steps_row else None, idle_steps[:1]))
        # T29b 不设高峰时段的 idle_only 默认全天闲时：正常路由不拦截
        r = httpx.post(f"{BASE}/v1/chat/completions", headers=ADMIN,
                       json={"model": "zzpaid2", "messages": [{"role": "user", "content": "hi"}]}, timeout=30)
        check("T29b idle_only未设时段默认全天闲时(放行)",
              r.status_code == 200, r.status_code)
        # T29c all_day 带全天高峰段仍放行（时段只约束 idle_only）
        r = httpx.post(f"{BASE}/v1/chat/completions", headers=ADMIN,
                       json={"model": "zzpaid3", "messages": [{"role": "user", "content": "hi"}]}, timeout=30)
        check("T29c all_day不受高峰时段约束", r.status_code == 200, r.status_code)
        # T29d 编辑表单式 PUT（只带 token_type + cost_peak.windows）：浅合并保留价格与 enabled。
        # 先设价格（模拟「预估费用」弹窗配置），再以表单口径提交 windows-only PUT，价格必须原样保留
        # （前端表单提交前已按 _peakLabel 规范化为 HH:MM，与表单行为一致）
        httpx.put(f"{BASE}/admin/models/zzbt/paid-allday", headers=ADMIN, timeout=15,
                  json={"cost_prices": {"hit": 1, "miss": 2, "out": 4}})
        r = httpx.put(f"{BASE}/admin/models/zzbt/paid-allday", headers=ADMIN, timeout=15, json={
            "token_type": "idle_only", "cost_peak": {"windows": "09:00-12:00"}})
        d = httpx.get(f"{BASE}/admin/model/zzbt/paid-allday/cost", headers=ADMIN, timeout=15).json()
        check("T29d 表单式PUT浅合并windows(价格/enabled保留·规范化)",
              r.status_code == 200 and d.get("peak", {}).get("windows") == "09:00-12:00"
              and d.get("peak", {}).get("enabled") is True
              and d.get("prices", {}) == {"hit": 1, "miss": 2, "out": 4}, d.get("peak"))
        # T29e 改回 all_day + 清空时段：windows 落空串、价格仍在；/admin/models 可见 cost_peak
        r = httpx.put(f"{BASE}/admin/models/zzbt/paid-allday", headers=ADMIN, timeout=15, json={
            "token_type": "all_day", "cost_peak": {"windows": ""}})
        d = httpx.get(f"{BASE}/admin/models", headers=ADMIN, timeout=15).json()
        m29 = next((m for m in d.get("models", []) if m["id"] == "zzbt/paid-allday"), {})
        check("T29e 清空时段落空串且价格保留(/admin/models透出)",
              r.status_code == 200 and (m29.get("cost_peak") or {}).get("windows") == ""
              and (m29.get("cost_peak") or {}).get("enabled") is True
              and m29.get("token_type") == "all_day" and m29.get("is_free") is False, m29.get("cost_peak"))
        # T29f 新增模型（POST）携带 token_type=idle_only + cost_peak：原样落库
        # （供应商制模型 id 由后端加前缀，请求 id 不可含 /）
        r = httpx.post(f"{BASE}/admin/models", headers=ADMIN, timeout=15, json={
            "id": "paid-new", "name": "mock-paid-new", "provider_id": "zzmock",
            "is_free": False, "token_type": "idle_only",
            "cost_peak": {"windows": "21:00-6:00"}})
        d = httpx.get(f"{BASE}/admin/models", headers=ADMIN, timeout=15).json()
        mn = next((m for m in d.get("models", []) if m["id"] == "zzmock/paid-new"), {})
        check("T29f POST新增idle_only+跨零点时段落库",
              r.status_code == 200 and mn.get("token_type") == "idle_only"
              and (mn.get("cost_peak") or {}).get("windows") == "21:00-6:00",
              (r.status_code, mn.get("cost_peak")))
        r = httpx.delete(f"{BASE}/admin/models/zzmock/paid-new", headers=ADMIN, timeout=15)
        check("T29g 测试新增模型清理", r.status_code == 200, r.status_code)

        # ── T29h-k（v2.15.1）高峰三维：每日时段 × 每周高峰（1-7）− 特定谷峰日（MMDD）──
        # 按运行当天北京日期动态构造，保证任何日期执行都确定
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _zi
        _bj = _dt.now(_zi("Asia/Shanghai"))
        _wd_today = str(_bj.isoweekday())                              # 1=周一…7=周日
        _wd_others = "".join(d for d in "1234567" if d != _wd_today)
        _md_today = f"{_bj.month:02d}{_bj.day:02d}"
        _ALLDAY = "0:00-23:59;23:59-0:00"
        def _set_peak(pk):
            r_ = httpx.put(f"{BASE}/admin/models/zzbt/paid-idle-block", headers=ADMIN, timeout=15, json={"cost_peak": pk})
            httpx.post(f"{BASE}/admin/reload", headers=ADMIN, timeout=15)
            return r_
        def _zzpaid_sel():
            httpx.post(f"{BASE}/v1/chat/completions", headers=ADMIN,
                       json={"model": "zzpaid", "messages": [{"role": "user", "content": "hi"}]}, timeout=30)
            return DB.execute("SELECT steps, selected FROM decision_log WHERE pool_name='zzpaid' ORDER BY id DESC LIMIT 1").fetchone()
        # T29h 每周高峰不含今天：即便全天时段也不拦（今天非高峰日）→ 队首 idle-block 直接接单
        _set_peak({"enabled": False, "windows": _ALLDAY, "weekdays": _wd_others, "exdates": ""})
        row = _zzpaid_sel()
        check("T29h 每周高峰不含今天不拦", row["selected"] == "zzbt/paid-idle-block", row["selected"])
        # T29i 每周高峰=今天：全天时段 + 命中周几 → 拦截，detail 带周几串
        _set_peak({"enabled": False, "windows": _ALLDAY, "weekdays": _wd_today, "exdates": ""})
        row = _zzpaid_sel()
        steps = json.loads(row["steps"]) if row["steps"] else []
        idle_steps = [s for s in steps if s.get("model") == "zzbt/paid-idle-block"]
        check("T29i 每周高峰命中今天即拦(带周几)",
              row["selected"] == "zzbt/paid-allday" and idle_steps
              and idle_steps[0].get("reason") == "peak_blocked"
              and (idle_steps[0].get("detail") or {}).get("peak_weekdays") == _wd_today,
              (row["selected"], idle_steps[:1]))
        # T29j 特定谷峰日=今天：即便周几命中 + 全天时段，谷峰日优先级最高 → 放行
        _set_peak({"enabled": False, "windows": _ALLDAY, "weekdays": _wd_today, "exdates": _md_today})
        row = _zzpaid_sel()
        check("T29j 谷峰日优先级最高(全天闲时)", row["selected"] == "zzbt/paid-idle-block", row["selected"])
        # T29k 三维落库与浅合并：只改 weekdays 时 windows/exdates 保留，cost 端点透出
        _set_peak({"weekdays": "135"})
        d = httpx.get(f"{BASE}/admin/model/zzbt/paid-idle-block/cost", headers=ADMIN, timeout=15).json()
        check("T29k 三维同源落库+浅合并透出",
              d.get("peak", {}).get("weekdays") == "135"
              and d.get("peak", {}).get("windows") == _ALLDAY
              and d.get("peak", {}).get("exdates") == _md_today, d.get("peak"))

        # ── T29l-n（v2.15.2）谷峰日区间：MMDD-MMDD 含首尾；起>止=跨年经元旦 ──
        from datetime import timedelta as _td2
        _yest = (_bj - _td2(days=1)).strftime("%m%d")
        _tom = (_bj + _td2(days=1)).strftime("%m%d")
        # T29l 区间 昨天-今天-明天（跨月时自动成为跨年区间）必覆盖今天 → 谷峰豁免放行
        _set_peak({"enabled": False, "windows": _ALLDAY, "weekdays": _wd_today, "exdates": f"{_yest}-{_tom}"})
        row = _zzpaid_sel()
        check("T29l 谷峰区间覆盖今天(放行)", row["selected"] == "zzbt/paid-idle-block", row["selected"])
        # T29m 跨年环绕区间 明天-昨天：覆盖除今天外一整圈，唯独不含今天 → 照拦（验证环绕展开的空档语义）
        _set_peak({"enabled": False, "windows": _ALLDAY, "weekdays": _wd_today, "exdates": f"{_tom}-{_yest}"})
        row = _zzpaid_sel()
        check("T29m 跨年环绕区间不含今天照拦", row["selected"] == "zzbt/paid-allday", row["selected"])
        # T29n 不含今天的常规区间 → 照拦；且原始区间串原样保留落库（cost 端点透出）
        _rng_far = f"{(_bj + _td2(days=10)).strftime('%m%d')}-{(_bj + _td2(days=20)).strftime('%m%d')}"
        _set_peak({"enabled": False, "windows": _ALLDAY, "weekdays": _wd_today, "exdates": _rng_far})
        row = _zzpaid_sel()
        d = httpx.get(f"{BASE}/admin/model/zzbt/paid-idle-block/cost", headers=ADMIN, timeout=15).json()
        check("T29n 区间不含今天照拦且区间串保留",
              row["selected"] == "zzbt/paid-allday"
              and d.get("peak", {}).get("exdates") == _rng_far, (row["selected"], d.get("peak", {}).get("exdates")))

        # ── T30（v2.16.0）OpenAI Responses API：POST/GET/DELETE /v1/responses + 工具往返 + 状态化 ──
        # T30a 非流式基本转换：instructions→system、input 字符串→user、max_output_tokens/reasoning.effort 映射；
        # 响应 object=response + message(output_text) item + usage 三元组
        captured_bodies.clear()
        r = httpx.post(f"{BASE}/v1/responses", headers=dict(ADMIN), timeout=30,
                       json={"model": "zzresp", "instructions": "你是测试助手", "input": "你好",
                             "max_output_tokens": 800, "reasoning": {"effort": "low"}})
        rb = r.json()
        up = captured_bodies[-1]
        check("T30a 非流式Responses→chat转换与响应形状",
              r.status_code == 200 and rb.get("object") == "response" and rb.get("status") == "completed"
              and any(i.get("type") == "message" and i["content"][0]["text"] == "答案" for i in rb.get("output", []))
              and rb.get("usage", {}).get("total_tokens") == 133
              and up["messages"][0] == {"role": "system", "content": "你是测试助手"}
              and up["messages"][1] == {"role": "user", "content": "你好"}
              and up.get("max_tokens") == 800 and up.get("reasoning_effort") == "low",
              (r.status_code, rb.get("object"), rb.get("usage")))

        # T30b 工具往返：flat tools→nested、function_call/输出历史→assistant.tool_calls/role=tool、
        # tool_choice 映射；上游 tool_calls 响应 → function_call item（call_id/name/arguments）
        captured_bodies.clear()
        r = httpx.post(f"{BASE}/v1/responses", headers=dict(ADMIN), timeout=30, json={
            "model": "zztool",
            "input": [
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "北京天气"}]},
                {"type": "function_call", "call_id": "call_prev", "name": "get_weather",
                 "arguments": "{\"city\":\"上海\"}"},
                {"type": "function_call_output", "call_id": "call_prev", "output": "多云"},
            ],
            "tools": [{"type": "function", "name": "get_weather", "description": "查天气",
                       "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}],
            "tool_choice": {"type": "function", "name": "get_weather"},
        })
        rb = r.json()
        up = captured_bodies[-1]
        fc = [i for i in rb.get("output", []) if i.get("type") == "function_call"]
        check("T30b 工具定义/历史/结果与tool_call响应往返",
              r.status_code == 200
              and up["tools"] == [{"type": "function", "function": {
                  "name": "get_weather", "description": "查天气",
                  "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}]
              and up["tool_choice"] == {"type": "function", "function": {"name": "get_weather"}}
              and up["messages"][1]["tool_calls"][0]["id"] == "call_prev"
              and up["messages"][2] == {"role": "tool", "tool_call_id": "call_prev", "content": "多云"}
              and len(fc) == 1 and fc[0]["call_id"] == "call_t1"
              and fc[0]["name"] == "get_weather" and json.loads(fc[0]["arguments"]) == {"city": "北京"},
              (r.status_code, [i.get("type") for i in rb.get("output", [])]))

        # T30c 流式：chat chunk → Responses 事件流（created → output_text.delta → completed 带 usage），
        # 计费与决策日志走既有链路（actual_tokens=133）
        _c30_before = call_count("zzbt/echo-token")
        ev_types, _completed30 = [], {}
        with httpx.stream("POST", f"{BASE}/v1/responses", headers=dict(ADMIN), timeout=30,
                          json={"model": "zzresp", "input": "流式测试", "stream": True}) as sr:
            _st30 = sr.status_code
            _cur30 = None
            for line in sr.iter_lines():
                if line.startswith("event: "):
                    _cur30 = line[7:]
                elif line.startswith("data: ") and _cur30:
                    ev_types.append(_cur30)
                    if _cur30 == "response.completed":
                        _completed30 = json.loads(line[6:])
        _dec30 = DB.execute("SELECT actual_tokens FROM decision_log WHERE pool_name='zzresp' ORDER BY id DESC LIMIT 1").fetchone()
        check("T30c 流式事件序+completed带usage+计费落账",
              _st30 == 200 and ev_types[0] == "response.created"
              and "response.output_text.delta" in ev_types and ev_types[-1] == "response.completed"
              and _completed30.get("response", {}).get("usage", {}).get("total_tokens") == 133
              and _dec30 and _dec30["actual_tokens"] == 133
              and call_count("zzbt/echo-token") == _c30_before + 1,
              (_st30, ev_types[:3], _dec30["actual_tokens"] if _dec30 else None))

        # T30d 状态化：store 默认 true → GET 可取；previous_response_id 链拼接（上游收到完整历史）；
        # DELETE 后 GET 404；store=false 不落档；未知 previous_response_id 404
        r0 = httpx.post(f"{BASE}/v1/responses", headers=dict(ADMIN), timeout=30,
                        json={"model": "zzresp", "input": "第一轮"})
        rid = r0.json().get("id")
        g = httpx.get(f"{BASE}/v1/responses/{rid}", headers=dict(ADMIN), timeout=15)
        captured_bodies.clear()
        r2 = httpx.post(f"{BASE}/v1/responses", headers=dict(ADMIN), timeout=30,
                        json={"model": "zzresp", "previous_response_id": rid, "input": "第二轮"})
        up2 = captured_bodies[-1]
        chain_msgs = [(m["role"], m["content"]) for m in up2["messages"]]
        g404 = httpx.get(f"{BASE}/v1/responses/resp_nonexistent", headers=dict(ADMIN), timeout=15)
        d30 = httpx.delete(f"{BASE}/v1/responses/{rid}", headers=dict(ADMIN), timeout=15)
        g2 = httpx.get(f"{BASE}/v1/responses/{rid}", headers=dict(ADMIN), timeout=15)
        r3 = httpx.post(f"{BASE}/v1/responses", headers=dict(ADMIN), timeout=30,
                        json={"model": "zzresp", "input": "不留档", "store": False})
        g3 = httpx.get(f"{BASE}/v1/responses/{r3.json().get('id')}", headers=dict(ADMIN), timeout=15)
        p404 = httpx.post(f"{BASE}/v1/responses", headers=dict(ADMIN), timeout=30,
                          json={"model": "zzresp", "input": "hi", "previous_response_id": "resp_gone"})
        check("T30d 状态化存取/链拼接/删除/store=false",
              r0.status_code == 200 and rid and g.status_code == 200 and g.json().get("id") == rid
              and r2.status_code == 200
              and chain_msgs == [("user", "第一轮"), ("assistant", "答案"), ("user", "第二轮")]
              and g404.status_code == 404 and d30.status_code == 200 and g2.status_code == 404
              and r3.status_code == 200 and g3.status_code == 404 and p404.status_code == 404,
              (rid, chain_msgs, g404.status_code, g2.status_code, g3.status_code, p404.status_code))

        # T30e 不支持项明确 400：background / conversation / 内置工具 / 未知 item 类型
        r_bg = httpx.post(f"{BASE}/v1/responses", headers=dict(ADMIN), timeout=15,
                          json={"model": "zzresp", "input": "hi", "background": True})
        r_cv = httpx.post(f"{BASE}/v1/responses", headers=dict(ADMIN), timeout=15,
                          json={"model": "zzresp", "input": "hi", "conversation": "conv_1"})
        r_ws = httpx.post(f"{BASE}/v1/responses", headers=dict(ADMIN), timeout=15,
                          json={"model": "zzresp", "input": "hi", "tools": [{"type": "web_search"}]})
        r_it = httpx.post(f"{BASE}/v1/responses", headers=dict(ADMIN), timeout=15,
                          json={"model": "zzresp", "input": [{"type": "mystery"}]})
        check("T30e 不支持项400(background/conversation/内置工具/未知item)",
              r_bg.status_code == 400 and r_cv.status_code == 400
              and r_ws.status_code == 400 and r_it.status_code == 400,
              (r_bg.status_code, r_cv.status_code, r_ws.status_code, r_it.status_code))

        # ── T30f-h（v2.16.1）openai_responses 上游协议：网关 chat 格式 ⇄ Responses 上游互转 ──
        # T30f chat 客户端 → Responses 上游：上游收到 Responses 格式（input/instructions/max_output_tokens），
        # 响应转回 chat 格式（content/usage），计费 133 落账
        captured_bodies.clear()
        r = httpx.post(f"{BASE}/v1/chat/completions", headers=dict(ADMIN), timeout=30, json={
            "model": "zzrespp", "max_tokens": 777,
            "messages": [{"role": "system", "content": "系统提示"},
                         {"role": "user", "content": "你好"}]})
        rb = r.json()
        up = captured_bodies[-1]
        check("T30f chat→Responses上游互转与计费",
              r.status_code == 200 and rb["choices"][0]["message"]["content"] == "答案"
              and rb["usage"]["total_tokens"] == 133
              and "input" in up and "messages" not in up and up.get("instructions") == "系统提示"
              and up.get("max_output_tokens") == 777
              and up["input"][0] == {"type": "message", "role": "user",
                                     "content": [{"type": "input_text", "text": "你好"}]}
              and token_used("zzbt/echo-resp") >= 133,
              (r.status_code, rb.get("usage"), up.get("instructions")))

        # T30g Responses 客户端 → Responses 上游（responses→chat→responses 全链往返）
        r = httpx.post(f"{BASE}/v1/responses", headers=dict(ADMIN), timeout=30,
                       json={"model": "zzrespp", "instructions": "系统提示", "input": "你好"})
        rb = r.json()
        check("T30g Responses客户端→Responses上游全链往返",
              r.status_code == 200 and rb.get("object") == "response"
              and any(i.get("type") == "message" and i["content"][0]["text"] == "答案"
                      for i in rb.get("output", []))
              and rb.get("usage", {}).get("total_tokens") == 133,
              (r.status_code, rb.get("usage")))

        # T30h 流式：上游 Responses 事件 → chat chunk（content 增量 + usage 帧 + [DONE]），决策日志 133
        chunks30 = []
        with httpx.stream("POST", f"{BASE}/v1/chat/completions", headers=dict(ADMIN), timeout=30,
                          json={"model": "zzrespp", "stream": True,
                                "messages": [{"role": "user", "content": "流式"}]}) as sr:
            _st30h = sr.status_code
            for line in sr.iter_lines():
                if line.startswith("data: "):
                    chunks30.append(line[6:])
        dec30 = DB.execute("SELECT actual_tokens FROM decision_log WHERE pool_name='zzrespp' ORDER BY id DESC LIMIT 1").fetchone()
        frames30 = [json.loads(c) for c in chunks30 if c != "[DONE]"]
        content30 = "".join(f["choices"][0]["delta"].get("content", "")
                            for f in frames30 if f.get("choices") and f["choices"][0].get("delta"))
        usage30 = [f for f in frames30 if f.get("usage")]
        check("T30h Responses上游流式→chat chunk与计费落账",
              _st30h == 200 and chunks30[-1] == "[DONE]" and content30 == "答案"
              and usage30 and usage30[-1]["usage"]["total_tokens"] == 133
              and dec30 and dec30["actual_tokens"] == 133,
              (_st30h, content30, dec30["actual_tokens"] if dec30 else None))

        # ── T31（v2.16.2）缓存亲和路由（affinity 插件）：同 Key + 同池候选集 → 确定性同一模型条目 ──
        from app.plugins.installed.affinity.plugin import pick_target as _aff_pick

        def _dec31(pool):
            row = DB.execute("SELECT selected, steps FROM decision_log WHERE pool_name=? ORDER BY id DESC LIMIT 1",
                             (pool,)).fetchone()
            return dict(row) if row else {}

        _AFF_CANDS = ["zzbt/echo-token", "zzbt/echo-req", "zzbt/echo-smart"]

        # T31a 基线（插件关）：load_balance 轮询把连续请求打散到不同条目——即上游缓存 miss 的根源
        _sel_a = []
        for _ in range(3):
            chat("zzaff", max_tokens=300)
            _sel_a.append(_dec31("zzaff").get("selected"))
        check("T31a 插件关:轮询打散(≥2不同条目)", len(set(_sel_a)) >= 2, _sel_a)

        # 建亲和测试 Key + 启用插件（pools 留空 = 全部池）
        r = httpx.post(f"{BASE}/admin/keys", headers=ADMIN,
                       json={"name": "zzkey4", "type": "user", "allowed_pools": ["zzaff", "zzaffsw", "zzaffemb"],
                             "token_type": "daily", "billing_mode": "token", "limit_amount": 1000000}, timeout=15)
        _aff_kid = r.json().get("key", {}).get("id")
        # 按创建返回的 id 取 secret：套件内 T24 已先建过一个同名 zzkey4（allowed_pools=["zzsw"]），
        # 按名字查会取到旧行的 secret → 鉴权落在旧 Key 上被 403
        _aff_sec = DB.execute("SELECT secret FROM api_keys WHERE id=?", (_aff_kid,)).fetchone()["secret"]
        httpx.post(f"{BASE}/admin/plugins/affinity/config", headers=ADMIN,
                   json={"pools": "", "virtual_nodes": 40}, timeout=15)
        _r_en = httpx.post(f"{BASE}/admin/plugins/affinity/enable", headers=ADMIN, timeout=15)
        _exp = _aff_pick(_AFF_CANDS, str(_aff_kid), 40)

        # T31b 同 Key 六连请求全部粘同一条目（= 纯函数哈希期望），决策含 affinity_selected
        _sel_b = []
        for _ in range(6):
            chat("zzaff", auth=_aff_sec, max_tokens=300)
            _sel_b.append(_dec31("zzaff").get("selected"))
        _steps_b = json.loads(_dec31("zzaff").get("steps") or "[]")
        check("T31b 同Key六连粘同一条目(=哈希期望)+affinity_selected",
              _r_en.status_code == 200 and _exp in _AFF_CANDS
              and all(s == _exp for s in _sel_b)
              and any(s.get("reason") == "affinity_selected" for s in _steps_b),
              (_exp, _sel_b, [s.get("reason") for s in _steps_b]))

        # T31c 无状态：/admin/reload 重建注册表后同一 Key 仍落同一条目（记忆表方案重启即丢）
        httpx.post(f"{BASE}/admin/reload", headers=ADMIN, timeout=30)
        time.sleep(0.3)
        chat("zzaff", auth=_aff_sec, max_tokens=300)
        _sel_c = _dec31("zzaff").get("selected")
        check("T31c reload后落点不变(无状态哈希)", _sel_c == _exp, (_sel_c, _exp))

        # T31d 亲和目标停用（valve_pct=0）→ 记 affinity_unavailable 落回正常次序；恢复后自动回粘
        def _cfg31(mutate):
            _c = json.load(open(os.path.join(REPO, "config.json"), encoding="utf-8"))
            for _m in _c["models"]:
                if _m.get("id") == _exp:
                    mutate(_m)
            json.dump(_c, open(os.path.join(REPO, "config.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
            httpx.post(f"{BASE}/admin/reload", headers=ADMIN, timeout=30)
            time.sleep(0.3)

        _cfg31(lambda m: m.update(valve_pct=0))
        r = chat("zzaff", auth=_aff_sec, max_tokens=300)
        _d_fail = _dec31("zzaff")
        _cfg31(lambda m: m.update(valve_pct=100))
        chat("zzaff", auth=_aff_sec, max_tokens=300)
        _d_back = _dec31("zzaff").get("selected")
        _steps_d = json.loads(_d_fail.get("steps") or "[]")
        check("T31d 目标停用落回次序+恢复回粘",
              r.status_code == 200 and _d_fail.get("selected") not in (None, _exp) and _d_back == _exp
              and any(str(s.get("reason", "")).startswith("affinity_unavailable") for s in _steps_d),
              (_d_fail.get("selected"), _d_back, [s.get("reason") for s in _steps_d]))

        # T31e 池清单过滤：pools 不含 zzaff → 该池不亲和（决策无 affinity 步）
        httpx.post(f"{BASE}/admin/plugins/affinity/config", headers=ADMIN,
                   json={"pools": "zznonexist", "virtual_nodes": 40}, timeout=15)
        chat("zzaff", auth=_aff_sec, max_tokens=300)
        _steps_e = json.loads(_dec31("zzaff").get("steps") or "[]")
        check("T31e 池清单外不亲和",
              not any("affinity" in str(s.get("reason", "")) for s in _steps_e),
              [s.get("reason") for s in _steps_e])
        httpx.post(f"{BASE}/admin/plugins/affinity/config", headers=ADMIN,
                   json={"pools": "", "virtual_nodes": 40}, timeout=15)

        # T31f Switch 定向请求旁路：亲和目标可能属另一侧，switch 请求不得参与（防静默跨侧）
        r = sw_chat("zzaffsw", switch="local", auth=_aff_sec)
        _steps_f = json.loads(_dec31("zzaffsw").get("steps") or "[]")
        check("T31f switch定向旁路(无affinity步,本地侧选中)",
              r.status_code == 200 and _dec31("zzaffsw").get("selected") == "zzbt/sw-local"
              and not any("affinity" in str(s.get("reason", "")) for s in _steps_f),
              (_dec31("zzaffsw").get("selected"), [s.get("reason") for s in _steps_f]))

        # T31g embedding 模态预筛：chat 条目被预筛掉，候选集={emb,emb2}，落点=二者哈希期望
        _exp_g = _aff_pick(["zzbt/echo-emb", "zzbt/echo-emb2"], str(_aff_kid), 40)
        _sel_g = []
        for _ in range(2):
            httpx.post(f"{BASE}/v1/embeddings", headers=dict(ADMIN, Authorization="Bearer " + _aff_sec),
                       json={"model": "zzaffemb", "input": "亲和测试"}, timeout=30)
            _sel_g.append(_dec31("zzaffemb").get("selected"))
        check("T31g embedding预筛后粘同一条目(≠chat条目)",
              _exp_g in ("zzbt/echo-emb", "zzbt/echo-emb2") and all(s == _exp_g for s in _sel_g),
              (_exp_g, _sel_g))

        # 收尾：停用插件（deep_clean 兜底再清 config 残留）
        httpx.post(f"{BASE}/admin/plugins/affinity/disable", headers=ADMIN, timeout=15)

        # ===== T32（v2.17.0 #46 批1）：one_time expired 粘滞位自动复位 + 模型端点热生效 + 跨类型字段剥离 =====
        # T4 已让 echo-once 走到 used=266/expired=1；这里重置为 used=150/expired=1 模拟"额度耗尽置位后"的粘滞态
        db_exec("INSERT OR REPLACE INTO one_time_state (model_name, used_tokens, created_at, expired) "
                "VALUES ('zzbt/echo-once', 150, 0, 1)")
        time.sleep(5.5)  # 等 5s 配额预检缓存过期（T4 后本早已过期，此处保守等待）
        r = chat("zzonce", content="hi")
        check("T32a expired粘滞位拒绝(503)", r.status_code == 503, r.status_code)
        # PUT 调高总额度 + 顺手带上跨类型字段 gift_grant_cap：应自动复位 expired、剥离无关字段、热生效
        r = httpx.put(f"{BASE}/admin/models/zzbt/echo-once", headers=ADMIN,
                      json={"max_tokens": 100000, "gift_grant_cap": 5000000}, timeout=15)
        check("T32b0 PUT调高额度保存", r.status_code == 200, r.text[:80])
        _st32 = db_exec("SELECT used_tokens, expired FROM one_time_state WHERE model_name='zzbt/echo-once'").fetchone()
        check("T32b1 expired自动复位(1→0,无需改库)", _st32 and _st32[1] == 0, tuple(_st32) if _st32 else None)
        r = chat("zzonce", content="hi")
        check("T32b2 调高后立即恢复路由(200,PUT自带_sync_pool热生效)", r.status_code == 200, r.status_code)
        _m32 = next((x for x in httpx.get(f"{BASE}/admin/models", headers=ADMIN, timeout=15).json()["models"]
                     if x.get("id") == "zzbt/echo-once"), {})
        check("T32c 跨类型剥离(gift_grant_cap不残留)", "gift_grant_cap" not in _m32, _m32.get("gift_grant_cap"))
        r = httpx.delete(f"{BASE}/admin/models/zzbt/echo-once", headers=ADMIN, timeout=15)
        _cnt32 = db_exec("SELECT COUNT(*) FROM one_time_state WHERE model_name='zzbt/echo-once'").fetchone()
        check("T32d 删除模型清one_time状态行(防同名重建带旧expired)",
              r.status_code == 200 and _cnt32[0] == 0, (r.status_code, tuple(_cnt32)))

        # ===== T33（v2.18.0 #45 批2）：Anthropic Messages 工具调用全链（非流式/历史往返/流式）=====
        _tools33 = [{"name": "get_weather", "description": "查天气",
                     "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}}}]
        _ah33 = {**ADMIN, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        captured_bodies.clear()
        r = httpx.post(f"{BASE}/v1/messages", headers=_ah33, timeout=60, json={
            "model": "zztool", "max_tokens": 500, "tools": _tools33, "tool_choice": {"type": "any"},
            "messages": [{"role": "user", "content": "北京天气"}]})
        _j33 = r.json()
        _tu33 = next((b for b in (_j33.get("content") or []) if isinstance(b, dict) and b.get("type") == "tool_use"), None)
        check("T33a 非流式200+tool_use块(name/input正确)",
              r.status_code == 200 and _tu33 and _tu33.get("name") == "get_weather"
              and _tu33.get("input") == {"city": "北京"}, (r.status_code, _j33.get("content")))
        check("T33a2 stop_reason=tool_use", _j33.get("stop_reason") == "tool_use", _j33.get("stop_reason"))
        _b33 = captured_bodies[-1]
        _fn33 = ((_b33.get("tools") or [{}])[0].get("function") or {})
        check("T33a3 上游收到OpenAI形状tools+tool_choice(any→required)",
              _fn33.get("name") == "get_weather"
              and ((_fn33.get("parameters") or {}).get("properties") or {}).get("city", {}).get("type") == "string"
              and _b33.get("tool_choice") == "required",
              (_b33.get("tools"), _b33.get("tool_choice")))
        # 历史往返：assistant tool_use / user tool_result → 上游应收到 tool_calls + role:tool 消息
        r = httpx.post(f"{BASE}/v1/messages", headers=_ah33, timeout=60, json={
            "model": "zztool", "max_tokens": 500, "tools": _tools33,
            "messages": [
                {"role": "user", "content": "北京天气"},
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "tu1", "name": "get_weather", "input": {"city": "北京"}}]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "tu1", "content": "晴 25 度"},
                    {"type": "text", "text": "谢谢"}]},
            ]})
        _b33 = captured_bodies[-1]
        _ms33 = _b33.get("messages") or []
        _asst33 = next((m for m in _ms33 if m.get("role") == "assistant" and m.get("tool_calls")), None)
        _toolm33 = next((m for m in _ms33 if m.get("role") == "tool"), None)
        check("T33b 历史assistant→tool_calls(id/arguments正确)",
              r.status_code == 200 and _asst33 and _asst33["tool_calls"][0]["id"] == "tu1"
              and json.loads(_asst33["tool_calls"][0]["function"]["arguments"]) == {"city": "北京"},
              [m.get("role") for m in _ms33])
        check("T33b2 tool_result→role:tool消息(tool_call_id/content)",
              _toolm33 and _toolm33.get("tool_call_id") == "tu1" and _toolm33.get("content") == "晴 25 度", _toolm33)
        _ti33 = [m.get("role") for m in _ms33]
        check("T33b3 tool消息紧跟assistant、剩余text后随user",
              "tool" in _ti33 and "assistant" in _ti33
              and _ti33.index("tool") == _ti33.index("assistant") + 1 and "user" in _ti33, _ti33)
        # 流式：mock-toolcall 增量 tool_calls → Anthropic content_block_start/input_json_delta 事件流
        r = httpx.post(f"{BASE}/v1/messages", headers=_ah33, timeout=60, json={
            "model": "zztool", "max_tokens": 500, "stream": True, "tools": _tools33,
            "messages": [{"role": "user", "content": "北京天气"}]})
        _t33 = r.text
        check("T33c 流式200", r.status_code == 200, r.status_code)
        check("T33c2 content_block_start(tool_use,id/name)",
              '"type": "tool_use"' in _t33 and '"call_t1"' in _t33 and '"get_weather"' in _t33, _t33[:150])
        check("T33c3 input_json_delta增量", '"input_json_delta"' in _t33, "")
        check("T33c4 message_delta(stop_reason=tool_use)+message_stop收尾",
              '"stop_reason": "tool_use"' in _t33 and "message_stop" in _t33, "")

        # ===== T34（v2.18.3 #51 批6）：管理员（服务器密钥）用量可见（伪 Key #0，decision_log 口径）=====
        r = httpx.get(f"{BASE}/admin/keys/0/usage", headers=ADMIN, timeout=15)
        _u34 = r.json() if r.status_code == 200 else {}
        check("T34a 管理员用量历史200+24小时桶",
              r.status_code == 200 and len(_u34.get("hours") or []) == 24,
              (r.status_code, len(_u34.get("hours") or [])))
        check("T34b 管理员当日有token账(total>0)", (_u34.get("total") or 0) > 0, _u34.get("total"))
        r = httpx.get(f"{BASE}/admin/keys/0/calls", headers=ADMIN, timeout=15)
        _c34 = r.json() if r.status_code == 200 else {}
        check("T34c 管理员最近调用200+key_name正确",
              r.status_code == 200 and "管理员" in (_c34.get("key_name") or "")
              and len(_c34.get("calls") or []) > 0,
              (r.status_code, _c34.get("key_name"), len(_c34.get("calls") or [])))
        r = httpx.get(f"{BASE}/admin/keys/999999/usage", headers=ADMIN, timeout=15)
        check("T34d 不存在的Key仍404(回归)", r.status_code == 404, r.status_code)

        # ===== T35（v2.16.5）：图片 MIME 归一化 + 图片格式不支持改判可切换（书生 image/jpg 同款根治）=====
        # 根因：客户端内联图片 MIME 写成非标准 image/jpg，网关原样透传给严格校验的 Anthropic 兼容
        # 上游（书生 /v1/messages）→ 400 invalid_request_error → 命中请求缺陷分类 → 不切兜底直接透传。
        # 修复：①两个协议转换出口归一化 image/jpg→image/jpeg；②"图片格式不支持"从缺陷类改为
        # 可切换类（不冷却换候选直至兜底池，整链失败才透传原文）。
        from app.core.format_adapter import normalize_image_media_type
        check("T35a MIME归一化(jpg→jpeg/空缺省png/大小写容错)",
              normalize_image_media_type("image/jpg") == "image/jpeg"
              and normalize_image_media_type("") == "image/png"
              and normalize_image_media_type(" image/JPG ") == "image/jpeg", None)
        from app.providers.anthropic_provider import AnthropicProvider
        _ap = AnthropicProvider.__new__(AnthropicProvider)  # 跳过 __init__：_convert_content 不用 httpx client
        _orig_part = {"type": "image", "source": {"type": "base64", "media_type": "image/jpg", "data": "QQ=="}}
        _blocks = _ap._convert_content([
            {"type": "image_url", "image_url": {"url": "data:image/jpg;base64,QQ=="}},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,QQ=="}},
            _orig_part,
        ])
        check("T35b OpenAI→Anthropic 出口归一化 jpg 且保留 png",
              _blocks[0]["source"]["media_type"] == "image/jpeg"
              and _blocks[1]["source"]["media_type"] == "image/png", str(_blocks)[:200])
        check("T35c 直发 Anthropic 图片块改写且不动原请求对象",
              _blocks[2]["source"]["media_type"] == "image/jpeg"
              and _orig_part["source"]["media_type"] == "image/jpg", str(_blocks[2])[:150])
        r = chat("zzimgbad", content="hi")
        check("T35d 图片格式400换候选后兜底池接住(200)", r.status_code == 200
              and "答案" in ((r.json().get("choices") or [{}])[0].get("message", {}).get("content") or ""),
              r.status_code)
        _steps35 = reject_steps("zzimgbad")
        check("T35e 决策记录image_format_unsupported(不冷却)+兜底选中",
              any(x.get("reason") == "image_format_unsupported"
                  and (x.get("detail") or {}).get("no_cooldown") for x in _steps35)
              and any(x.get("reason") == "fallback_selected" for x in _steps35),
              [x.get("reason") for x in _steps35])
        r = chat("zzimgonly", content="hi")
        check("T35f 整链失败仍透传上游400原文", r.status_code == 400
              and "inline image type is not supported" in r.text, (r.status_code, r.text[:150]))

        # ===== T36（v2.16.6 自 hellonew v2.22.0 回移）：用量统计四视图 /stats 累计口径 + 缓存计费聚合 + 双面板元素在位 =====
        r = httpx.get(f"{BASE}/stats", headers=ADMIN, timeout=15)
        _st36 = {m["id"]: m for m in (r.json().get("models") or [])}
        _ec = _st36.get("zzbt/echo-cost", {})
        check("T36a stats透出累计口径(全模型 total_tokens_all/total_requests_all，T26 模型累计>0)",
              r.status_code == 200 and _st36
              and all("total_tokens_all" in m and "total_requests_all" in m for m in _st36.values())
              and (_ec.get("total_tokens_all") or 0) > 0 and (_ec.get("total_requests_all") or 0) > 0,
              (r.status_code, _ec.get("total_tokens_all"), _ec.get("total_requests_all")))
        check("T36b stats透出缓存计费聚合(cost_tokens=T26 累计 3次/命中80/未命中220/输出99)",
              _ec.get("cost_tokens") == {"calls": 3, "hit": 80, "miss": 220, "out": 99}, _ec.get("cost_tokens"))
        _idx = open(os.path.join(REPO, "static", "index.html"), encoding="utf-8").read()
        _hf = open(os.path.join(REPO, "static", "hfadmin.html"), encoding="utf-8").read()
        check("T36c 双面板四视图元素在位(四按钮/分组+付费渲染/拖拽持久化/旧维度按钮已移除)",
              all(s in _idx for s in ("stat-dim-billing", "stat-dim-provider", "stat-dim-pool", "stat-dim-paid",
                                      "statRenderGrouped", "statRenderPaid", "statGrpBind", "gw_statgrp"))
              and all(s in _hf for s in ('data-dim="provider"', 'data-dim="pool"', 'data-dim="paid"',
                                         "statRenderGrouped", "statRenderPaid", "statGrpBind"))
              and "stat-dim-requests" not in _idx and "stat-dim-tokens" not in _idx
              and 'data-dim="requests"' not in _hf and 'data-dim="tokens"' not in _hf, None)
        check("T36d 付费视图排序口径在位(有单价按累计费用/未设单价按累计token垫后)",
              all(s in _idx for s in ("_priced", "total_tokens_all", "未设单价 · 按累计 token 排序"))
              and all(s in _hf for s in ("_priced", "total_tokens_all", "未设单价 · 按累计 token 排序")), None)

        # ===== T37（v2.16.8 自 hellonew v2.22.3/v2.22.4 回移）：分组管理入口改版 + 统计外层系统组嵌套 =====
        check("T37a 双面板开关行⚙️管理入口在位(每行cog+维度管理盒+🗑删除+confirm确认)",
              all(s in _idx for s in ("grp-row-cog", "grpMgmtToggle", "grpMgmtDelete", "grpMgmtRename",
                                      "grp-mgmt-providerGroups", "grp-mgmt-poolGroups", "grp-mgmt-del", "confirm("))
              and all(s in _hf for s in ("grp-row-cog", "grpMgmtToggle", "grpMgmtDelete", "grpMgmtRename",
                                         "grp-mgmt-providerGroups", "grp-mgmt-poolGroups", "grp-mgmt-del", "confirm(")), None)
        check("T37b 统计外层系统组随收纳开关联动(outerOn门) + 嵌套元素在位(stat-ogroup/ocollapsed/未分组垫底)",
              all(s in _idx for s in ("const outerOn = grpSettings()[statDim === 'provider' ? 'models' : 'pools'];",
                                      "const odefs = outerOn ? (grpData()[okind] || []) : [];",
                                      "stat-ogroup", "ocollapsed", "o:__ungrouped__", "stat-ogroup-body", "sog-total"))
              and all(s in _hf for s in ("const outerOn = grpSettings()[statDim === 'provider' ? 'models' : 'pools'];",
                                         "const odefs = outerOn ? (grpData()[okind] || []) : [];",
                                         "stat-ogroup", "ocollapsed", "o:__ungrouped__", "stat-ogroup-body", "sog-total")), None)
        check("T37c index统计卡片增量键修正为x.m.id(v2.22.0起x.id致5s整卡重建闪屏)",
              "prev[x.id]" not in _idx and "seen.add(x.id)" not in _idx
              and "statCardEnsure(prev, grid, x, i); el.style.order = i; seen.add(x.m.id)" in _idx, None)

        # ===== T38（v2.16.9 四项修复）：Key 用量历史恒记 / 统计首开并行拉取 / 按池嵌套池改版 / 调用记录筛选序号守卫 =====
        # 问题1：charge_key_usage 原对管理员 Key 与未限额 Key 早退 → api_key_hourly_usage 恒空，
        #        用量历史弹窗除管理员伪 Key#0（decision_log 口径）外全空。改法：限额计量仍按原语义跳过，
        #        小时桶记录对所有带 Key 的调用恒写（main.py 四计费点 is_user_key→key 判定）。
        r = httpx.post(f"{BASE}/admin/keys", headers=ADMIN,
                       json={"name": "zzkey9", "type": "user", "allowed_pools": ["zzlocal"],
                             "token_type": "", "billing_mode": "token", "limit_amount": 0}, timeout=15)
        kid9 = r.json().get("key", {}).get("id")
        secret9 = DB.execute("SELECT secret FROM api_keys WHERE name='zzkey9'").fetchone()["secret"]
        chat("zzlocal", auth=secret9, content="hi")
        ru = httpx.get(f"{BASE}/admin/keys/{kid9}/usage", headers=ADMIN, timeout=15)
        check("T38a 无限额Key用量历史不再全空(小时桶恒写)",
              ru.status_code == 200 and (ru.json().get("total") or 0) > 0, (ru.status_code, ru.json().get("total")))
        r = httpx.post(f"{BASE}/admin/keys", headers=ADMIN,
                       json={"name": "zzkey10", "type": "admin"}, timeout=15)
        kid10 = r.json().get("key", {}).get("id")
        secret10 = DB.execute("SELECT secret FROM api_keys WHERE name='zzkey10'").fetchone()["secret"]
        chat("zzlocal", auth=secret10, content="hi")
        ru = httpx.get(f"{BASE}/admin/keys/{kid10}/usage", headers=ADMIN, timeout=15)
        check("T38b 管理员类型Key也写小时桶(带Key调用入账,服务器密钥仍走decision_log口径)",
              ru.status_code == 200 and (ru.json().get("total") or 0) > 0, (ru.status_code, ru.json().get("total")))
        rc = httpx.get(f"{BASE}/admin/keys/{kid9}/calls", headers=ADMIN, timeout=15)
        check("T38c 无限额Key最近调用记录可见(存量行为不回归)",
              rc.status_code == 200 and len(rc.json().get("calls", [])) >= 1,
              (rc.status_code, len(rc.json().get("calls", []))))
        # 问题2：loadStats 原 await /stats 之后再拉视图元数据（两轮串行往返，慢链路首开翻倍）；
        #        改为 /stats 与 providers/pools/models 并行。双面板静态接线断言。
        check("T38e 双面板统计首开提速(/stats与视图元数据并行拉取)",
              all(s in _idx for s in ("const metaNeed = statDim === 'provider' || statDim === 'pool' || statDim === 'paid';",
                                      "const metaP = metaNeed ? Promise.all([statMeta('providers'), statMeta('pools'), statMeta('models')]) : null;"))
              and all(s in _hf for s in ("const metaNeed = statDim === 'provider' || statDim === 'pool' || statDim === 'paid';",
                                         "const metaP = metaNeed ? Promise.all([statMeta('providers'), statMeta('pools'), statMeta('models')]) : null;")), None)
        # 问题3：按池视图嵌套池改版——模型多处归属不去重（卡片键 @池名）、父池内子池以计费卡片代替
        #        （stat-subcard 区分样式、treeModels 整树去重聚合）。纯前端，双面板静态接线断言。
        check("T38f 双面板按池嵌套改版(模型多处出现@池名卡片键+子池计费卡片stat-subcard+树聚合treeModels)",
              all(s in _idx for s in ("_ckey: x.m.id + '@' + pn", "cls: 'stat-subcard'", "const treeModels = pn =>",
                                      "x._ckey || x.m.id", "stat-card.stat-subcard"))
              and all(s in _hf for s in ("_ckey: x.m.id + '@' + pn", "cls: 'stat-subcard'", "const treeModels = pn =>",
                                         "x._ckey || x.m.id", "stat-card.stat-subcard")), None)
        # 问题4：loadDecisions 原无并发序号守卫——切池筛选与 5s 轮询并发时旧筛选的晚到响应覆盖当前视图
        #        （具体池视图显示旧数据）。与 loadStats 的 _statLoadSeq 同款守卫。双面板静态接线断言。
        check("T38g 双面板调用记录筛选序号守卫在位(旧筛选晚到响应丢弃)",
              all(s in _idx for s in ("let _logLoadSeq = 0;", "if (seq !== _logLoadSeq) return;"))
              and all(s in _hf for s in ("let _logLoadSeq = 0;", "if (seq !== _logLoadSeq) return;")), None)
        # 顺修：index statUpdateCard 增量字段 x.id/x.name 残留（v2.16.6 移植引入，T37c 只修了卡片键两处，
        #       漏了 .stat-id/.stat-name 两处——5s 增量更新会把模型名写成 "undefined"）。
        check("T38h index统计卡片增量字段修正(x.id/x.name→x.m.id/x.m.name,T37c漏网两处)",
              "setStatTxt(el, '.stat-id', x.id)" not in _idx
              and "setStatTxt(el, '.stat-name', x.name" not in _idx
              and "setStatTxt(el, '.stat-id', x.m.id)" in _idx
              and "setStatTxt(el, '.stat-name', x.m.name + ' · ' + x.billing)" in _idx, None)

        # ===== T39（v2.16.10 自 hellonew v2.22.8 回移）：余量监控工位（绑定/端点/刷新端口结果文件/调度熔断）=====
        _qres_file = os.path.join(REPO, "cheackresult.json")
        r = httpx.get(f"{BASE}/admin/quota/platforms", headers=ADMIN, timeout=15)
        _plats = r.json().get("platforms") or {}
        check("T39a 六平台元数据在位(deepseek/openrouter余额型+智谱bars型)",
              r.status_code == 200 and set(_plats) == {"minimax", "zhipu", "mimo", "qianfan", "deepseek", "openrouter"}
              and _plats.get("deepseek", {}).get("kind") == "balance"
              and _plats.get("openrouter", {}).get("kind") == "balance"
              and _plats.get("zhipu", {}).get("kind") == "bars", (r.status_code, sorted(_plats)))
        # custom 绑定：建→立即查询 unset「执行器未启用」（零网络零外呼）；自定义模式不走平台枚举
        r = httpx.post(f"{BASE}/admin/quota/bindings", headers=ADMIN, json={
            "platform": "custom", "name": "zz-quota-custom", "source": "provider", "provider_id": "zzmock",
            "enabled": True, "script_id": "zzdemo", "params_raw": "userId:123456;passToken:V1:zzsecret;"}, timeout=15)
        _cb = r.json()
        _cbid = _cb.get("id")
        check("T39b custom绑定建完即查unset执行器未启用(script_id/has_params回显)",
              r.status_code == 200 and _cbid
              and (_cb.get("result") or {}).get("status") == "unset"
              and "执行器未启用" in ((_cb.get("result") or {}).get("error") or "")
              and (_cb.get("binding") or {}).get("script_id") == "zzdemo"
              and (_cb.get("binding") or {}).get("has_params") is True,
              (r.status_code, (_cb.get("result") or {}).get("status")))
        r = httpx.get(f"{BASE}/admin/quota/bindings", headers=ADMIN, timeout=15)
        _ql = r.json()
        _cbv = next((b for b in _ql.get("bindings", []) if b.get("id") == _cbid), {})
        check("T39c 绑定列表三合一(snapshot.status/error+platforms+providers)",
              r.status_code == 200 and _cbv
              and (_cbv.get("snapshot") or {}).get("status") == "unset"
              and "执行器未启用" in ((_cbv.get("snapshot") or {}).get("error") or "")
              and "deepseek" in (_ql.get("platforms") or {})
              and any(p.get("id") == "zzmock" for p in _ql.get("providers", [])),
              (r.status_code, (_cbv.get("snapshot") or {}).get("status")))
        # 显式提交才覆盖：params_raw 留空 = 保持原值（不 trim 不解析）；停用 enabled=0 凭证保留
        r = httpx.put(f"{BASE}/admin/quota/bindings/{_cbid}", headers=ADMIN,
                      json={"platform": "custom", "params_raw": ""}, timeout=15)
        _row = DB.execute("SELECT params_raw, enabled FROM quota_bindings WHERE id=?", (_cbid,)).fetchone()
        _kept = _row["params_raw"] if _row else ""
        r2 = httpx.put(f"{BASE}/admin/quota/bindings/{_cbid}", headers=ADMIN,
                       json={"platform": "custom", "enabled": False}, timeout=15)
        _row2 = DB.execute("SELECT params_raw, enabled FROM quota_bindings WHERE id=?", (_cbid,)).fetchone()
        check("T39d PUT留空params_raw原值保持+停用enabled=0凭证保留",
              r.status_code == 200 and _kept == "userId:123456;passToken:V1:zzsecret;"
              and r2.status_code == 200 and _row2["enabled"] == 0 and _row2["params_raw"] == _kept,
              (r.status_code, _kept, r2.status_code, _row2["enabled"] if _row2 else None))
        r = httpx.delete(f"{BASE}/admin/quota/bindings/{_cbid}", headers=ADMIN, timeout=15)
        _left = DB.execute("SELECT COUNT(*) c FROM quota_snapshots WHERE binding_id=?", (_cbid,)).fetchone()["c"]
        _lefth = DB.execute("SELECT COUNT(*) c FROM quota_history WHERE binding_id=?", (_cbid,)).fetchone()["c"]
        rs = httpx.get(f"{BASE}/admin/quota/scripts", headers=ADMIN, timeout=15)
        check("T39e DELETE级联删快照历史零残留+scripts空目录返回[]",
              r.status_code == 200 and _left == 0 and _lefth == 0
              and rs.status_code == 200 and rs.json().get("scripts") == [],
              (r.status_code, _left, _lefth, rs.json().get("scripts")))
        # manual 绑定（DeepSeek 平台、base_url 指向本 mock 的 /user/balance）：建→查 ok 余额两位小数 + Key 掩码
        r = httpx.post(f"{BASE}/admin/quota/bindings", headers=ADMIN, json={
            "platform": "deepseek", "name": "zz-quota-ds", "source": "manual",
            "api_key": "sk-zztest-secret-xyz", "base_url": f"http://127.0.0.1:{MOCK_PORT}"}, timeout=15)
        _ds = r.json()
        _dsid = _ds.get("id")
        check("T39f deepseek manual建→查ok余额124.11+api_key掩码无明文",
              r.status_code == 200 and (_ds.get("result") or {}).get("status") == "ok"
              and (_ds.get("result") or {}).get("balance") == 124.11
              and (_ds.get("binding") or {}).get("api_key_masked") == "sk-z…-xyz"
              and "sk-zztest-secret-xyz" not in json.dumps(_ds, ensure_ascii=False),
              (r.status_code, (_ds.get("result") or {}).get("balance"), (_ds.get("binding") or {}).get("api_key_masked")))
        # cheackresult.json 落仓库根且绝不含密钥；独立刷新端口 health/result/单条 refresh（token 未设不鉴权）
        _qtxt = open(_qres_file, encoding="utf-8").read() if os.path.exists(_qres_file) else ""
        rh = httpx.get("http://127.0.0.1:18655/health", timeout=10)
        rr = httpx.get("http://127.0.0.1:18655/result", timeout=10)
        rp = httpx.post(f"http://127.0.0.1:18655/refresh?binding_id={_dsid}", timeout=30)
        _qdoc = json.loads(_qtxt) if _qtxt else {}
        _rrdoc = rr.json() if rr.status_code == 200 else {}
        _rpdoc = rp.json() if rp.status_code == 200 else {}
        _rpb = next((b for b in _rpdoc.get("bindings", []) if b.get("id") == _dsid), {})
        check("T39g cheackresult无密钥+刷新端口三端点(health/result与文件一致/refresh单条触发)",
              "sk-zztest-secret-xyz" not in _qtxt and "V1:zzsecret" not in _qtxt
              and rh.status_code == 200 and rh.json().get("ok") is True
              and rr.status_code == 200 and _rrdoc.get("generated_at") == _qdoc.get("generated_at")
              and rp.status_code == 200 and (_rpdoc.get("count") or 0) >= 1
              and _rpb.get("balance") == 124.11,
              (rh.status_code, rr.status_code, rp.status_code, "secret" in _qtxt,
               _qdoc.get("generated_at"), _rrdoc.get("generated_at"),
               _rpb.get("status"), _rpb.get("error"), _rpb.get("balance")))
        # 熔断：直写快照（bars 任一条 used_pct=100 且 reset_at 未到）→ 专用大限额池请求 503 + 决策原因码
        _now = time.time()
        db_exec("""INSERT INTO quota_bindings (platform, name, enabled, source, provider_id, created_at, updated_at)
                   VALUES ('custom', 'zz-quota-circ', 1, 'provider', 'zzmock', ?, ?)""", (_now, _now))
        circ_bid = DB.execute("SELECT id FROM quota_bindings WHERE name='zz-quota-circ'").fetchone()["id"]
        _fuse_payload = json.dumps({"bars": [{"label": "测试窗口", "total": 100, "used": 100,
                                              "used_pct": 100.0, "remaining_pct": 0.0,
                                              "detail": "T39 熔断夹具", "reset_at": _now + 3600}]}, ensure_ascii=False)
        db_exec("""INSERT OR REPLACE INTO quota_snapshots (binding_id, status, plan, payload, error, fetched_at)
                   VALUES (?, 'ok', '', ?, '', ?)""", (circ_bid, _fuse_payload, _now))
        httpx.post(f"{BASE}/admin/reload", headers=ADMIN, timeout=30)
        time.sleep(0.5)
        r = chat("zzpq")
        _steprow = DB.execute("SELECT steps FROM decision_log WHERE pool_name='zzpq' ORDER BY id DESC LIMIT 1").fetchone()
        check("T39h 供应商余量熔断503(bars任一条100%且reset_at未到→provider_quota_exhausted)",
              r.status_code == 503 and _steprow is not None
              and "provider_quota_exhausted" in (_steprow["steps"] or ""),
              (r.status_code, "provider_quota_exhausted" in (_steprow["steps"] or "") if _steprow else None))
        # error 快照不熔断（无证据不误杀）
        db_exec("UPDATE quota_snapshots SET status='error', payload='{}', error='mock' WHERE binding_id=?", (circ_bid,))
        httpx.post(f"{BASE}/admin/reload", headers=ADMIN, timeout=30)
        time.sleep(0.5)
        r = chat("zzpq")
        check("T39i error快照不熔断(无证据不误杀,恢复200)", r.status_code == 200, r.status_code)
        # reset_at 到期自动恢复
        _expired = json.dumps({"bars": [{"label": "测试窗口", "total": 100, "used": 100, "used_pct": 100.0,
                                         "remaining_pct": 0.0, "detail": "", "reset_at": _now - 1}]}, ensure_ascii=False)
        db_exec("UPDATE quota_snapshots SET status='ok', payload=? WHERE binding_id=?", (_expired, circ_bid))
        httpx.post(f"{BASE}/admin/reload", headers=ADMIN, timeout=30)
        time.sleep(0.5)
        r = chat("zzpq")
        check("T39j reset_at到期自动恢复(熔断解除200)", r.status_code == 200, r.status_code)
        # circuit_enabled=false 完全旁路（熔断快照在位仍 200），测后还原 true
        db_exec("UPDATE quota_snapshots SET payload=? WHERE binding_id=?", (_fuse_payload, circ_bid))
        httpx.put(f"{BASE}/admin/quota/settings", headers=ADMIN, json={"circuit_enabled": False}, timeout=15)
        httpx.post(f"{BASE}/admin/reload", headers=ADMIN, timeout=30)
        time.sleep(0.5)
        r = chat("zzpq")
        check("T39k circuit_enabled=false完全旁路(熔断快照在位仍200,测后还原true)", r.status_code == 200, r.status_code)
        httpx.put(f"{BASE}/admin/quota/settings", headers=ADMIN, json={"circuit_enabled": True}, timeout=15)
        # 双面板前端：无页签 + 挂载点位置分立（hfadmin 徽章后 / index 名字+ID 后，均不在按钮行）+ 弹窗配置区与四接线
        check("T39l 双面板前端在位(无页签/pq-inline位置分立/pqq-section/pqFill·Save·Sync·decorate接线/原因码chip)",
              "tab-quotas" not in _idx and "tab-quotas" not in _hf
              and '<div class="card-title"><span class="drag-handle">⠿</span> ${esc(p.name)} ${provBadge(p.protocol)}<span class="pq-inline" data-pq-inline="${esc(p.id)}"></span></div>' in _hf
              and '<div class="card-title"><span class="drag-handle" title="拖拽排序">⠿</span> 🔌 ${esc(p.name)}<span class="provider-id-tag">${esc(p.id)}</span><span class="pq-inline" data-pq-inline="${esc(p.id)}"></span></div>' in _idx
              and all(s in _idx for s in ("pqq-section", "pqFillEditSection", "pqSaveFromEdit", "pqDeleteFromEdit",
                                          "decorateProvQuota", "pqSyncBindings", "provider_quota_exhausted",
                                          "余量监控已保存并查询", "余量监控已停用"))
              and all(s in _hf for s in ("pqq-section", "pqFillEditSection", "pqSaveFromEdit", "pqDeleteFromEdit",
                                         "decorateProvQuota", "pqSyncBindings", "provider_quota_exhausted",
                                         "余量监控已保存并查询", "余量监控已停用")), None)

        # ===== T40（v2.17.0）：hmg-admin 第三面板（多页面真路由 + 共享资产 + 单 DOM 双主题，纯只读新增零后端行为改动） =====
        _r40root = httpx.get(f"{BASE}/hmg-admin", headers=ADMIN, timeout=15, follow_redirects=False)
        _pages40 = {p: httpx.get(f"{BASE}/hmg-admin/{p}", headers=ADMIN, timeout=15)
                    for p in ("model", "pools", "stats", "logs", "keys", "plugins")}
        check("T40a 六页路由200+/hmg-admin 307落地model/白名单外404",
              _r40root.status_code == 307 and "/hmg-admin/model" in str(_r40root.headers.get("location", ""))
              and all(r.status_code == 200 for r in _pages40.values())
              and httpx.get(f"{BASE}/hmg-admin/nope", headers=ADMIN, timeout=15).status_code == 404,
              (_r40root.status_code, {k: v.status_code for k, v in _pages40.items()}))
        _css40 = httpx.get(f"{BASE}/hmg-admin/assets/hmg.css", headers=ADMIN, timeout=15)
        _js40 = httpx.get(f"{BASE}/hmg-admin/assets/hmg.js", headers=ADMIN, timeout=15)
        check("T40b 资产可达+Content-Type+ETag在位+目录遍历防护(../model.html白名单外404)",
              _css40.status_code == 200 and _css40.headers.get("content-type", "").startswith("text/css")
              and _js40.status_code == 200 and _js40.headers.get("content-type", "").startswith("application/javascript")
              and bool(_css40.headers.get("etag")) and bool(_js40.headers.get("etag"))
              and httpx.get(f"{BASE}/hmg-admin/assets/..%2Fmodel.html", headers=ADMIN, timeout=15).status_code == 404, None)
        _etag40 = _pages40["model"].headers.get("etag")
        check("T40c 页面ETag/304协商+no-cache(内容未变零传输)",
              bool(_etag40) and _pages40["model"].headers.get("cache-control") == "no-cache"
              and httpx.get(f"{BASE}/hmg-admin/model", headers={**ADMIN, "if-none-match": _etag40}, timeout=15).status_code == 304, None)
        check("T40d 六页壳挂载点+主题引导+hmg资产引用+__GATEWAY_VERSION__已替换",
              all("__GATEWAY_VERSION__" not in r.text for r in _pages40.values())
              and all('id="hmg-sidebar"' in r.text and "hmg_theme" in r.text and "data-page=" in r.text for r in _pages40.values())
              and all("assets/hmg.css?v=" in r.text and "assets/hmg.js?v=" in r.text for r in _pages40.values()), None)
        check("T40e model页业务接线(供应商/模型CRUD+编辑弹窗+余量监控区+内联余量+思考参数+赠费校准+双拖拽)",
              all(s in _pages40["model"].text for s in ("function loadModels", "function editModel", "function submitModel",
                  "pqq-section", "pqFillEditSection", "pqSaveFromEdit", "pqSyncBindings", "decorateProvQuota",
                  "data-pq-inline", "renderReasoningStrip", "showGiftCalibrate", "bindProviderDrag", "bindModelDrag",
                  "openCostSettings")), None)
        check("T40f pools页业务接线(池CRUD/嵌套子池/单模型锁定/兜底/重命名/Switch门控/双拖拽)",
              all(s in _pages40["pools"].text for s in ("function loadPools", "poolItemHTML", "collectPoolModels",
                  "openSingleMode", "openFallbackTargets", "renamePool", "togglePoolSwitch", "toggleAutoOrder",
                  "toggleLoadBalance", "bindPoolDrag", "bindDrag")), None)
        check("T40g stats页业务接线(四维度/安全阀红线红区/gift余额口径/嵌套分组/付费视图/增量渲染/5s轮询守卫/分组网格等高)",
              all(s in _pages40["stats"].text for s in ('data-dim="billing"', 'data-dim="provider"', 'data-dim="pool"',
                  'data-dim="paid"', "stat-mark", "stat-redzone", "gift_yesterday_leftover", "statCardEnsure",
                  "statRenderGrouped", "statRenderPaid", "_statLoadSeq", "HMG.poll.start",
                  "${HMG.avatar(x.m.id)}", 'title="${esc(x.m.id)}"',
                  ".stat-group-body { display: grid; gap: 14px; grid-template-columns: repeat(auto-fill, minmax(290px, 1fr)); }")), None)
        check("T40h logs页业务接线(原因码全集含余量熔断/池筛选/增量刷新/序号守卫/5s轮询)",
              all(s in _pages40["logs"].text for s in ("provider_quota_exhausted", "fallback_switch_error", "peak_blocked",
                  "reasonChip", "formatDetail", "_logLoadSeq", "populateLogFilter", "HMG.poll.start",
                  "HMG.avatarDot(d.pool_name)", "HMG.busy(this, loadDecisions())", 'class="empty loading"')), None)
        check("T40i keys页业务接线(创建编辑/轮换日志/用量历史SVG/专属池/管理员伪Key#0)",
              all(s in _pages40["keys"].text for s in ("function loadKeys", "function submitKey", "showKeyRotations",
                  "loadKeyUsage", "usage-hours", "loadKeyOwnPool", "管理员（服务器密钥）",
                  'class="pool-chip"', "copyKey('${jsq(k.secret)}', this)", "btn-ok-flash")), None)
        check("T40j plugins页业务接线(插件卡启停/扫描/config_schema动态表单含pools·slider/headroom设置/节省统计入口)",
              all(s in _pages40["plugins"].text for s in ("function loadPluginCenter", "togglePlugin", "scanPlugins",
                  "pcField", "pcPoolsValue", "savePluginConfig", "openHeadroomSettings", "applyHrModeHighlight",
                  "showHeadroomStats")), None)
        # 共享资产层单源：grp 套件/成本预估/节省统计/轮询管理/双主题全在 hmg.js·hmg.css，六页零复制
        _hmgjs = open(os.path.join(REPO, "static", "hmg-admin", "assets", "hmg.js"), encoding="utf-8").read()
        _hmgcss = open(os.path.join(REPO, "static", "hmg-admin", "assets", "hmg.css"), encoding="utf-8").read()
        check("T40k 共享资产单源(hmg.js: 分组收纳/成本预估/节省统计/轮询管理/壳注入/双主题; hmg.css: Linear双token/三断点/减动效)",
              all(s in _hmgjs for s in ("function grpWrapHtml", "function grpSyncFromDOM", "function grpOpenSettings",
                  "function _parsePeakWindows", "function recalcCost", "function saveCostPrices",
                  "function showHeadroomStats", "const hmgPoll", "function hmgSetTheme", "function hmgRenderShell",
                  "function gwGroupsBoot"))
              and all(s in _hmgjs for s in ("function hmgHue", "function hmgAvatar", "function hmgAvatarDot", "function hmgBusy"))
              and all(s in _hmgcss for s in ("--canvas: #101116", "--canvas: #ffffff", "--accent: #5e6ad2",
                                             "@media (max-width: 1199px)", "@media (max-width: 767px)",
                                             "prefers-reduced-motion", "--hue0: #8f9aff", ".hmg-av {",
                                             "livePulse", "skelSweep", "hmgSpin")), None)
        # 残留闸复核：admin/hfadmin 双面板文件未引入任何 hmg 引用（文件级零改动由 git diff 把关，此处锚点复核）
        check("T40l 双面板零污染复核(两面板无 hmg-admin 引用,原余量锚点仍在你位)",
              "hmg-admin" not in _hf and "hmg-admin" not in _idx
              and "pq-inline" in _hf and "pq-inline" in _idx, None)

    finally:
        try:
            deep_clean()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            pass
        print(f"[suite] gateway proc returncode={proc.poll()}", flush=True)
        try:
            # shutdown() 放后台 daemon 线程执行（它要等 serve_forever 循环退出，可能等在途请求），
            # 主流程随即 server_close() 关监听套接字——既不滞留进程，也不留半关套接字的线程噪音。
            threading.Thread(target=srv.shutdown, daemon=True).start()
            srv.server_close()
        except Exception:
            pass

    fails = [n for n, okk in RESULTS if not okk]
    print(f"\n===== 回归套件 {len(RESULTS) - len(fails)}/{len(RESULTS)} 通过 =====", flush=True)
    for n in fails:
        print("  失败:", n, flush=True)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    import subprocess
    try:
        main()
    except Exception:
        traceback.print_exc()
        try:
            deep_clean()
        except Exception:
            pass
        sys.exit(1)
