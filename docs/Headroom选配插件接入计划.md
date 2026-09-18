# Headroom 选配插件接入计划

> 目标：把 [headroom](https://github.com/headroomlabs-ai/headroom)（上下文压缩中间件，72k★，Apache-2.0）作为 Model Gateway(8650) 的**选配插件**，对出站请求的 messages 自动压缩，降低上游 token 消耗。
> 原则：**默认关闭、config 开关、可灰度、可回退、故障旁路**——插件任何异常不得影响网关本职转发。
> 源码已克隆至 `D:\AIcoding\headroom`（commit 9f32800，浅克隆）；调研基于 headroom-ai 0.37.0（PyPI 实包验证）。
> 撰写日期：2026-09-15

---

## 一、可行性结论（已验证）

| 项 | 结论 | 依据 |
|:--|:--|:--|
| 端点匹配 | ✅ 完全重合 | headroom ASGI 中间件拦截 `POST …/v1/chat/completions`、`…/v1/messages`，正是网关仅有的两个聊天端点（`main.py:270-278`） |
| 接入 API | ✅ 现成 | 一行式库调用 `compress(messages, model)`，同步函数，返回 `CompressResult`（messages / tokens_before / after / saved / compression_ratio / transforms_applied） |
| 统计上报 | ✅ 现成 | `CompressionHooks.post_compress(event)` 观测回调；ASGI 模式还有 `x-headroom-tokens-saved` 等响应头 |
| CCR 风险 | ⚠️ 需显式关闭 | 库模式下 CCR 会往 `tools` 注入 `headroom_retrieve`，客户端没装 headroom 时模型调用会落空 → **网关场景必须 `CCRConfig(enabled=False)`** |
| 阻塞性 | ⚠️ 需线程隔离 | `compress()` 是同步函数，必须 `asyncio.to_thread()` 包裹，否则压缩耗时阻塞网关事件循环 |
| 依赖重量 | ⚠️ 需隔离环境 | 拉取 litellm、openai、onnxruntime（含编译 .pyd）等，与网关 fastapi/httpx/pydantic 2.x 无版本冲突，但应装独立 venv |
| 流式响应 | ✅ 无影响 | 压缩只发生在请求体，响应 SSE 透传链路（`_wrap_key_stream` 等）不动 |
| 非聊天端点 | ✅ 无影响 | embeddings / rerank / Ollama 探测 / admin 不在拦截范围 |

## 二、方案选型

| 方案 | 做法 | 优点 | 缺点 | 结论 |
|:--|:--|:--|:--|:--|
| A. ASGI 中间件 | `app.add_middleware(CompressionMiddleware)` 约 5 行 | 改动最小 | 黑盒：无法按 key/池灰度、无 per-request 超时熔断、内部同步调用阻塞事件循环、统计要解析响应头 | 备选 |
| **B. handler 内嵌（推荐）** | `_chat_handler` 内插一层插件调用 | 可控：config 开关 / 按池白名单 / dry-run / to_thread + 超时 / 异常旁路 / stats 入库 | 改动 ~1 个新文件 + main.py 数行 | **采用** |
| C. 外挂代理 | 起独立 `headroom proxy` 指向 8650，客户端改指向 | 网关零改动；CCR 完整可用（代理能看到完整对话流并自答检索调用） | 客户端要改端口；多一个进程 | 回退预案 |

## 三、部署形态

1. **独立虚拟环境**：`python -m venv D:\AIcoding\model-gateway\.venv-headroom`（或 conda env），内装网关 requirements + `headroom-ai==0.37.0`（锁版本，记入新建 `requirements-headroom.txt`）。不污染 miniconda base。
2. **import guard 懒加载**：插件模块顶层只 `try: import headroom` 探测可用性；未安装 → 插件静默禁用，网关照常启动。首次真正启用时才 import（headroom 导入链重，不拖累无插件启动）。
3. 网关单进程运行（`uvicorn.run` 未开 workers），headroom 的进程内单例 pipeline / 本地缓存无多进程一致性问题。

## 四、代码修改点

### 1. 新增 `headroom_plugin.py`（插件封装层，核心）

```python
# 职责：config 解析、懒加载、线程池隔离、超时熔断、异常旁路、统计上报
class GatewayStatsHooks(CompressionHooks):
    def post_compress(self, event): ...   # 异步投递到统计队列 → 落库

async def maybe_compress(body: dict, pool_name: str, caller: str) -> dict:
    # 1. config 未启用 → 原样返回（最快路径，一个 bool 判断）
    # 2. 池不在白名单 / 消息里无 tool 角色大块 → 原样返回
    # 3. asyncio.wait_for(asyncio.to_thread(_sync_compress, ...), timeout=10s)
    # 4. 任何异常 → logger.warning + 返回原文（旁路，绝不 fail-closed）
```

`_sync_compress` 内部要点：
- `compress(body["messages"], model=req.model, config=CompressConfig(...), hooks=GatewayStatsHooks())`
- CCR 关闭：管线级 `CCRConfig(enabled=False, inject_tool=False)`（实现时以 0.37.0 实际管道参数为准，参考 `headroom/config.py:583` 与 `compression/universal.py:68 ccr_enabled`）
- `CompressConfig(min_tokens_to_compress=500, protect_recent=4, compress_user_messages=False, compress_system_messages=False, kompress_model="disabled")`——初期只跑规则压缩（SmartCrusher + CodeCompressor），不下载 ML 权重
- dry-run 模式：正常计算压缩但**不替换** body，仅产出统计（headroom 的 `optimize=False` 透传语义需实测确认，必要时自己比较 before/after）

### 2. `main.py`（约 3 处小改）

- `_chat_handler` 在 `body = anthropic_to_openai(body)` 之后（约 158 行）、`ChatCompletionRequest(**body)` 校验之前插入：
  ```python
  body = await headroom_plugin.maybe_compress(body, pool_name_hint, caller)
  ```
  （pool_name 在 176 行才解析，插入点需要先做一次轻量 `_resolve` 或把压缩挪到 176 行 `_resolve` 之后、`execute_*` 之前——实现时二选一，倾向后者：池白名单判断最准确。）
- Anthropic 格式先转 OpenAI 再压缩：复用现有统一管线，压缩器只面对一种格式。
- `lifespan` 里加插件初始化/关闭（`hooks` 队列 join）。

### 3. `config.json` 新增 `headroom` 节点（默认全关）

```json
"headroom": {
  "enabled": false,
  "mode": "dry_run",              // dry_run | live
  "pools": ["auto"],              // 池白名单
  "min_tokens_to_compress": 500,
  "protect_recent": 4,
  "compress_user_messages": false,
  "compress_system_messages": false,
  "ccr_enabled": false,
  "inject_tool": false,
  "kompress_model": "disabled",   // 初期不加载 ML 压缩模型
  "target_ratio": null,
  "timeout_seconds": 10,
  "log_sample_rate": 1.0
}
```

### 4. `database.py`：新表 `headroom_stats`

字段：`ts, caller, pool, model, mode, tokens_before, tokens_after, tokens_saved, compression_ratio, transforms, latency_ms, error`。
dry-run 与 live 都记录——这是灰度决策的数据来源。加简单清理任务（如保留 90 天），挂现有 scheduler。

### 5. `admin.py` + `hfadmin` 面板（进阶，可后置）

- 开关读写（复用现有 config 管理 API 模式）
- 节省量看板：今日/7日 saved token、按池/按 caller 分布、压缩耗时 p95、旁路率（异常占比）

## 五、与现有机制的交互确认

| 机制 | 影响 | 处理 |
|:--|:--|:--|
| 用户 Key 计费（按上游 usage） | live 后计费量自然下降 | 正向，提前周知对账方 |
| ContextOverflowPassThrough | 压缩后上游 400 超限减少 | 正向 |
| Anthropic + tools | 网关本就显式拒绝（`main.py:153`） | 无交集 |
| OpenAI tools 调用 | tools 数组不压（只压 messages），tool 消息 content 是压缩主收益来源 | 正向 |
| 上游 KV-cache 命中率 | 压缩需保持前缀确定性才不破坏缓存 | **灰度期核心验证指标**；进阶可传 `frozen_message_count`（CompressConfig 已支持，代理模式会自动算，库模式需自己数上一轮消息数） |
| 20MB 请求体上限 | 不变 | 大请求正是收益主体，不设额外门槛 |

## 六、灰度发布步骤

1. **第 1 周 dry-run**：`enabled=true, mode=dry_run`。只记 headroom_stats 不生效。
   验收：tokens_saved 分布（期望工具输出重的会话 >50%）、压缩耗时 p95 < 500ms（to_thread 隔离下）、旁路率 < 0.1%。
2. **第 2 周 live 小流量**：`mode=live`，池白名单先只放 `auto`；观察上游各模型 cache 命中率不掉、客户端无 tool 调用报错、答案质量抽检。
3. **第 3 周全量 + 面板**：按池放开，kompress_model 视需求评估开启。
4. **回退**：config `enabled=false` 即完全旁路（热生效走现有 `POST /admin/reload`）；极端情况回退方案 C 外挂代理。

## 七、工作量估计

| 项 | 估时 |
|:--|:--|
| headroom_plugin.py + main.py 接入 | 0.5~1 人日 |
| headroom_stats 表 + 清理任务 | 0.5 人日 |
| admin 开关 + 面板看板 | 0.5~1 人日（可后置） |
| dry-run 灰度观察 | 3~5 天（挂机观察为主） |

## 八、附录：源码索引（D:\AIcoding\headroom）

| 文件 | 内容 |
|:--|:--|
| `headroom/compress.py` | `compress()` 一行式 API、`CompressConfig`、`CompressResult` |
| `headroom/integrations/asgi.py` | ASGI 中间件参考实现（拦截路径、缓冲、指标响应头） |
| `headroom/hooks.py` | `CompressionHooks`（pre/post_compress、biases） |
| `headroom/config.py` | `CCRConfig`（:583）、`PrefixFreezeConfig`、`HEADROOM_*` 环境变量约定 |
| `headroom/compression/universal.py` | 压缩管线与 `ccr_enabled` 管道参数（:68） |
| `examples/context_compression_demo.py` | 官方压缩效果演示 |
| `docs/` | 文档站（Next.js 源码），`docs/content/` 下有 proxy/平台文档 |
