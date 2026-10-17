<div align="center">

# 🚀 Hello My Gateway

### Model Gateway · 本地 LLM 自动切换网关 · 聚合多上游 · 配额保护 · 可视化管控

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Version](https://img.shields.io/badge/version-v2.14.0-orange)](#-版本)
[![Status](https://img.shields.io/badge/status-stable-brightgreen)](#)

对外暴露 **OpenAI 兼容**与 **Anthropic Messages** 接口，
对内聚合多上游模型，**自动配额管理 · 限流保护 · 故障切换 · 多模态降级**。

[**快速开始**](#-快速开始) · [**核心特性**](#-核心特性) · [**架构**](#-架构) · [**API 文档**](#-对外-api) · [**版本**](#-版本)

</div>

---

## 📦 v2.14.0 工程化重构与插件中心

本仓库（hello-my-gateway）是 model-gateway 的**重构工作副本**：代码按功能分层 + 设置页升级为插件中心。

```
app/
├── main.py            # FastAPI 入口（端点/生命周期）
├── core/              # 基础层：config / database / models / keyauth / reasoning / format_adapter / scheduler / logging / paths
├── gateway/           # 转发层：pool（选模/回退/计费）+ search_summary / search_sse / web_fetch
├── admin/             # 管理面 API（/admin/*）+ 共享鉴权 deps
├── providers/         # 上游协议适配（openai / anthropic / qianfan_search）
├── tools/             # probe_reasoning 等内部工具
└── plugins/           # ★ 插件中心：manager + base + installed/<插件>/
    └── installed/
        ├── switch_pool/   # 🔀 Switch 切换池（热插拔，POST /{池名}?switch=local|net）
        ├── headroom/      # 🪴 Headroom 上下文压缩（热插拔，默认停用）
        └── affinity/      # 🧲 缓存亲和路由（热插拔，默认停用）
providers→app/providers，static/ 前端，scripts/ 启动脚本，docs/ 全部文档，tests/ 回归测试
backup/ 备份统一归档（config/ db/ static/ snapshots/，见 backup/README.md）
data/ 运行时数据（gateway.db、探测缓存、dev pid），与代码隔离
```

- **插件中心**：面板「🧩 插件中心」页管理插件（卡片/启停开关/每插件设置），启用/停用/改配置**即时热生效**；
  自定义插件 = 在 `app/plugins/installed/` 放一个含 `manifest.json` 的文件夹 + 点「扫描新插件」，零代码即接
  （开发规范见 `docs/插件中心开发指南.md`；API：`/admin/plugins/*`）。
- **端口纪律**：生产 8650 仅由用户本人启动；开发/测试一律用 `scripts/start_dev_8651.ps1`（强制 8651 + 仅监听 127.0.0.1）。

---

## 📑 目录

- [✨ 核心特性](#-核心特性)
- [🏗️ 架构](#-架构)
- [🚀 快速开始](#-快速开始)
- [📂 项目结构](#-项目结构)
- [🎯 路由规则](#-路由规则)
- [🔌 上游适配](#-上游适配)
- [🧠 核心逻辑](#-核心逻辑)
- [⚙️ 配置说明](#-配置说明)
- [🎨 管理后台](#-管理后台)
- [📡 对外 API](#-对外-api)
- [💾 数据存储](#-数据存储)
- [📜 版本](#-版本)
- [⚠️ 已知限制](#-已知限制)

---

## ✨ 核心特性

| 🚨 故障转移 | 📊 配额管理 | 🎯 智能择优 |
|:---|:---|:---|
| 用量耗尽 / 限流触顶 / 上游报错 → **自动切换下一个** | 每日 Token、用量、5h 滚动窗口、`one_time` 一次性令牌、**使用量安全阀**（`已用+预估 ≥ k×上限` 即跳过路由，编辑界面滑块调节） | 顺序模式 或 **自动择优**（按实时延迟） |

| 🪆 多提供商聚合 | 🔀 模型池嵌套 | 📷 多模态降级 |
|:---|:---|:---|
| 同名模型（如 OpenAI + Azure `gpt-4o`）自动归入"大池子" | 池内可嵌套子池（`pool:子池名`），内置循环引用保护 | 请求含图片时自动跳过纯文本模型；无多模态时明确拒绝 |

| 🛡️ 兜底池可配置 | ⏱️ 延迟阈值可调 | 🔄 API 密钥自动轮换 |
|:---|:---|:---|
| 从兜底池卡片一键勾选为哪些池兜底 | 每池可配置 `slow_latency_threshold`（默认 3000ms，0=不限） | 用户密钥可设过期时长，到期自动生成新 secret，旧 secret 宽限期内仍可认证 |

| 🎨 双管理后台 | 🖱️ 拖拽排序 | 📜 详细调用记录 |
|:---|:---|:---|
| 传统 `/admin` + 科技感 `/hfadmin`，共用同一套后端 API | 池内模型、模型列表、池/供应商顺序均可拖拽 | 每条 step 携带具体数值（ms/RPM/错误类型/HTTP 状态） |

| 🔌 三接口官方格式 | 📡 流式支持 | ⚡ 自动测速 |
|:---|:---|:---|
| OpenAI Compatible / OpenAI Responses / Anthropic 三接口官方格式原生收发（含工具调用与状态化），网关自动转换 | SSE 流式透传，Anthropic 自动转 OpenAI chunk | 管理后台一键并发测速，滑动平均延迟 |

| 🧠 统一思考控制 | 🧪 计费回归安全网 | 📐 智能估算超时 |
|:---|:---|:---|
| `reasoning_effort` 六档 → 按模型 `reasoning_map` 换算上游思考参数；思考内容统一回传 `reasoning_content` / `thinking` | 293 断言计费回归套件（`test_billing_regression.py`）：流式/非流式/一次性/RPM 触顶/安全阀/并发入账零丢失，任何改动先跑套件再上线 | `smart_estimate` 模型按 token 量动态计算超时（吞吐 EMA 校准），样本不足自动回退固定值 |

| 📡 Embedding / Rerank | 🔑 用户密钥管理 | 📄 JSON 输出路由 |
|:---|:---|:---|
| `/v1/embeddings`、`/v1/rerank` 独立端点，仅路由到对应模态的模型 | 用户密钥可设限额（daily / 5h / 一次性）、计费模式、可用池，1 小时粒度用量历史，到期自动轮换 | 勾选 `json_output` 的模型组成硬门槛：带 `response_format(json)` 的请求只路由到支持的模型 |

| 🧲 缓存亲和路由（插件） | 🎚️ 亲和即优先 | 📈 命中可观测 |
|:---|:---|:---|
| 同一调用 Key 在同一池内始终优先路由到同一模型条目（无状态一致性哈希），提升上游提示词缓存命中率 | 亲和目标不可用（冷却/限流/高峰/配额尽/模态不符）自动落回池内正常次序，恢复后自动回粘；Switch 定向与单模型锁定不参与 | 决策日志 `affinity_selected` / `affinity_unavailable` 标签直接反映亲和命中情况，配合「预估费用」的缓存命中统计可量化改造成效 |

| ⚡ 高性能数据层 | 🔒 并发事务安全 | 🪶 轻量运行 |
|:---|:---|:---|
| `/stats` 单快照聚合（面板轮询 ~250 次查询 → 4 条 SQL）、流式结算合并单事务、RPM/TPM 内存滑窗零查询、上游长连接复用 | bulk 事务任务本地化：并发计费不互相捆绑，回滚不越界、崩溃不丢账，"响应返回前计费已落盘" | WAL 定期回收防膨胀、面板页面 mtime 缓存零重复读、后台标签页自动暂停轮询 |

---

## 🏗️ 架构

```
                          ┌─────────────────────────────┐
   客户端 (hermes 等)      │        Model Gateway         │
   ────────────────        │                             │
   OpenAI 协议 ───────────►│  /v1/chat/completions       │──► 上游 A: OpenAI / 兼容
                          │  /v1/messages (Anthropic)   │──► 上游 B: Anthropic
                          │                             │──► 上游 C: 自建 LLM
   x-api-key / Bearer ────►│  ┌───────────────────────┐  │
                          │  │  Auth  (verify_key)    │  │
                          │  └──────────┬────────────┘  │
                          │             ▼               │
                          │  ┌───────────────────────┐  │
                          │  │  ModelPool            │  │
                          │  │  ├─ select_model      │──► 配额 / RPM / TPM / 多模态
                          │  │  ├─ auto_order        │     冷却 / 上下文 / 嵌套
                          │  │  ├─ fallback_pool     │──► 兜底池回退
                          │  │  └─ usage + cooldown │     SQLite 持久化
                          │  └──────────┬────────────┘  │
                          │             ▼               │
                          │  ┌───────────────────────┐  │
                          │  │  providers/           │  │
                          │  │  ├─ openai_provider.py │──► OpenAI Chat Completions
                          │  │  └─ anthropic_provider │──► Anthropic Messages
                          │  └───────────────────────┘  │
                          └─────────────────────────────┘
```

**请求生命周期**：客户端 → Auth 鉴权 → Pool 选模（硬门槛过滤 + 择优排序） → Provider 适配 → 调用上游 → 计费入库 → 流式/非流式响应。

---

## 🚀 快速开始

### 环境要求

- **Python 3.10+**（用到了 `match` 语句、`dict | None` 等新语法）
- 网络可访问至少一个 OpenAI 兼容或 Anthropic 提供商

### 三步启动

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动
python -m app.main

# 3. 访问
# 管理后台:  http://127.0.0.1:8650/admin/
# API 地址:  http://127.0.0.1:8650/v1
```

### 首次配置

1. 浏览器打开 `/admin/`，输入管理密码（`config.json` 的 `server.api_key`；**首次部署务必改成强随机值**）
2. 在 **模型管理** 添加你的模型（OpenAI / Anthropic / 自建；推荐先建"供应商"再在其下挂模型）
3. 在 **模型池** 调整 `auto` 池的模型顺序
4. 在客户端（如 hermes）填 Base URL `http://127.0.0.1:8650/v1` 和同一 API Key

> 💡 **外部访问**：将 `config.json` 的 `server.host` 改为 `0.0.0.0`。Windows 防火墙会首次弹窗询问是否放行，需同意。
>
> 💡 **Ubuntu / Linux**：`python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt && python3 -m app.main`，或直接 `./start_gateway.sh`（自动选 venv/系统解释器；停止 `./stop_gateway.sh`）。代码本身跨平台：运行时目录（data/ logs/ backup/）首次启动自动创建，路径处理与日志滚动均适配 POSIX 文件系统。回归套件同样可在 Linux 运行：`python tests/test_billing_regression.py`。
>
> 🧪 **改动计费相关代码后**：`python tests/test_billing_regression.py` 运行 293 断言计费回归套件（自动在 8678 起隔离实例 + mock 上游，不触碰生产端口），全绿再上线。

---

### 🔁 开机自启（Windows）

根目录两个直达入口：`start_gateway.cmd`（双击即启，自动选择 .venv-headroom/miniconda 解释器，已在运行则直接退出）与 `stop_gateway.cmd`（停止本仓库网关，带身份核验防止误停其他环境的 8650 实例）。静默启动逻辑在 `scripts/start_gateway.ps1`（健康检查通过后主程序隐藏窗口后台运行）与 `scripts/stop_gateway.ps1`。

**方式一（推荐，当前使用）**：启动文件夹放置静默启动器（当前用户登录时自动拉起一次；只自启、不守护不保活）：

```bat
:: 文件：%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\model_gateway_autostart.vbs
CreateObject("Wscript.Shell").Run "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""D:\AIcoding\hello-my-gateway\scripts\start_gateway.ps1""", 0, False
```

删除该 .vbs 即取消自启。

**方式二**：注册表 Run 键（效果等同）：

```bat
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v ModelGateway /t REG_SZ /d "powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\AIcoding\hello-my-gateway\scripts\start_gateway.ps1" /f
```

手动重启：先双击 `stop_gateway.cmd`（或运行 scripts 下的 stop_gateway.ps1），再双击 `start_gateway.cmd` 即可。

## 📂 项目结构

```
hello-my-gateway/
├── app/                          # 全部源码（v2.14.0 起按功能分层）
│   ├── main.py                   # FastAPI 入口，对外 API 路由
│   ├── core/                     # 基础设施：config/database/keyauth/models/paths/reasoning/scheduler
│   │   └── paths.py              # 路径常量中心（运行时产物归 data/，备份归 backup/）
│   ├── gateway/                  # 核心：pool.py 模型池调度/计费，search_sse/search_summary/web_fetch
│   ├── admin/                    # 管理后台 API（模型/供应商/池/密钥 CRUD + 决策查询）
│   ├── providers/                # openai / anthropic / qianfan_search 上游适配器
│   ├── tools/                    # probe_reasoning 等内部工具
│   └── plugins/                  # 插件中心：base/manager/routes + installed/ 下可热插拔插件
│       └── installed/
│           ├── switch_pool/      # 🔀 Switch 切换池（POST /{池名}?switch=local|net）
│           ├── headroom/         # 🪴 Headroom 上下文压缩（非必装，无库自动旁路）
│           └── affinity/         # 🧲 缓存亲和路由（同 Key 同池粘同一模型条目，无状态一致性哈希）
├── scripts/                      # 启停与工具脚本（updater / dev8651 / 静默启停 ps1）
├── docs/                         # 全部文档
├── static/
│   ├── index.html                # 传统管理后台（/admin）
│   └── hfadmin.html              # 科技感管理面板（/hfadmin）
├── tests/
│   └── test_billing_regression.py # 计费回归套件（293 断言，隔离实例 + mock 上游）
├── start_gateway.cmd             # Windows 启动入口（自动选解释器，幂等）
├── stop_gateway.cmd              # 停止入口（身份核验，只停本仓库网关）
├── start_gateway.sh              # Linux/macOS 启动入口（语义同 .cmd：选解释器 + 已运行自检）
├── stop_gateway.sh               # Linux/macOS 停止入口（按端口定位 + cmdline 身份核验）
├── config.json                   # 全部配置：服务、模型注册、池定义（不入库，首次运行在根目录创建）
├── data/                         # 运行时产物：gateway.db / 探测缓存 / pid（不入库）
├── backup/                       # 配置/数据库/前端备份归档（不入库）
├── logs/                         # 日志（不入库）
└── requirements.txt
```

---

## 🎯 路由规则

客户端请求的 `model` 字段决定网关如何路由：

| 填写内容 | 路由行为 | 示例 |
|:---|:---|:---|
| `auto` | 走 `auto` 池（默认） | `"model": "auto"` |
| 池名 | 走该池，按池配置调度 | `"model": "deepseek"` |
| `池名:模型名` | 在指定池中匹配该模型 | `"model": "deepseek:gpt-4o-mini"` |
| 模型名 | 大池子：所有同名模型按序尝试 | `"model": "gpt-4o"` |
| `标签/模型名` | 精确指定某个模型实例 | `"model": "openai/gpt-4o"` |

### Anthropic Messages 格式调用

客户端可用 Anthropic SDK 直接调用（`/v1/messages` 或 `/v1/chat/completions` 均可），网关自动转换；v2.16.4 起支持完整工具调用（tools 定义、tool_use/tool_result 历史往返、出站 tool_use 块与流式 input_json_delta），Claude Code 类客户端可直接接入：

| 方向 | 处理 |
|:---|:---|
| **请求** | Anthropic Messages → OpenAI Chat Completions：`system` 字段提取、`messages` 内容块（text/image）转换、`stop_sequences` → `stop` |
| **认证** | `x-api-key: <密钥>` 或 `Authorization: Bearer <密钥>` 均可 |
| **响应** | OpenAI → Anthropic Messages：含流式事件 `message_start` / `content_block_delta` / `content_block_stop` / `message_delta` / `message_stop`；`message_delta.usage.output_tokens` 反映真实 Token 用量 |
| **限制** | Anthropic 格式暂不支持 `tools` / `tool_choice`（返回 400 明确拒绝，请用 OpenAI Compatible 或 OpenAI Responses 格式） |

### OpenAI Responses 格式调用（v2.16.0）

客户端可用 OpenAI SDK 的 `client.responses.create()` 直接调用（`base_url` 指向网关、以 `/v1` 结尾，与 OpenAI Compatible 同一配置），端点为官方路径 `POST /v1/responses`：

| 方向 | 处理 |
|:---|:---|
| **请求** | Responses → Chat Completions：`input`（字符串/item 数组）→ `messages`、`instructions` → `system`、`tools`（flat）→ nested function、`tool_choice` 映射、`reasoning.effort` → `reasoning_effort`、`max_output_tokens` → `max_tokens`、`text.format`（json_object/json_schema）→ `response_format`（json_output 路由门槛自动生效） |
| **工具往返** | `function_call` item → assistant `tool_calls`、`function_call_output` → role=tool；响应/流式增量中的 `tool_calls` → `function_call` output item（`call_id`/`name`/`arguments`，流式为 `response.function_call_arguments.delta` 事件） |
| **响应** | Chat → Responses：`object:"response"` + output items（`reasoning` / `message`(`output_text`) / `function_call`）+ 官方 usage 形状；流式事件 `response.created` → `output_item.added` → `output_text.delta` / `reasoning_text.delta` → `response.completed`（sequence_number 递增） |
| **状态化** | `store` 默认 true：响应自动存档（`previous_response_id` 引用完整上下文，一次查库链展开）；`GET /v1/responses/{id}` 检索、`DELETE /v1/responses/{id}` 删除；`item_reference` 在所引用响应 output 内一级解析。TTL 由 config 键 `responses_store_ttl_hours` 控制（默认 720 小时，存档时顺带清理过期行） |
| **限制** | 不支持 `background`、`conversation`、内置工具（`web_search` / `file_search` 等，仅支持 function 工具）、未知 item 类型（均 400 带原因）；`truncate` / `include` / `metadata` / `parallel_tool_calls` 忽略 |

---

## 🔌 上游适配

| `provider` | 协议 | 端点 |
|:---|:---|:---|
| `openai` | OpenAI Compatible | `{base_url}/chat/completions` |
| `openai_responses` | OpenAI Responses（v2.16.1） | `{base_url}/responses` |
| `anthropic` | Anthropic | `{base_url}/v1/messages` |

Anthropic 适配器自动完成：

- ✅ 提取 OpenAI 的 `system` 消息 → Anthropic `system` 字段
- ✅ `image_url`（base64 / URL）→ Anthropic `image` 块
- ✅ 响应转回 OpenAI 格式（`usage`、`finish_reason` 映射、流式 chunk）

---

## 🧠 核心逻辑

### 1️⃣ 可用性判定（硬门槛）

每次调用时，候选模型按以下优先级依次检查，**任一不通过即排除**：

| # | 条件 | 含义 |
|:---:|:---|:---|
| 1 | **用量** | 每日 Token / 请求次数用尽 → 排除（一次性模型到期永久失效） |
| 2 | **RPM** | 最近 60 秒请求数触顶 → 排除（实时重算，恢复快） |
| 3 | **TPM** | 最近 60 秒 Token 总量触顶 → 排除 |
| 4 | **多模态** | 请求含图片但模型为纯文本 → 排除 |
| 5 | **冷却** | 上游 429 / 5xx / 超时后冷却（时长分级见下） → 排除 |
| 6 | **上下文** | 估算 Token 超过模型 `context_window` → 排除 |
| 7 | **JSON 输出** | 请求带 `response_format(json)` 时仅保留勾选 `json_output` 的模型 |

### 2️⃣ 调用顺序（仅在可用模型中生效）

- **顺序模式**：严格按池内设定顺序，第一个可用的即被选中
- **自动择优模式**：在可用模型中按**实时延迟从低到高**优先（延迟来自测速 + 真实请求耗时的滑动平均）；超过池配置 `slow_latency_threshold`（默认 3000ms，0=不限）的模型直接排到末尾（nested 子池用各自阈值）
- **负载均衡模式**（`load_balance: true`，与自动择优互斥）：每次请求从**上次选中模型的下一个**开始轮转（round-robin），失败仍顺序尝试后续模型——即在多个可用模型间轮流分配请求，而非固定用第一个
- **🧲 缓存亲和**（`affinity` 插件启用后叠加在上述策略之前）：同一调用 Key 在本池候选集上确定性优先同一**模型条目**——无状态一致性哈希（`hash(key_id)` 落在候选环上的位置），重启/热加载不丢亲和关系，候选增删只影响相邻 Key 的落点。粘性粒度是条目而非模型名（上游提示词缓存跟随 上游账号×模型×前缀，同名模型挂多供应商时只有条目级粘性能命中）。亲和只是优先：目标不可用时照常走池内原策略并记 `affinity_unavailable`，恢复后自动回粘；候选先按请求形状预筛（模态/JSON/视觉）。`auto_order` 池不建议开启（亲和会覆盖其"优先消耗快到期配额"的排序语义）

### 3️⃣ 故障切换

选中模型调用上游，成功则返回；遇到 429 / 异常则标记冷却并切换下一个，直到成功或候选耗尽（返回 503 及具体原因）。

**冷却时长分级**（v2.10.5+）：

| 错误类型 | 冷却 |
|:---|:---|
| 上游 429 限流 | 10s |
| 上游 5xx（500-599） | 5s |
| 网络超时 / 连接失败 | 5s |
| 其他异常 | 5s（兜底） |
| 上下文超限（400 + context length 类报错） | **不冷却**（模型本身健康），尝试下一候选；整池容不下时把上游 400 原文透传给客户端 |

**兜底池回退**：当主池（及其子池）所有模型都不可用时，自动回退到该池 `fallback_pool` 指定的兜底池。可在管理后台从兜底池卡片一键勾选"为哪些池兜底"（auto 池强制兜底）。

### 4️⃣ 多模态降级

- 请求含图片时，按顺序跳过纯文本模型，选中第一个可用的多模态模型
- 若整个调用链中无任何可用多模态模型，返回 503：`请求包含图片，但池内没有可用的多模态模型`

### 5️⃣ 模型池嵌套

- 池内条目可以是**模型**，也可以是**子池**（`pool:池名`）
- 轮到子池时，按该子池自身配置（自动/顺序）调用其内部模型；子池耗尽则继续下一项
- 内置循环引用保护（A→B→A 不会死循环）
- **计费不重复**：所有嵌套最终解析到同一模型注册表条目（按模型 ID 唯一计数），同一模型无论直接引入还是经子池引入，都共享同一用量计数器

### 6️⃣ 一次性令牌（`token_type: "one_time"`）

- 首次调用记录创建时间，累加用量
- 到达 `max_tokens` 或超过 `ttl_seconds` 后永久失效
- 任何刷新都不重置

### 7️⃣ 余额返还制（`token_type: "gift"`，v2.11.3+ 显式选择）

针对"每天到账时刻赠送昨日消耗量（有日上限）"类活动（火山方舟 Ark 等）的配额模式。在模型编辑的**令牌类型**下拉中选择 `gift（余额返还制）` 即启用（v2.11.3 起为显式选择，不再做供应商名文字识别）；列表与统计页以「余额返还制」徽章取代「每日」显示。

- **连续余额账本**：每笔调用扣减余额；到 `refresh_time` 时刻补入 `min(昨日自然日用量, daily_token_limit)`——不再是"清零重置为满额"，消耗不均匀时也不会虚高
- 每天花 ≤ 上限 → 余额恒满；某天花超 → 超出部分永久扣除（与平台真实语义一致）
- 余额耗尽即预检拒绝（走兜底/下一候选），不会打到平台扣真实额度；网关停机错过到账时刻会按 `model_daily_stats` 历史惰性补账，不丢账
- 安全阀口径（v2.12.0）：gift 模型的阀门分母 = **min(当前可用总额池, 用户设置上限)**——补账时刻前后自动跟随池变化，与统计卡片分母同源；已用取今日自然日消耗
- `daily_token_limit` 复用为余额上限、`refresh_time` 复用为到账补账时刻；统计页大数字 = **今日自然日消耗**（真实值，不随补账窗口变化、不被上限钳制），进度条 = 今日消耗 / 当前可用量（14:00 前 = 上一日剩余；补账后 = 剩余 + 补账，随窗口变化），右下角 = 预计补账额度，完整明细见卡片 ⓘ 悬停
- 状态存于 `gift_state` 表（首次自动初始化为满额；如需对齐当前真实剩余可手工修改该表）

### 8️⃣ 计费模式

- `billing_mode: "token"`：按消耗 Token 计数
- `billing_mode: "request"`：按请求次数计数（每次 +1；流式在流建立成功时预扣 1 次，流式不再重复计）
- 流式按上游返回的 `usage.total_tokens` 真实入账（usage 到达即记 + 流结束兜底重试）；上游全程未返回 usage 时**不静默丢**——记调用次数并告警，token 记 0
- 所有计费写入合并为单事务（`db.bulk()`），并发入账经回归套件验证零丢失、零重复

---

## ⚙️ 配置说明

> 推荐通过管理后台修改（实时生效）；也可直接编辑 `config.json` 后重启。

```jsonc
{
  "server": {
    "host": "0.0.0.0",                  // 0.0.0.0 = 监听所有网卡；127.0.0.1 = 仅本机
    "port": 8650,
    "api_key": "your-strong-key"        // ⚠️ 暴露到局域网前务必修改
  },
  "default_mode": "auto",
  "default_reasoning_effort": "low",    // 请求不带 reasoning_effort 时的默认档（v2.10.2+，缺省 low；"auto"/"" = 用模型默认）
  "providers": [                        // 供应商：连接信息共享，模型通过 provider_id 继承
    {
      "id": "my-openai",
      "name": "我的 OpenAI",
      "protocol": "openai",             // openai / anthropic
      "base_url": "https://api.openai.com/v1",
      "api_key": "sk-xxx",
      "proxy_url": ""                   // 可选：该供应商走代理（http://127.0.0.1:7890）
    }
  ],
  "models": [
    {
      "id": "openai/gpt-4o",            // 唯一标识（标签/模型名）
      "name": "gpt-4o",                 // 模型名（同名自动归入大池子）
      "provider_id": "my-openai",       // 继承供应商的协议/base_url/api_key（与内联三件套互斥）
      "provider": "openai",             // 内联模式：openai（兼容）或 anthropic
      "base_url": "https://api.openai.com/v1",
      "api_key": "sk-xxx",
      "daily_token_limit": 1000000,     // 每日上限（0=不限；按次计费时单位为次）
      "valve_pct": 100,                 // 使用量安全阀 k（%）：已用+预估 ≥ k×上限 即跳过路由；0=停用该模型；上限 0（不限）不生效
      "rpm_limit": 60,                  // 每分钟请求上限（0=不限）
      "tpm_limit": 100000,              // 每分钟 Token 上限（0=不限）
      "context_window": 128000,         // 上下文窗口（0=不校验）
      "max_concurrency": 0,             // 最大并发（0=不限，默认 0）
      "token_type": "daily",            // 免费模型: daily / rolling_5h / one_time / gift / local；付费模型: all_day（全天可用）/ idle_only（仅闲时可用，高峰时段取 cost_peak.windows，留空=全天闲时）
      "billing_mode": "token",          // token（按 Token）/ request（按请求次数）
      "is_free": true,                  // 免费标注（绿）；付费模型显式设为 false（红，令牌类型即切换为 all_day/idle_only 两套）
      "cost_peak": {                    // 高峰期（与「缓存 Token 计费统计」同源互通，存储一处两处修改生效；仅 idle_only 用作路由闲时判定，其余键仅供计费）
        "enabled": false,               // 高峰分段计价开关（仅计费用）
        "windows": "9:00-12:00;14:00-16:00",  // 每日高峰时段（北京时间，多段分号分隔；空=当天全天）
        "weekdays": "12345",            // 每周高峰（周几 1=周一…7=周日；空=每天）
        "exdates": "1001-1007;0930",    // 特定谷峰日（MMDD 或 MMDD-MMDD 区间含首尾，起>止=跨年经元旦；每年循环，谷峰日全天闲时/平峰，优先级最高）
        "hit": 0, "miss": 0, "out": 0   // 高峰三单价（平峰用 cost_prices）
      },
      "modality": "vision",             // text / vision / embedding / rerank
      "json_output": false,             // true = 支持 response_format(json)，硬门槛路由
      "smart_estimate": false,          // true = 按 token 量动态计算非流式超时（吞吐 EMA 校准）
      "no_stream_options": false,       // true = 流式不下发 stream_options（本地 vllm 等不认时勾选）
      "refresh_time": "00:00",          // daily 窗口起点 / 余额返还制的到账补账时刻（北京时间）
      "timezone": "Asia/Shanghai",
      "reasoning_map": { },             // 思考映射：off/minimal/low/medium/high/max → 透传上游片段（可省略）
      // one_time 专属:
      "max_tokens": 0,                  // 一次性最大用量
      "ttl_seconds": 0,                 // 一次性存活时长（秒）
      "expire_date": ""                 // 一次性过期日期（YYYY-MM-DD，北京时间，到期次日失效）
    }
  ],
  "pools": {
    "auto": {
      "strategy": "sequential",
      "auto_order": false,              // true = 按延迟自动择优
      "load_balance": false,            // true = 轮转分配（与自动择优互斥）
      "slow_latency_threshold": 3000,   // 自动择优降级阈值（ms，0=不限）
      "fallback_pool": "兜底池",        // 主池耗尽时回退到此处
      "model_ids": ["openai/gpt-4o", "pool:fixed"]
    },
    "fixed": {
      "auto_order": false,
      "slow_latency_threshold": 3000,
      "fallback_pool": null,            // 留空则不启用兜底
      "model_ids": ["openai/gpt-4o-mini"]
    }
  },
  "single_override": {                  // auto 池"一键单模型"模式（面板开关，持久化于此）
    "auto": ""
  }
}
```

> ⏰ **时区**：统一固定为北京时间（Asia/Shanghai），不可修改。

---

## 🎨 管理后台

网关同时提供两套前端，**共用同一套 `/admin/*` 后端 API**：

| 入口 | 风格 | 适用 |
|:---|:---|:---|
| **`/admin/`** | 传统表格 | 习惯 classic 风格 |
| **`/hfadmin`** | 科技感卡片 | 现代化 UI，新功能优先在此落地 |

### 标签页功能矩阵

| 标签页 | 核心功能 |
|:---|:---|
| 📦 **模型管理** | 增删改模型；标注免费/付费、纯文本/多模态/嵌入/重排、计费模式；**JSON / 思考映射 / 智能超时 等能力徽章一眼可辨**；同名模型自动归入大池子；模型条目可拖拽排序并持久化；编辑弹窗按"计费与额度 / 配额与限流 / 流式与输出选项"分组，点编辑零请求直开，手机端单列适配 |
| 🪆 **模型池** | 新增/删除/重命名池；拖拽 ⠿ 调整优先级与池内模型顺序；加入模型或子池；开关"自动择优"；配置延迟阈值 ms（0=不限）；🎯 一键单模型（auto 池临时锁定单个模型）；⚡ 测速本池；从兜底池卡片一键选择为哪些池兜底 |
| 📊 **用量统计** | 卡片式可视化，进度条展示 已用/总量（青→琥珀→红分级），支持 计费量 / 调用次数 / Token 用量 三维度切换，每 5 秒自动刷新 |
| 📜 **调用记录** | 每次调用的完整选模过程与切换依据（选中/排除原因），支持按池筛选；每步携带 route_ms / upstream_ms 耗时分解，决策行含估算/实际 Token 对照 |
| 🔑 **API 密钥** | 用户密钥 CRUD：限额类型（daily / rolling_5h / 一次性）、限额值、计费模式（按 Token/按次）、可用池白名单、过期自动轮换，1 小时粒度用量历史条形图 |

### 调用记录的原因标签（v2.3.4+）

每条 step 除静态标签外还携带 `detail` 对象，前端渲染时**追加具体数值**（用 ` · ` 分隔），鼠标悬停看完整信息。无需 SQLite 迁移——`steps` 已经是 JSON 列。

| 标签 + detail 示例 | 含义 |
|:---|:---|
| 选中调用 | 最终选中该模型 |
| 用量已尽 · 1,500/1,000 (150%) | 配额或限流排除 |
| RPM 触顶 · 60/60 RPM | RPM 触发上限 |
| TPM 触顶 · 120,000/100,000 TPM | TPM 触发上限 |
| 安全阀触顶 · 已用 95,000 + 预估 5,020 ≥ k上限 80,000（k=80%） | 使用量安全阀：本请求预估消耗后将越过 k×最大量限制，跳过该模型 |
| **余量熔断 · 剩 3,600s 后重置**（或 · 「5小时窗口」已用尽） | 供应商余量工位查得该供应商耗尽（套餐任一条 100% 且未到 reset_at，或余额≤0）——整供应商 fail-close 跳过，到期或余量回升自动恢复（`provider_quota_exhausted`，v2.16.10） |
| 冷却中 · 剩 45s | 上游刚报错，临时跳过 |
| 超上下文窗口 · 请求估算 150,000 tok，上限 128,000 | 请求过长 |
| 不支持图片 · 模型为 text，请求含图 | 纯文本模型遇到图片请求 |
| 一次性已失效 · 已用 50,000/50,000 | 一次性模型到期/用完 |
| 一次性已失效 · 已存活 3700s / TTL 3600s | 同上，TTL 触发 |
| 子池无可用接口 | 嵌套子池内全部不可用 |
| **🧲 亲和选中** | 缓存亲和插件命中：该 Key 在本池候选集上的确定性落点（`affinity_selected`） |
| **🧲 亲和目标不可用 · （安全阀触顶等具体原因）** | 亲和落点被可用性检查拦下，本次落回池内正常次序（`affinity_unavailable:<原因>`），目标恢复后自动回粘 |
| **上游限流 → 切换 · HTTP 429 · 234ms · 冷却 5s** | 上游 429 限流后切换 |
| **上游错误 → 切换 · TimeoutError · HTTP 500 · 2300ms · 冷却 5s** | 上游错误后切换 |

**`detail` 字段参考**：

| reason | detail 字段 |
|:---|:---|
| `quota_exhausted` (daily) | `{used, limit}` |
| `quota_exhausted` (rolling_5h) | `{used, limit, window_remaining_sec}` |
| `rpm_limited` / `tpm_limited` | `{current, limit}` |
| `valve_exceeded` | `{used, estimated, limit, valve_pct, effective_limit, reason_detail}`（安全阀：已用+预估 ≥ k×上限） |
| `provider_quota_exhausted` | `{reset_at, remaining_sec, binding, bar, reason_detail}`（余量熔断：reset_at=null 表示无固定时刻，轮询回升即恢复） |
| `cooldown` | `{remaining_sec}` |
| `context_exceeded` | `{estimated, window}` |
| `no_vision` | `{modality}` |
| `one_time_expired` | `{used, limit}` / `{age_sec, ttl_sec}` / `{expire_date}` |
| `switch_429` / `fallback_switch_429` | `{status: 429, latency_ms, cooldown_sec}` |
| `switch_error` / `fallback_switch_error` | `{error_type, status?, latency_ms, cooldown_sec, error?}` |
| `context_overflow` | `{status: 400, no_cooldown: true, error}`（上下文超限透传） |

---

## 📡 对外 API

### 客户端 API

| 端点 | 方法 | 说明 |
|:---|:---:|:---|
| `/v1/chat/completions` | POST | 对话（支持 `stream`，OpenAI Compatible 与 Anthropic 格式均可） |
| `/v1/messages` | POST | Anthropic 格式对话（同 `/v1/chat/completions`；v2.16.4 起支持 `tools`/`tool_choice`/`tool_use`/`tool_result` 全量工具调用，含流式 `input_json_delta`） |
| `/v1/responses` | POST | OpenAI Responses 格式对话（v2.16.0，含工具调用与 `previous_response_id` 状态化，见上文专节） |
| `/v1/responses/{id}` | GET / DELETE | 检索 / 删除已存档的 Responses（`store=true` 时；创建 Key 本人或管理员） |
| `/v1/embeddings` | POST | OpenAI 兼容 embedding（仅路由到模态=embedding 的模型，响应透传上游） |
| `/v1/rerank` | POST | 重排（Jina/Cohere/SiliconFlow 兼容；仅路由到模态=rerank 的模型，响应透传上游） |
| `/v1/models` | GET | 模型与池列表 |
| `/v1/model/load` | GET | 查询单个模型当前并发负载（active 处理中 / waiting 排队，见[并发负载查询说明](并发负载查询说明.md)） |
| `/health` | GET | 健康检查 |
| `/version` | GET | 网关版本号（从 git tag 读取） |
| `/stats` | GET | 各模型用量统计 |
| `/speedtest` | POST | 测速（可传 `model_ids`） |

### 管理 API

| 端点 | 方法 | 说明 |
|:---|:---:|:---|
| `/admin/models` | GET/POST/PUT/DELETE | 模型 CRUD（编辑时 ID 不可变；PUT 为按键合并，显式提交的键才覆盖） |
| `/admin/models/reorder` | PUT | 调整模型管理列表顺序 |
| `/admin/model/{id}/metrics` | GET | 智能估算校准数据（样本数 / 吞吐 EMA / 平均输出） |
| `/admin/model/{id}/load` | GET | 单模型并发负载（active / waiting / max_concurrency，见[并发负载查询说明](并发负载查询说明.md)） |
| `/admin/providers` | GET/POST/PUT/DELETE | 供应商 CRUD（含 reorder） |
| `/admin/pools` | GET/POST/PUT/DELETE | 池 CRUD（含 rename / reorder / fallback_targets） |
| `/admin/pools/{name}/single_override` | POST/DELETE | auto 池"一键单模型"开关 |
| `/admin/decisions` | GET | 调用决策记录（含 estimated/actual_tokens、route_ms/upstream_ms） |
| `/admin/reload` | POST | 热重载配置（保存模型/池后自动调用） |
| `/admin/keys` | GET/POST/PUT/DELETE | 用户 API 密钥 CRUD（含 `expire_seconds` 自动轮换） |
| `/admin/keys/{id}/usage` | GET | 密钥用量（当前窗口 + 1 小时粒度历史） |
| `/admin/test_model` | POST | 单模型连通性测试 |
| `/admin/test_proxy` | POST | 代理连通性测试 |
| `/admin/reasoning` | GET | 各模型思考参数探测结论 + 生效映射 + 六档实际注入片段（纯本地读取） |
| `/admin/reasoning/probe` | POST | 强制重测单个模型的思考参数（后台执行，只更新探测缓存） |

> 另有 Ollama / Open WebUI 兼容探测端点（`/api/tags`、`/api/show`、`/props` 等），供客户端"连接检测"通过，真实对话仍走 OpenAI / Anthropic 协议。

### 调用示例

```bash
# 健康检查
curl http://127.0.0.1:8650/health

# 发起对话
curl -X POST http://127.0.0.1:8650/v1/chat/completions \
  -H "Authorization: Bearer your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "你好"}]
  }'

# 流式调用
curl -X POST http://127.0.0.1:8650/v1/chat/completions \
  -H "Authorization: Bearer your-key" \
  -H "Content-Type: application/json" \
  -d '{"model": "auto", "stream": true, "messages": [{"role":"user","content":"hi"}]}'

# Embedding（池内需有模态=embedding 的模型）
curl -X POST http://127.0.0.1:8650/v1/embeddings \
  -H "Authorization: Bearer your-key" \
  -H "Content-Type: application/json" \
  -d '{"model": "auto", "input": "你好"}'

# Rerank 重排（池内需有模态=rerank 的模型）
curl -X POST http://127.0.0.1:8650/v1/rerank \
  -H "Authorization: Bearer your-key" \
  -H "Content-Type: application/json" \
  -d '{"model": "auto", "query": "什么是重排", "documents": ["文档A", "文档B"], "top_n": 2}'

# 统一思考控制：reasoning_effort 六档 off/minimal/low/medium/high/max
# 网关按各模型配置的 reasoning_map 自动换算为上游思考参数；未配置的模型忽略该参数
curl -X POST http://127.0.0.1:8650/v1/chat/completions \
  -H "Authorization: Bearer your-key" \
  -H "Content-Type: application/json" \
  -d '{"model": "auto", "reasoning_effort": "high", "messages": [{"role":"user","content":"9.11 和 9.8 哪个大？"}]}'
```

### 🧠 统一思考控制（reasoning_effort）

客户端传 `reasoning_effort`，取值六档：`off / minimal / low / medium / high / max`（大小写不敏感；非法值 422）。不带参数 = 默认 `low` 档（v2.10.2+，config.json 顶层 `default_reasoning_effort` 可调）；传 `"reasoning_effort": "auto"`（v2.10.3+）= 本次用模型默认行为。

- **OpenAI 格式入口**：请求体顶层直接传 `"reasoning_effort": "high"`。
- **Anthropic 格式入口**（`/v1/messages`）：传 `thinking: {"type": "enabled", "budget_tokens": 8192}`，网关自动归一化——`disabled`→off；enabled 按 budget 分档（≤1024→minimal，≤4096→low，≤10240→medium，≤20480→high，>20480→max）。
- **模型配置 `reasoning_map`**（管理面板模型编辑 → "思考映射" JSON 框，或 config.json 模型条目）：显式声明"档位 → 透传上游的请求体片段"，例如：

```json
"reasoning_map": {
  "off":     {"thinking": {"type": "disabled"}},
  "minimal": {"reasoning_effort": "minimal"},
  "low":     {"reasoning_effort": "low"},
  "medium":  {"reasoning_effort": "medium"},
  "high":     {"reasoning_effort": "high"},
  "max":     {"reasoning_effort": "max"}
}
```

规则：片段原样 merge 进上游请求体（禁止覆盖 model/messages/tools/max_tokens 等核心字段）；请求档位未配置时回落到更低档位中最近的（无更低取最低配置档）；未配置 `reasoning_map` 的模型不注入任何参数。anthropic 协议上游注入 `thinking.budget_tokens` 时若大于 max_tokens 会自动抬高（+1024）。

- **思考内容回传**：非流式 OpenAI 响应统一带 `reasoning_content` 字段（MiniMax 等 `<think>` 内联的模型自动提取）；Anthropic 客户端方向自动转换为 `thinking` 块 / 流式 `thinking_delta`。流式 OpenAI→OpenAI 原样透传。
- **思考参数探测（纯人工触发，v2.15.3 起新增不再自动跑）**：编辑弹窗 🧠 探测条点「🔄 重新探测」实测该云端模型支持的思考参数写法与档位（结论只进缓存 `data/reasoning_probe_cache.json`，reasoning_map 是否采用由用户点「📥 填入建议」决定）；批量补测用 `python -m app.tools.probe_reasoning`（`--report` 按需再生成报告、`--apply` 写入 config.json、`--filter 关键字` / `--force` 控制范围）；embedding/rerank 模态不参与思考控制（输入框隐藏、映射自动剥离）。

---

## 💾 数据存储

SQLite（`gateway.db`）持久化以下表：

| 表 | 用途 |
|:---|:---|
| `token_usage` | 每模型每日累计用量（按 `refresh_time` 窗口判定） |
| `request_log` | 最近 60 秒请求记录（RPM/TPM 滑动窗口） |
| `one_time_state` | 一次性模型的用量、创建时间、是否过期 |
| `rolling5h_state` | 滚动 5h 窗口限额的用量与窗口起点 |
| `decision_log` | 最近 500 条调用决策（选模过程、估算/实际 Token、route_ms/upstream_ms 耗时分解） |
| `call_metrics` | 智能估算校准样本（估算值、真实 prompt/completion/total、耗时），驱动吞吐 EMA |
| `model_daily_stats` | 模型每日调用次数 / Token 用量统计 |
| `api_keys` | 用户/管理员 API 密钥（`expire_seconds` / `rotated_at` / `previous_secret`） |
| `api_key_usage` / `api_key_hourly_usage` | 密钥当前窗口用量 / 1 小时粒度历史用量 |
| `key_rotation_log` | 密钥轮换审计日志（仅存前 8 字符前缀，不存完整密钥） |
| `users` | 守护面板用户 |

> 🔐 **API 密钥轮换**：密钥可设置过期时长（秒），到期自动轮换新 secret，旧 secret 宽限期（约过期时长的 20%，1~24 小时）内仍可认证。
>
> 💾 写入均为 WAL 单连接 + `db.bulk()` 合并事务（计费多表一次 commit），读不阻塞写。

---

## 📜 版本

### 最新

**`v2.17.23`** — **🩹 模型池改名导致分组成员丢失（反馈：模型池分组数据更新后丢失了，检查代码）**：**根因是分组成员存的是「池名字符串」而非对象引用，而 `rename_pool` 只迁移了池字典本身与其他池里的 `pool:old` 子池引用，完全没动 `config.ui_groups.poolGroups[].members`**。后果链条——①池改名后 `grpWrapHtml` 按 `members.includes(池名)` 过滤渲染，新名匹配不到旧名，**成员静默从分组消失掉进「未分组」**；②**放大效应**：下一次任意拖拽触发 `grpSyncFromDOM` 按 DOM 重写整张成员表，把旧名**彻底抹掉**，用户此后无从察觉也无从恢复。实测复现（修复前）：`Model模型:DeepSeek-V4-Flash|Qwen全家桶` → `POST /admin/pools/…/rename` 返回 **200 ok**（无任何报错）→ `Model模型:Qwen全家桶`，成员丢失、池掉进 `__ungroup__`，属**静默数据丢失**。**修法**：新增共用迁移函数 `_ui_groups_migrate_member(config, kind, old, new=None)`（`new=None` 表示成员已删除直接剔除），接到三个此前都漏了这条路径的处理器——`rename_pool` 改名迁移、`delete_pool` 剔除悬空引用、`delete_provider` 剔除悬空引用（成员存供应商 id）。**顺带厘清供应商改名为何不受影响**：`providerGroups.members` 存 id，而编辑弹窗把 id 设为不可改（`if key == "id": continue`），改名走「新建+删除」、id 不变，不存在池改名那种名字失配路径。验证：改名后成员表 `DeepSeek-V4-Flash→DS-V4-Flash` 正确迁移且池留在 `gmodel` 内；删池 `["zz-del-test","Hermes"]→["Hermes"]`；删供应商 `["dotsai","intern-ai"]→["intern-ai"]`；语法检查过、回归 **293/293** 全绿。影响面仅 `app/admin/routes.py`，**含后端改动、部署需重启进程**（不同于前几版纯资产刷新），`static/` 零改动。

**`v2.17.22`** — **🎬 动效线：motion-design skill 清单落地（依据 LottieFiles motion-design v1.0.0；§9 红线经裁决只管 hmg-admin 工作线，本轮解封）**：清单 10 项独立核对后**6 项属实已修、3 项前提错误、1 项已过期**，另**新发现 1 个语义 bug** 并顺带修复。①**C1（无障碍 CRITICAL）**：`index.html` 零 `prefers-reduced-motion`（hfadmin 有 4 处 / hmg.css 1 处，历史遗留不同步），两处 infinite 循环 `ping 1.8s` 状态点扩散与 `sheen 2.6s` 扫光对前庭功能障碍用户是持续刺激——照 hfadmin:713 写法补齐，装饰扫光改 `animation:none`（`.01ms` 下会闪一帧后停在初始位）、进度条 width 过渡归 0；**Playwright reduced_motion 实测**：`no-preference` 下 `ping/1.8s` → `reduce` 下 **`none/0s`**。②**C2**：`index.html:334` 与 `hfadmin.html:514` 的 `.stat-fill` 同时挂 `transition:width` 与 `animation:fillIn`(scaleX)，可见宽度 = width(t)×scaleX(t) 两条 ease-out 叠乘，前段快得不是设计曲线——两文件均删动画与随之失效的 `@keyframes fillIn`（已确认全仓无其他引用），JS 侧本就是双 rAF 后设 width 触发过渡，视觉无损。③**H1**：清单称 `pools.html:254` 无封顶，**实为 `Math.min(i*35,400)` 早已合规**（此条过期）；真正未封顶的 `hfadmin.html:2501` 已补封顶。④**H2-a 前提错误**：`index.html:156` 的 `.ts-thumb` 是 `onclick="location.href='/hfadmin/'"` 里的纯装饰子元素，**全仓无规则移动过它的 `left`**——那是死代码不是「布局属性做过渡」，照建议加 `:checked` 等于凭空造开关；只删死声明。⑤**H2-b 前提错误**：`hfadmin.html:580` 的 `.usage-grid/.usage-col/.usage-bar-wrap/.usage-bar` **全仓零 markup 零 JS**——`loadKeyUsage()` 渲染的是内联 SVG，这四条是 SVG 重写前的遗留死 CSS，`transition:height` 从未运行；不删（删等于删废弃特性样式），记为发现。⑥**H3**：index.html 4 处 `transition:all` 按各自 hover 实际变化集展开（`.btn` 五项含 box-shadow/transform、`.arrows button` 与 `.info-dot` 三项、`.remove-x` 两项）；**全文扫描残留 0**。⑦**M1 + 新发现语义 bug**：`keyPulse` 的 box-shadow 属 skill 性能表 Avoid 档且 2.6s 无休止重绘，改伪元素 + `transform/scale` 只动合成层；而评审未发现——原 keyPulse **无条件**挂在 `.key-dot` 上，**未登录的红点同样泛绿色光晕**，且盖掉了 `.has-key` 原有静态环，现收敛到 `.has-key` 才有。⑧**M2**：`.overlay` 靠 display 切换导致退场为 0，补 **150ms** 退场（= 入场 220ms 的 68%，落在 skill「退场 = 入场的 65-75%」，`ease-in` 加速离场）；保留 display 切换以免 `.modal` 的 `pop` 入场每次打开不再重放，12 处打开点收敛为 `openModal`、4 条关闭路径收敛为 `closeModalEl`，元素挂 `_closeTimer` 防「关完 150ms 内又开」竞态；选择器 `.overlay.show.closing`(0,3,0) 压过 `.overlay.show`(0,2,0) 与声明顺序无关。**知情偏离**：H2-c `hmg.css .grp-inner` 的 padding/margin 过渡经裁决不改——手风琴可变高度折叠本质即 layout 动画，transform 无法做真实披露。CSS 括号 index 405/405、hfadmin 479/479 平衡，`node --check` 两页内联脚本均过，回归 **293/293** 全绿。`static/hmg-admin/` 本轮零改动，纯资产刷新即见。

**`v2.17.21`** — **🔤 hmg-admin 模型名升为行内主角（反馈：模型的名字对比度还是低了，不容易发现）**：①**诊断为主信息被自己的徽章压过去**——实测模型行各类文字对卡片底的对比度：`.model-name` 暗 6.47 / 亮 6.40，而 `--ink` 正文基准是 13.46 / 17.91（**主信息只有正文的三分之一到二分之一**）；`.model-id` 4.66 / 5.32 勉强过 AA；`.badge-ok`「免费」徽章暗 **10.08**，**比模型名高 3.6** 且自带底色+描边+圆角三重强化 → 暗色下行内最该被看到的模型名反而不如自己的徽章显眼，扫读视线先落在徽章上；②**根因是 v2.17.15 P2-3 的意图从未落地**——原注释写「22 字符 mono 退到第三级、模型名当主角」，实现却把两者**一起降级**（id 用 ink-tertiary 第三级 ✓，name 用 ink-subtle 第四级 ✗），注释与实现相反，模型名被放到比 ID 更弱的一级；③**修法**：`.model-name` 回 `--ink`、13px/500，CR 6.47 → **13.46**（暗）/ 6.40 → **17.91**（亮），与正文基准完全对齐，暗色下已反超徽章；`.model-id` **维持 ink-tertiary 不动**（本就是 P2-3 的正确部分，且 T40v 以精确子串钉死该行，改则破锚点）；改后三级阶梯清晰 **供应商名 15px/600 › 模型名 13px/500 › 模型 ID 12px/400**；④终态：1920/1440/768/390 四档零横滚、13 行行高集合 [45,46] 整齐、无模型名被截断、≤1199 徽章独立成行布局未受影响、`node --check` 过；回归 **293/293** 全绿。仅 `model.html` 一行样式，纯资产刷新即见。

**`v2.17.20`** — **🎚️ hmg-admin 分组可发现性回退重做 + 收放动画（反馈：文字对比度低了、分组改成透明背景后不容易被发现，还是不适合；追加：展开和折叠能不能加动画）**：①**v2.17.19 错在哪（先量后改）**：把两条反馈落成可测指标——组头底 vs 画布仅 **1.11/1.09**，比它收纳的成员卡（1.15/1.14）**还不可见**；亮色 `--wash` 是深色半透明，把成员数 `grp-count` 对比度从 5.32 压到 **4.29 跌破 AA**。**教训：层级 ≠ 削弱可发现性**——用「让父级变弱」表达从属，结果父级本身都看不见；②**改实心面 + 强调边**（替代削弱式层级）：组头底 `--wash`→`surface-2`（vs 画布 1.11→**1.26**，已高于成员卡 1.15）、左色条 2px hairline→**3px `--accent`**（vs 画布 **3.92/4.12**，双主题最强可发现性载体，沿用 v2.17.15 P1-3 供应商卡色条先例）、补 `--hairline` 描边、保持无投影（非卡片语义）、字号 12.5→**13px**、成员数 `ink-tertiary`→**`ink-subtle`**（4.84→**5.91** / 4.29→**5.81**，双主题过 AA）；「未分组」退一档但不隐形；缩进 28px + 竖导轨 + `minmax(0,1fr)` 横滚修复原样保留；③**收放动画**（工作单 §16.7「动效必须答出目的」：让「卡片正被收进/放出一个组」的因果可见，正对「关联度较差」）：`.grp-body` 只开一条行 `1fr`↔`0fr`，`0fr` 把唯一子项压成 0 高、溢出由 `.grp-inner{overflow:hidden}` 裁掉 → 高度可 transition（旧 `display:none` 不可过渡），不写死 max-height 故无需量高；`--t2` 200ms（<300ms 红线内）+ `--ease-out`，三角标同步 200ms；减动效显式补 `transition-duration:.01ms`（全局规则只压 `animation-*` 不覆盖 `transition-*`）；④**取证陷阱实录×2**：`minmax(0,1fr)`→`0fr` 是**假动画**——track sizing 函数不同 Chrome 无法插值，`getAnimations()` 仍报 `running` 的 CSSTransition（**不可当动画存在的证据**），44 帧采样零中间值直接 1243→0 跳变；改 `1fr`→`0fr` 后轨迹为 `1243→20→9→3→1→0`；`.grp-inner` 为末卡投影留的 `padding-bottom:8px+margin-bottom:-8px` 收起后撑出 8px **隐形点击带**（`elementFromPoint` 实测 `hitInside:true`），收起态一并归零后为 false；⑤终态：双向插值各 4 个中间帧、折叠后 `innerH=0/hitInside=false`、重展 4 成员完整还原、1920/1440/768/390 四档零横滚、3 个分组节 `.grp-inner` 落点齐备（落点已从 `.grp-body` 改指 `.grp-inner`，`grpSyncFromDOM` 用 `sec.querySelectorAll` 不受影响）、双主题对比度未因动画回退；回归 **293/293** 全绿。影响面仅 `static/hmg-admin/`（hmg.css/hmg.js/pools.html/model.html），`index.html`/`hfadmin.html` 按 §9 红线零改动。纯资产刷新即见。

**`v2.17.19`** — **🖥️ hmg-admin 全屏铺满 + 分组层级重构（反馈：全屏时右侧 1/5 有背景但组件不展示，好像宽度只到左侧 4/5；供应商、模型池等的分组与内部模型关联度较差，同等规模没体现从属关系）**：①**P0 全屏右侧空 1/5**：`.hmg-main { max-width: 1492px }` 把内容列钉死（该值含 244px 左侧留白），视口宽于它时右侧只剩背景——1920 屏实测内容右缘 1460px、右侧空白 420px（≈1/5）→ 去掉上限铺满，右缘 1460 → **1888**（= 1920−32 右内边距）；②**P1 分组层级重构**：组头与成员卡改前**同为** surface-1 底 + hairline + 圆角 + 投影、成员区仅缩进 2px，展开后读作同级并列 → 三重信号表达父子：组头降级为「区段栏」（去卡面/描边/投影，改弱底 + 左色条，实测 box-shadow:none 对成员卡完整三层投影）、成员缩进 28px（窄屏 16px）、成员区左侧挂竖导轨（折叠随体内一并消失）；「未分组」兜底桶弱化；stats 页自建 .stat-group/.stat-ogroup 与 .grp-* 无交集零影响；③**P1 pools.html 漏调 `gwGroupsBoot()`**（model/index/hfadmin 三页都有）——该页分组只认 localStorage，换设备/换浏览器首开模型池看不到服务端已配的分组，v2.16.8 #54 跨端共享在该页失效 → 对齐补上；④**P1 390px 整页横滚 31px（既有）**：`.grp-body` 未声明列模板，隐式轨道按内容最大尺寸定档，成员卡内 nowrap 文本（provider-head `min-width:220px` + .model-row 整行）的 min-content 393px 顶爆轨道——正是 v2.17.18 在 .grid 上修过、漏掉 .grp-body 的同款 bug → `minmax(0,1fr)`。取证实录：In-app Browser 的 FilePanel 仅 639 CSS px 宽、Ctrl+− 对该面板无效，无法复刻 1920 全屏，改用 Playwright 自建多视口取证；既有横滚经注入原值的 A/B 实测定性为「非本次引入」后才修。终态：1920/1440/768/390 × model/pools 八组全部 `scrollWidth == clientWidth` 零横滚、内容右缘 = 视口 − 右内边距、组头 box-shadow:none 而成员卡满投影、折叠/展开与拖拽落点与分组管理面板交互无回归；回归 **293/293** 全绿（与 v2.17.18 基线同数）。纯资产刷新即见，无需重启。

**`v2.17.18`** — **📱 hmg-admin 手机版全面体检（反馈：要适配手机版不能出现异常，详细检查视觉对齐）**：①**真异常×1**：stats 页整页横滚 28px（docSW 418/390）——grid `repeat(auto-fill, minmax(290px,1fr))` 的 `1fr` auto 最小值被卡内 nowrap 文本（stat-id 模型名）内在宽度顶爆轨道（实测轨道 406.083px > 容器 366px，卡 min-content 仅 239，纯引擎 track-sizing 行为）→ 教科书修法：`minmax(min(290px,100%),1fr)` + 移动端 `minmax(0,1fr)`；②**同款隐患普查加固×6**：stats .stat-group-body、hmg.css .grid(280)/.grid-tight(240)、keys .key-grid(230,auto-fit) 全部 `min(…,100%)` 化 + 各 ≤767 媒体查询 `minmax(0,1fr)` 化（320px 小屏同样免疫）；③**视觉对齐修复×1**：移动端抽屉 `top:0` 时品牌/版本块整块被 52px 顶栏盖住（v2.17.11 的版本完整显示在手机上失效）→ `top:52px` 从顶栏下缘起滑；④**程序化审计方法**：逐元素 right>vw 且祖先无 overflow 裁剪判定越界 + main 直接子元素左缘集合对齐度量 + 逐个隐藏子元素/width:min-content 二分定位轨道真凶（CSSOM 遍历须查 `.cssRules.length`——CSSStyleRule.cssRules 恒为对象，空列表也是 truthy）；⑤**取证陷阱实录**：IAB goto() 走启发式缓存不强制再验证，资产改后必须 reload()——一轮抽屉「打不开」假异常即源于此；stats 首拍发虚为 5s 轮询入场中帧。终态：六页 docSW==clientWidth 零横滚、内容边缘统一 12..368、弹窗 390 全屏 sheet 零溢出、抽屉滑入+品牌版本可见、stats 卡 366 整；回归 **292→293** 全绿（T40w 新增：六处 grid 加固/移动端模板/抽屉 top52；T40g 同步）；主题还原 dark。纯资产刷新即见。

**`v2.17.17`** — **🌈 hmg-admin 亮色身份光提强（反馈：白天的颜色太单调）**：v2.17.16 灰底白卡干净但素——白卡占视口七成，色只能从底缝/页头透出，而亮色 aura 还是 v2.17.14 按白画布定的 .075/.06/.07，落到灰画布上几不可感。①**亮色专属粉彩三元组**：六页 `--aura-*` 在亮色换「混白 40%」高亮度版（`:root[data-theme=light] body[data-page]` 六条覆盖，特异性压过亮色基座）——粉彩自身亮度高，alpha 提到 **.21/.165/.175**（约 2.8 倍）也不拉低画布亮度，直接复用暗色原色提强则会把 ink-subtle 跌破 4.5（数值否决实录）；WCAG 实测六页双峰值 ink-subtle ≥**4.55**；②斜光轴 .03→.09、底角辉光 .05→.14、html 角 .09/.07→.12/.10；③**网格靛调**：亮色双层网格从深灰线改 `rgba(94,106,210,.055/.028)`——灰底上的蓝图感；④**白卡微靛投影**：亮色 --shadow-card 第二层换 `rgba(94,106,210,.16)`，白卡在灰底上有一点点颜色身份（hover 同步微靛）。暗色零改动。回归 292/292 全绿（T40u/o/m/r 亮色锚点随提强同步，新增亮色粉彩表 2 锚点）；8660 实拍：model 顶部粉彩靛光带+靛网格+微靛投影、stats 青色身份分明——亮色六页「房间光」成立且白卡不脏；主题还原 dark。纯资产刷新即见。

**`v2.17.16`** — **🌅 hmg-admin 亮色主题美术重构：灰底白卡（反馈：白天的颜色不太好看；frontend-design + emil-design-eng 复核）**：①**诊断**：v2.17.15 的「卡片比页面深」在亮色读作脏不是浮——中灰卡 #eaebf1 压近白画布+灰阶文字=灰蒙蒙（实拍取证）；Linear DESIGN.md 明言官方不出亮色（line 546），亮色本就是自建设计，按轻色高级感铁律重构：**浮起 = 卡片比环境更亮**（iOS/macOS 分组范式）；②**定稿灰底白卡**：canvas 冷灰 `#eef0f4` + 卡片纯白 `#ffffff`（canvas↔s1 **1.141/ΔL*5.25**，≥1.13 深度线仍达标），s2 `#f2f4f8`/s3 `#eaedf3`/s4 `#e1e5ec` 为白卡上的填充梯、hairline 三档 `#dcdfe8/#c6ccd8/#adb5c3` 随白卡重排；③ink-subtle 回退 v2.17.14 值 `#5a5f6a`（白卡 6.40 对比更足更透亮）、tertiary `#666b79`（灰画布 4.67 达 AA——#616674 在新画布只有 4.35）、warn-ink 保持 `#8a5009`（白卡 tint 5.76；#9a5b0a 仅 4.39）；hue1/hue6 徽章对不动（白卡上 6.2/6.5 更跳）；§19 亮色 aura 峰值在新画布复核 4.8-5.2 全过；④语义 tint（ok/warn/err/acc）与 dark 全部零改动——本轮纯亮色 token 层。回归 292/292 全绿（T40k/r/v 亮色锚点随新梯同步，零新增断言）；8660 亮色四页实拍（model 白卡浮起+左色条/stats·logs 白卡+语义徽章跳出+文本恢复锐度；stats 首拍发虚为 5s 轮询入场中帧，稳定态复拍正常）；主题还原 dark。纯资产刷新即见。

**`v2.17.15`** — **🎨 hmg-admin 第 20 轮评审落地：中性阶降彩度 + 彩色只留计费轴 + 删除钮安静化（docs/美化问题清单-hmg-admin-model-20261015.md P0/P1/P2 全量核实后实施）**：①**P0 色板结构性修正**：暗色中性阶降彩度 HSL 37%→11-13%（canvas #161823→#131418、s1-s4/hairline 三档全移），靛蓝只留给 accent 与氛围层——「每一块表面都是笔触」终结；亮色纯白纸（S=0）→冷调纸面 #fafafc、s1 #eaebf1 全梯随移；**canvas↔s1 1.09→1.149/1.141（§16.4「卡片宜 ≥ΔL*6」八轮以来首次达标）**，暗 tertiary #828aa2（P2-8：4.41→4.66 达 AA）、亮 ink-subtle/tertiary/warn-ink 随更深表面同步提档（warn 顺修：#9a5b0a 在新 s2 上 3.89 破线→#8a5009 4.66）；②**P1-1 彩色只留计费轴**：一次性→`.badge-one` 紫（hue1 对 AA 4.5+）、5h限额→`.badge-idle` 琥珀（hue6 对 4.7+，亮 hue6 #a8721c→#7a500c、暗 hue1 #b08cff→#bb9bff 随徽章上脸调 AA），embedding/rerank/多模态能力轴回中性、JSON/智能超时降 `.tag`（描边弱化）、思考映射 `.badge-line` 无填充描边式——单行彩色徽章 6→恒 ≤1；③**P1-2 删除钮安静化**：delProvider/delModel 两处 `btn-danger`→`btn-ghost danger-hover`（§17.2 已有规范贯彻到底，本页 btn-danger 清零，与池成员 ✕ 同款）；④**P1-3 身份色承担分组**：供应商卡 3px 左侧色条（`pc-hue-N` 专属前缀——**踩坑：挂 `hue-N` 会撞头像全局 tint 类把整卡染色**，已记实录）+ 头像 20→26px，24 卡按色分区扫读；⑤P2：ep-id 弱化（13.5px/600→12px/tertiary）、拖拽把手 .55→.3、`.m-tags` 包徽章（桌面 display:contents 零变化，≤1199 独立行横向滚治行高失控）、a/.link 改 acc-ink 文字安全色（P2-7：accent 作文字 3.45 不达 AA）；⑥验收五项实测：暗靛蓝占比 **96.8→16.8%**（≤88 ✓，感知阈值 S<0.30 全页口径；严格阈值 S<0.12 下新旧表面均计入靛蓝、数字不动 96.9%，口径已注）、暗中性阶彩度 11-13%（≤14 ✓）、canvas↔s1 ✓、单行彩徽 ≤1 ✓；**亮色无彩色 99.2% 未达 ≤92% 如实记录**——清单 §6 与 P1-1 自相矛盾（去色噪必然缩彩色面积），取舍已按 P1-1 执行交用户裁决；氛围层保留（P2-1）：实拍可见、中性化后对比更强，评审 jpg 压缩疑洗掉低对比渐变；P2-2（模型 sparkline/余量条）与 P2-5（sticky 搜索/索引）涉数据管线与 UX 功能延期后续轮。回归 **291→292** 全绿（T40v 新增 40 锚点；T40k/r/s 色值锚点随新梯同步；踩坑修复后复跑全绿）；8660 双主题首 paint 主题法截图（绕 §14 冻结伪影：切主题后卡片背景冻结，先写 localStorage 再 reload）+ DOM 断言全过。纯资产刷新即见。

**`v2.17.14`** — **🌌 hmg-admin 背景每页身份色（反馈：背景还是太单一；每页一组 --aura-* 三元组，切页即换「房间光」）**：①**六页六色**：`body[data-page]` 覆盖 `--aura-a/b/c` 裸三元组（model 品牌靛蓝 `130 143 255` / pools 紫 `167 139 250` / stats 青 `64 205 255` / logs 天蓝 `96 165 250` / keys 紫红 `192 132 252` / plugins 青绿 `41 195 176`，整页 0.92×V 微降保 AA），渐变侧 `rgb(var(--aura-x) / α)` 消费；②**四层吃变量**：舷光（加宽提亮 `1240px×400px at 50% -12%`）、20%/86% 两盏顶辉光、body::after 极光团（620×460→**780×560**）、新增 **115° 斜向光轴**（transparent 42%→中心 aura-a /.05→transparent 61%，给平底加方向性）——html::before 底角辉光不吃 body 变量（自定义属性不向上继承），保持品牌靛青静态；③**alpha 全局统一不随页变**：暗 `.15/.12/.10`、亮 `.075/.06/.07`，色相全亮保身份感——WCAG 数值实测两主题×六页「舷光核心」「左上辉光簇」两处峰值叠点 ink-subtle 全部 ≥4.5:1（ink-tertiary 使用面约束本就不进 aura 顶带，不另设阈值；等 alpha 方案对青色系破 4.5、加深三元组成泥色，两案皆否决）；④**平底两件套提亮**：双层网格暗 .024/.013→**.05/.028**、亮 .022/.012→**.032/.016**，暗角 .38→**.46**（明暗值域拉开立体感）；⑤回归 **290→291** 全绿（T40u：六页 aura 表/四层变量锚点/双主题 alpha/网格/暗角/底角/旧值清零；T40t 舷光锚点移交 T40u，T40m/T40o 色值锚点随 v2.17.14 现状同步并注明）；8660 实证：六页变量逐页正确、logs 页四层解析取证（96,165,250/.15 等）、暗色三页截图（model 靛/stats 青/keys 紫红）身份可辨、亮色 stats 粉彩青可辨文本清晰、主题还原 dark。纯资产改动**刷新即见**。

**`v2.17.13`** — **🌌 hmg-admin 舷光背景 + 「系统在运转」三件套 + 命中区扩大（反馈：背景单一/要科技感；frontend-design + emil-design-eng 定调：大胆只花一处、动效必须有目的且 <300ms）**：①**舷光**（全场唯一大胆笔触）：暗色 body::before 顶部新增 `1100px×360px at 50% -12%` accent 光带（rgba(130,143,255,.12)，与既有 22%/84% 两盏顶辉光叠成「仪表舱顶光」，随 ambientDrift 38s 漂移+透明度 .72↔1 呼吸），亮色同位粉彩 .07（晨空冷带）；舷光在 vignette 透明区内，文本对比度不受影响；②**轮询数值变化闪现**：hmg.js 新增 `hmgLiveTxt`（文本变了才触发一次 `.live-chg` tint 闪现 0.8s ease-out——color+text-shadow 起始态过渡回原样，不变零动画、首渲不闪），stats 页 `.stat-used`/`.stat-pct` 两个活数接线（5s 轮询的数字跳动首次有视觉反馈，§16.7「没有一个动效表达系统在运转」的落地）；③**logs 吞吐 sparkline**：页头 120×26 内联 SVG 迷你图，48 次采样（≈4 分钟）环形缓冲，每次轮询 push 当前 decisions 数（空窗如实落 0），accent 折线+渐变面积+头部呼吸点；SVG 颜色走 style 属性（表现属性不解析 CSS 变量的坑），<768 隐藏；④**命中区扩大**（易操作）：i-tip ⓘ（15→25px）、pq-refresh ⟳（22×18→30×26）、grp-x 🗑（22→30px）——视觉尺寸不变、透明 ::before 外扩；⑤自检确认对齐已稳不动（页头 46/46 居中、stat 数字行 147/308/469 齐平）。回归 **289→290** 全绿（T40t：舷光双主题锚点/liveFlash/sparkline 环形缓冲/命中区外扩）；顺修 v2.17.12 插条目时「### 最新」标题重复一次的笔误；node --check 过；8660 双主题×双视口复验。纯资产改动**刷新即见**。

**`v2.17.12`** — **🎨 hmg-admin 双主题色板定稿 + 徽章语义/布局分组 + emoji 换线性 SVG（按他线工作单《美化工作单-hmg-admin.md》§16 评审清单逐条核实实施，回填 §17）**：①**亮色塌陷修复**（评审实测 surface-1/2 仅 1.01:1 同色）：`--surface-2` #f6f7f7→#eceef1、surface-3/4→#e4e7ec/#dcdfe6（全梯 ΔL*≈2.5-2.8 均匀可辨，卡片/侧栏选中/徽章/次按钮不再糊成一片）、hairline 三档 #d5d9e0/#bfc5cf/#aab1bf（顺修 tertiary 反超 strong 的断梯）、卡片阴影 .05/.07→.06/.10；②**双主题 ink 提色 + 使用面约束**：暗 ink-subtle→#9ea4bb（surface-4 上 4.6:1 达 AA）、ink-tertiary→#7e859c，亮 ink-subtle→#5a5f6a、ink-tertiary→#6b707c（surface-1 上 4.6:1）；「ink-tertiary 只许 canvas/surface-1、抬升表面改用 ink-subtle」写入 token 注释，违规点 logs 页 `.rsn-neutral` 改 ink-subtle（AA 与第三级视觉层级不可兼得的评审结论落地）；③**徽章语义**（badge-warn 只留真告警）：模型一次性/5h限额/仅闲时/余额返还制/付费 与池 子池/专属池 全部素徽章（说明入 title），顺修评审漏记的池成员「付费」badge-err 红色滥用；④**池卡布局重排**：7 个异质控件一字长龙→`.pool-head-ctls` 两行分组（上排开关+阈值同质组/下排动作组虚线分隔右对齐，<768 左对齐铺满），note-box 从整行加粗底色条降为左 2px 线静默注（完整解释保留在开关 title），「Switch」标签补 title，供应商卡内「＋添加模型」与「一键单模型」btn-primary→btn-secondary（页头主 CTA 唯一实心紫）；⑤**emoji→线性 SVG**：58 处代理对普查后的按钮/徽章/状态行可见面全清（⚡🔍🗑🛠⚙📥🔄🔒🔓→HMG_ICONS 新 9 字形 bolt/shield/trash/refresh/download/wrench/lock/unlock/search；🛡️代理/🧠状态行/💡/🔀/🌐/💾 去装饰），弹窗标题按既定先例保留作个性、注释不动；静态 HTML 宿主走新增 `hmgInjectIcons` data-icon 注入器保持单源，`.btn svg`/`.badge svg` 尺寸规则配套；⑥**汉堡修复**（评审 §16.6）：`hmg-burger` 误带 `theme-btn` 类致每切一次主题菜单图标被换成太阳/月亮，去掉类（一行修）；⑦其余：原生 select `appearance:none`+data-URI chevron 自绘（跨平台外观收编设计系统）、池成员移除 ✕ 加 `.danger-hover` 悬停显险、供应商 URL `.sub-clip` 单行截断+title 保全量、模型行 ID/名称单行截断（文字先收缩修徽章被挤换行参差）、身份色哈希种子掺长度（同前缀名不系统性落邻相，8 相位鸽笼碰撞如实注释）。回归 **287→289** 全绿（T40r 色板定稿锚点全套 + T40s 徽章语义/池卡分组/图标注入器/汉堡/哈希种子）；node --check 过；8660 双主题×双视口 DOM 断言（亮 s1/s2=#f5f6f6/#eceef1 冻结动画 finish 后实锤、模型行 nowrap+ellipsis、池卡 column/flex-end/dashed、select appearance:none、badge-warn 页面计数 0）+ 四象限截图 `gui-test-screenshots/v2_17_12_*.png`；评审 §16.2 鉴权修复经 `git log -S` 核实已随 v2.17.4 入库本轮零改动。纯资产改动**刷新即见**。

**`v2.17.11`** — **🔖 hmg-admin 品牌版本完整显示 + 余额查询就近化（反馈：①左上角版本没显示完全 ②供应商余额查询按钮太远布局不好）**：①**品牌行堆叠**：版本串含提交哈希（如 `v2.17.10 · 32e6f2c`，~17 字符）在 logo+网关名同行放不下，`flex:1+右对齐省略号`恒截断——改为 `.brand-txt` 列容器（名称上/版本下，logo 垂直居中跨两行），任何侧栏宽度完整可见；`.ver` 保留 ellipsis 仅作极端兜底；两处断点选择器同步（≤1199 图标条隐藏 `.brand-txt`、<767 抽屉恢复 `display:flex`）。②**余额查询就近化**：查明全面板**不存在独立查询按钮**（查询只随编辑弹窗「保存」触发——查询动作与保存耦合且离显示位最远）；供应商卡片头部余量徽章（`pq-inline`）后新增 **⟳ 立即查询**（`.pq-refresh` 22×18px 幽灵小钮，仅已绑定供应商渲染；`pqRefreshNow` PUT **原绑定字段** `platform/enabled` 原样回传——服务端「显式提交才覆盖」语义下其余字段保持原值，收到 PUT 即 `refresh_binding`+落盘结果文件；完成后走 `pqSyncBindings` 唯一通道重绘徽章，toast 反馈 ok/unset/失败三态；spin 态 `hmgSpin` 复用+disabled 防连点；零后端改动）。18px 高 <22.5px 标题行盒不膨胀（v2.17.5 行内三件套）。回归 **286→287** 全绿（T40q：brand 堆叠结构/旧单行标记移除/断点同步/pqRefreshNow/⟳ 注入/PUT 原字段锚点）；node --check 过；8660 实证：版本 `v2.17.2 · 1851e44` 完整可见（stacked+无截断 DOM 断言），⟳ 与余量徽章同容器就近在位，真点击 toast「余额已刷新」+spin 清理；截图 gui-test-screenshots/v2_17_11_brand_quota_refresh.png。纯资产改动**刷新即见**。

**`v2.17.10`** — **↗️ hmg-admin 弹窗动作行右下角（反馈：供应商保存/取消应放卡片右下角——对话框主操作右下是通则）**：`.modal-actions` 样式从 model.html 页面级**上收共享 hmg.css** 并加 `justify-content: flex-end`——供应商弹窗与密钥弹窗的裸 取消/保存（创建）按钮全部靠右下；「左侧辅助动作 + `.right` 右组」范式不受影响（模型弹窗的 测试连接 仍在左，`.right` 的 `margin-left:auto` 先吃掉全部富余空间，flex-end 对其无感）；顺修 keys 页 `.modal-actions` 类**从未定义样式**的漂移（此前纯靠浏览器默认流，无 8px 间距无 flex 对齐）。回归 **285→286** 全绿（T40p：共享规则整行锚点 + 页面级复制移除 + keys 接线）；8660 实证：打开供应商编辑弹窗断言 `justify-content: flex-end`、保存按钮右缘与容器右缘齐平（rightInset 0）、取消在保存左侧，截图 gui-test-screenshots/v2_17_10_provider_modal_footer.png，弹窗已关闭还原页面。纯资产改动**刷新即见**。

**`v2.17.9`** — **🎨 hmg-admin 亮色主题同构淡彩版（反馈：白天显得很单调——v2.17.7 把噪点/暗角/极光都给了暗色，亮色只剩三团 .04-.06 的淡晕，白底近乎素纸）**：亮色配方对齐暗色的**层结构**、全族降 alpha 防脏——①body::before 从 4 层扩到 **8 层**：三向辉光（靛蓝 `.06→.09` 顶部 + **新增青 `.06`** 右上 + 紫 `.04→.055` 右下）+ **纸张噪点上白底**（与暗色共用同一 feTurbulence data URI，抽 `--noise-url` 变量双主题引用）+ 44px 细网格 + **264px 大网格**（暗线 `.012`，蓝图感）；暗角白底退场（边缘压暗读作屏幕脏，刻意不给）。②**粉彩极光启用**：`body::after` 从 `display:none` 改为同位同动画（auroraDrift 26s）仅换色——靛 `.14`/紫 `.09` 白底甜点感（对比暗色 `.16/.10` 全族降档）。③html::before 第二漂移层淡晕提亮：紫 `.05→.07`/青 `.04→.055`，38s 漂移在白天也可见。回归 **284→285** 全绿（T40m 一锚点随配方升级 `.05→.055` 同步 + 新增 T40o：亮色极光规则/三向辉光/噪声变量共用/大网格锚点）；8660 DOM 断言：亮色 body::before 八层 backgroundSize（噪点/三辉光/44+264 双网格）+ 无暗角残留 + 极光 `display:block auroraDrift@26s` 粉彩双色在位 + html::before 淡晕无暗色值 + 对比 **17.9:1** 不变（辉光只压底比率只升）；截图 gui-test-screenshots/v2_17_9_desktop_light.png。纯资产改动**刷新即见**。

**`v2.17.8`** — **🪄 hmg-admin 拖拽丝滑化（反馈：拖模型/供应商/池改顺序不丝滑；四个拖拽面统一治理，纯前端刷新即见）**：病灶四件——①换位瞬跳（dragover 中点判定 `insertBefore` 直改 DOM，零过渡且易振荡）；②**双重变淡拖影**（dragstart 同步设 `opacity:.4`，Chromium 拖影快照在事件处理后拍→拖影本身半透明+原地也淡，糊成一团）；③落点后**全量重渲闪放**（供应商 `loadModels()`/池 `loadPools()` 无条件重拉，入场瀑布全体重播）；④模型行/池条目/池卡 dragstart **无 setData，Firefox 拖不动**。修法：**hmg.js 共享 FLIP 助手 `hmgFlipMove(container, match, insertFn)`**（换位前抓可见兄弟 top→换位→dy 反向 `translateY` 内联→下一帧 `.18s var(--ease-out)` 过渡归零→transitionend 清内联；`.dragging` 标记排除被拖元素、`prefers-reduced-motion` 直接换位不动画）——瞬跳变 180ms 滑移；dragover **rAF 合帧**（`pendingEv` 最新意图胜出，`preventDefault`/`dropEffect="move"` 仍同步）；变淡改**异步**（拖影快照后 rAF 内加 `.dragging` 在位守卫——极速落下不残淡）；成功路径**不再重渲**（DOM 已是所存顺序，仅 api 失败才 `loadModels()/loadPools()` 回拉）；四处补 `setData("text/plain", id)`。回归 **283→284** 全绿（T40n：flipMove 助手+导出/四拖拽面接线/失败才重渲/setData/.dragging 锚点）；hmg.js+四内联脚本 node --check 过；8660 合成 DragEvent 实证：FLIP 单元断言（真实换位后 b/c 兄弟内联 `translateY(45px/45.67px)` 反向位移、换回零残留）、四链路 `.dragging` 生命周期 + setData + PUT 净零（顺序未变时落库为无害 no-op）、**供应商/池拖完 spy 实测 `loadModels/loadPools` 0 调用**（闪烁根源已除）；IAB 环境两事实记录：rAF 被遮挡节流冻结（rAF 门控换位无法端到端，中间帧数学由单元断言覆盖；真实浏览器 rAF 正常）、页内 `await`/定时器被拉长（测试一律 node 侧编排+页内纯同步分段）。

**`v2.17.7`** — **🌌 hmg-admin 背景丰富化（反馈：背景太单一——修前暗色仅顶部两团靛蓝系辉光+一团极光，右侧与下 2/3 视口纯平底色且全体同色相）**：①**第二漂移层**（`html::before` fixed 负 z 层，inset -22% 防漂移露边）：紫 `rgba(178,118,235,.09)` 压右下 + 青 `rgba(64,205,255,.07)` 压左下——两个新色相打破单一靛蓝族；**ambientDrift 38s** 与原 auroraDrift 26s 互质不锁步，与顶部极光分踞上/下覆盖全视口；blur 38px 静态滤镜、transform/opacity 合成器动画。②**静态质感层**（body::before 从 4 层扩到 8 层，抗色带/加纵深）：**胶片噪点**（SVG feTurbulence 去饱和 data URI，180px 无缝平铺 .05 透明度）+ **暗角**（radial transparent 55%→rgba(4,5,9,.38) 边缘压暗）+ **264px 大网格**（蓝图感，与 44px 细网格共存）。③**亮色主题同步**：html::before 极淡双色晕（紫 .05/青 .04）+ body 右下加紫晕；噪点/暗角白底退场；顺修**永不匹配选择器** `:root[data-theme="light"] html::before`（:root 即 html 自身不能是自己的后代，改为 `:root[data-theme="light"]::before`——该 bug 让亮色第二层一直回落暗色色值）。④手机端大模糊层 blur 降 26px 省 GPU；reduced-motion 全局冻结自动接管。回归 **282→283** 全绿（新增 T40m：第二漂移层紫青双色/feTurbulence 噪点/暗角/264 大网格/亮色淡晕/手机降 blur 锚点）；DOM 断言：暗色 body::before 8 层 backgroundSize（暗角/噪点/双辉光/44+264 双网格）+ ambientDrift@38s blur(38px) fixed z-1 + auroraDrift@26s 同屏并存、对比 14.8:1 不变（暗角只压暗背景比率只升）、亮色 html::before tint 在位且无暗色色值、双主题往返正确、卡片级联 #f5f6f6 不受影响、手机 390px blur(26px)；截图 gui-test-screenshots/v2_17_7_*（亮色截图一次 IAB 半合成残影，DOM 断言证伪后重拍）。纯资产改动**刷新即见**。

**`v2.17.6`** — **🌌 hmg-admin 暗色三档提亮降对比 + 科技氛围层（反馈：暗黑太深邃对比太高不舒服/缺动态/无科技特效感）**：①**色板第三档**：canvas `#101116→#161823`、ink `#f4f5f9→#e9ebf3`（实测对比 **17.3:1→14.8:1**，Linear 暗色同档），surface 四梯/hairline 三级/ink-muted 同族上移，side-bg `rgba(19,21,30,.72)` 随族；T40k 画布锚点同步 `#161823`。②**科技氛围层**（`body::before/::after` 负 z 固定层，画布色移到 html、body 透明）：44px 暗网格 + 顶部双极光 radial（lavender .11 / blue .07）+ **auroraDrift 26s 极光缓漂**（translate3d/scale/rotate 纯 transform 合成器动画，blur 34px 静态滤镜）；亮色主题极光退场、网格换暗线极淡不脏版面。③**恒动细节**：品牌 Logo 渐变 **brandFlow** 9s 缓流、进度条 **fillFlow** 3.2s 数据流光（`.stat-fill/.bar>i/.q-track i` 的 ::after 42% 白光条 translateX 循环——零宽条自然无光）、nav 选中项 **accent 光条+柔光**、主按钮 hover **辉光扫过一次**（sheenSweep，feedback-tied 非恒动）。全部动画 transform/opacity（合成器线程，主线程零占用），reduced-motion 全局冻结规则自动接管。回归 **282/282** 全绿（T40k 增补 auroraDrift/fillFlow/brandFlow/body::before 锚点）；DOM 断言：对比 14.8:1、四动画 computed 在位、亮色 aurora `display:none`；截图 gui-test-screenshots/v2_17_6_*。纯资产改动**刷新即见**。
**`v2.17.5`** — **📐 hmg-admin 头像对齐修正（v2.17.4 回归：标题行头像中心比名字文字低 7.6px 且行盒被撑到 28.3px）**：根因 `vertical-align:-6px` 过量 + 20px 行内片膨胀行盒；修法 `.card-title .hmg-av` 改 `vertical-align:middle` + 纵向负 margin -3px（行盒贡献 14px < 22.5 行高）+ `relative top:-2px` 光学居中——实测 model/keys 双页偏差 **0.25px**、行盒回落 23.3px；stats 24px 大头像 `margin-top:-1px` 校正首行光心（修前 -2.87px）；删 keys 私有规则统一共享。回归 282/282 全绿；刷新即见。
**`v2.17.4`** — **🎨 hmg-admin 体验丰富化（三次反馈四条：换行打乱布局/颜色单一乏味/缺动效与按钮/信息过载排布差；frontend-design + emil-design-eng 按单实施，纯资产刷新即见）**：①**身份色系统**（杀单调的主招，Linear label 色系）：双主题 8 相位 token（`--hue0..7`，暗色 tint 底+提亮 ink、亮色加深 ink），`hmgHue()` 名字哈希恒定相位——**实体头像** `hmgAvatar()` monogram 方片上遍统计/密钥/模型/模型池四页卡 title（同供应商族天然同色分组，实测 8 相位全启用）、**池名小圆点** `hmgAvatarDot()` 上遍调用记录行与密钥授权池 chip。②**换行治理**：`stat-id/stat-name` 单行截断省略号 + `title` 保全量（增量更新路径同步 title），实测 **0 垂直折行、18 个多卡行数字基线全对齐**（修前 `HuoSan-Ark/DeepSeek-V4-Flash正式版` 等长名折 2-3 行把数字/徽章顶得上下错位）；keys 授权池顿号文本改 **pool-chip pill**（换行只发生在 chip 之间不再拦腰断名）。③**按钮实体化**（「缺少按钮」实锤=ghost 透明底读作裸文本）：`.btn-ghost` 全局 wash 底+发丝边+hover 提亮——复制/编辑/用量历史/轮换日志/禁用/刷新/分组查看/节省统计全部一眼可点；复制按钮**文本态回声**（「已复制 ✓」1.2s 还原）。④**动效补强**（全部 reduced-motion 自动冻结、目的=状态指示）：骨架屏 `.empty.loading` 微光扫过（stats/logs/keys 首载）、5s 轮询页页头 **live-dot 绿点慢脉冲**、`hmgBusy()` 按钮忙态旋转圈（兼容函数/Promise 传入，logs 刷新接线）、`stat-pct` 占比按 warn/err 着色（增量更新同步 className）。回归 **282/282** 全绿（T40g/h/i/k 增补身份色/加载态/忙态锚点）；六页内联 JS + hmg.js node --check 过；DOM 断言：53 统计头像/214 池 chip/200 日志池点/8 相位全用/亮色全套映射正确；截图 gui-test-screenshots/v2_17_4_*。
**`v2.17.3`** — **📐 hmg-admin 四线体检（对齐/功能/美观/性能）：分组视图等高补漏**：继续体检发现并修复一处 v2.17.2 漏网——等高修复只改了平铺 `.stat-grid`，**漏了分组视图的实际网格容器 `.stat-group-body`**（`align-items:start` 残留；分组收纳开启时统计页的真实渲染路径），实测 17 个多卡行中 **8 行参差、高差最大 38px**——正是「卡片大小不一致」在分组视图的残留；去掉 start 改默认 stretch（与 `.stat-grid` 同款注释），修复后实测 **0 参差 + 同行进度条底缘对齐**（barAligned DOM 断言）；T40g 锚点加固防回退（分组网格无 start 的整行锚点），回归 **282/282** 全绿。其余五线全过：六页真实数据渲染零横向溢出（22 供应商卡/21 池卡/53 统计卡/100 调用卡/33 密钥卡/4 插件卡）、新增供应商弹窗开合生命周期（入场 .on/12 字段/新增态余量区正确隐藏/出场零残留）、**双主题级联正确性**（页内时钟实测 theming 类 200ms 生命周期 + 双向往返 token 翻转，light 侧栏 rgba(255,255,255,.72)/卡片 #f5f6f6 断言）、移动端抽屉三态（遮罩 opacity/pointer-events 与位移联动）、手机弹窗 sheet 化（width/height 100% + 直角 + 安全区底垫）、性能（DCL 80ms、共享资产 gzip 后 10K/17K、@keyframes 纯 opacity/transform、六页零裸 setInterval 全走 pollMgr visibilitychange 感知、进度条 width 过渡为刻意设计）；跨页工具栏 27px=`.btn-sm` 刻意紧凑变体非漂移。**IAB 环境鉴别法新增**：遮挡节流冻结动画时钟 → 过渡属性计算样式冻结在起始值（76 个 hmgIn 钉在 t=0），`document.getAnimations().forEach(a=>a.finish())` 强制终态后级联全部正确——三次疑似「主题切换不动」均环境问题非产品 bug。纯 stats.html 一行 CSS + 测试锚点，**刷新即见无需重启**（截图 gui-test-screenshots/v2_17_3_*）。
**`v2.17.2`** — **✨ hmg-admin 高端感返工（验收反馈：黑太暗伤眼/缺鼠标互动/卡片参差/廉价感；本轮引入 apple-design 材质体系 + superhuman 排版原则；纯 hmg-admin 资产改动刷新即见）**：①**色板升亮（护眼核心）**：暗色画布从营销站「海报黑」`#010102` 升到**产品面石墨** `#101116`（靛蓝倾向，与 lavender 同族色相），表面阶梯 `#17181f/#1d1f28/#242734/#2b2e3d`、hairline `#2a2d3a/#3a3e51`、ink 四级全部随族提亮——长时间高密度使用不再刺眼；亮色 ink 从纯黑改 `#16171b`（superhuman「never pure black」暖黑原则）；T40k 画布锚点同步。②**材质层级（apple-design：材料即层级）**：侧栏/移动端顶栏改**半透明模糊材质**（`--side-bg` 72% + `backdrop-filter: blur(20px) saturate(1.3)`），主区改 padding-left 布局让**内容从材质下滑过**；`prefers-reduced-transparency` 降级为实心；卡片落地影升级两层（贴地+环境），亮暗各自定标。③**鼠标互动补齐（"死"板 → 活）**：小卡片 hover **浮起三件套**（translateY(-2px) + 阴影绽放 `--shadow-hover` + 描边提亮），大容器（供应商/log 卡）只描边不位移（整卡大片位移晃眼）；全部卡片 hover 顶部**辉光发丝线**（`--sheen` accent 渐变 1px，opacity 过渡）——顶级工具的标志性反馈；主按钮 hover 上浮+accent 辉光投影、次按钮上浮+落地影，按下仍 scale(.97)；nav 选中项图标点 lavender（选中语义，品牌许可范围）。④**卡片一致性**：stats 网格去掉 `align-items:start` 改默认 stretch——**同行卡片等高**（实测首行 147/147/147），统计卡改 flex 纵排、进度条+元信息 `margin-top:auto` 贴底对齐；卡片内边距统一 16px。⑤**去 emoji（廉价感主因）**：侧栏🔑/☀️/🌙、顶栏☰、分组📂、工具栏 ⚙️/👤/📊/🪴/💰/🔌 全部换**统一线性 SVG 图标**（hmg.js 新增 sun/moon/menu/folder/cog + `.side-ic/.ic-btn` 尺寸类），弹窗标题保留 emoji 作个性签名。⑥排版微调（apple：字距随字号）：12px 级小字加 +.01em 正字距（.badge/.hint/.fld 等）。回归 **282/282** 全绿（T40k 画布锚点更新 `#101116`，其余零断言变化）；hmg.js+六页内联 JS node --check 过；8660 实测：暗色桌面（石墨色板+卡片浮起层）、stats 同行等高（DOM 断言 [147,147,147]）、hover 辉光发丝线（::before opacity=1 悬停态断言）、侧栏材质（rgba .72 + blur(20px) 计算样式断言）、亮色桌面、手机抽屉全过；IAB 截图管线又现 4 次残影/半合成帧，均 DOM 断言证伪后重拍（证据 gui-test-screenshots/v2_17_2_*）。生效说明：纯 hmg-admin 资产改动，**刷新即见，无需重启网关**。
**`v2.17.1`** — **🎨 hmg-admin 视觉质感返工（v2.17.0 只搬了 Linear 色板、没搬质感与动效，本轮按三份设计技能把执行层补齐；纯 hmg-admin 资产改动刷新即见）**：①**Elevation 补齐品牌两段**：卡片顶边微高光（`inset 0 1px 0` 白 3.5%——品牌"pixel rendered"质感）+ 极轻落地影；hover 抬升 = 表面阶梯 surface-1→2 + hairline→strong（品牌 Elevation 2 语义，**不位移不加投影**——深色上阴影抬卡是反模式）；浮层（弹窗/菜单/toast/抽屉）专用两层 shadow-pop + 遮罩 backdrop-blur 7px；亮色主题 edge/影/tint 全套独立定标。②**动效按 emil-design-eng 规范落地**：动效 token 三曲线（强 ease-out `cubic-bezier(.23,1,.32,1)`、ease-io、iOS 抽屉曲线 `(.32,.72,0,1)`）× 三档时长；按钮/图标钮/关闭钮**按压 scale(.97)**（160ms）；弹窗改 `.on/.off` transition 生命周期（入场 300ms 缩放 .965+淡入、**出场 140ms 恒快于入场**、`__closing` 防重入，Esc/遮罩/✕ 同路）；下拉菜单 **transform-origin 跟随触发源方向**缩放展开（弹窗豁免恒居中）；toast 改 transition 可中断；抽屉 iOS 曲线 320ms 滑入 + 遮罩 opacity/visibility 淡入淡出替代 display 硬切；**进度条/余量条宽度 450ms 过渡**——5s 轮询数值变化变成平滑动画；唯一编排时刻=入场瀑布（主区浮起 + `.stagger`/统计卡/插件卡 40ms 级差 360ms backwards，stats/logs 增量创建仅新卡播放）；`prefers-reduced-motion` 降级为「少而柔」：保留透明度淡入、去掉位移与宽度过渡。③**彩色标签系统**（品牌产品面允许彩色标签，营销面才禁）：语义 tint 底 + 提亮文字双主题四组 token（ok/warn/err/acc 的 ink+tint，暗色提亮保对比）；免费=绿 tint、**付费=琥珀 tint（付费是信息不是错误，从 badge-err 降级）**、思考映射=强调色 tint、协议/模态/每日=中性 pill（品牌 status-badge 规格）。④**去噪与微交互**：「删除」btn-danger 改 err-tint 底+hover 才描 err-line（全场最吵红框变安静，confirm 弹窗破坏键仍醒目）；模型行操作组（测速/编辑/删除）**hover/focus-within 才滑入显现**（触屏恒显）——密集列表去噪；拖拽把手呼吸（opacity .55→1）；行 hover wash + 圆角盒 inset 分隔线；复选卡片/池勾选 `:has(:checked)` 强调态；焦点环 2px accent @50%；`:root color-scheme` 双主题（原生控件/滚动条跟随）；`::selection`/滚动条 thumb/空态虚线框/ⓘ气泡 blur+淡入淡出（防竞态延迟摘除）。⑤**对齐修补**：品牌行名称 nowrap+版本号右对齐省略（实测「HMG 网关」被挤换行）；非分组模式卡片纵向节奏 12px；seg 改 pill 容器（pricing-tab 选中=表面抬升语义）；六页页面级配合（model 供应商头 hairline 分隔、stats 卡 edge+入场+fill 渐变、plugins 卡入场级差、logs 原因码 chip 换 token tint、pools 条目 hover、keys 勾选态+悬浮参考卡质感）。⑥hmg.js 三生命周期重写（hmgOpenModal/hmgMenu/hmgToast）。回归 **282/282** 全绿（纯资产零断言变化）；hmg.js+六页内联 JS node --check 过；8660 四象限实测（暗/亮×桌面/手机+抽屉+双主题弹窗，截图存 gui-test-screenshots/v2_17_1_*），实测修复品牌行换行一处；IAB 捕获管线三次残影帧均以 DOM 断言复核为准。生效说明：纯 hmg-admin 资产改动，**刷新即见，无需重启网关**。
**`v2.17.0`** — **🧭 hmg-admin 第三面板：六页多页面真路由 + 单 DOM 双主题 + 手机全适配（全新界面，零后端行为改动，admin/hfadmin 零触碰）**：①**架构三决策**（与 /admin /hfadmin 并存的第三面板，`/hmg-admin` → 307 落地 `/hmg-admin/model`）：每菜单独立 URL（`model|pools|stats|logs|keys|plugins` 六页白名单路由，白名单外 404）——告别单 HTML 页签切换，可深链/收藏/刷新，每页只带本页业务 JS（内联）；**共享资产单源**（`static/hmg-admin/assets/hmg.css` 设计 token+组件+三断点响应式、`hmg.js` 鉴权/api/壳注入/弹窗/菜单/toast/轮询管理/分组收纳套件/成本预估弹窗/节省统计——六页零复制，从架构上终结第三份复制粘贴漂移；侧栏壳由 `hmgRenderShell()` 按 `body[data-page]` 单源注入）；**单 DOM 双主题**（`html[data-theme=light|dark]` + CSS 变量仅换色——同一界面同一布局，区别于 admin/hfadmin 两套文件两种界面；默认跟随 `prefers-color-scheme`，手动切换记忆 `localStorage.hmg_theme`，`<head>` 内联引导脚本防闪白）。②**视觉**（任务工作单 `docs/美化工作单-hmg-admin.md`）：锚点品牌 Linear（awesome-design-md 档案 token 原样摘录——近黑画布 `#010102`、四级表面阶梯 + hairline 边框承载层级拒绝阴影、lavender `#5e6ad2` 单点缀色只用于选中态/焦点环/主按钮绝不装饰滥用；亮色 = 品牌唯一公开的 inverse 系列 + 派生阶梯，工作单如实标注派生项）；中文系统字体硬约束、数字 `tabular-nums`；动效克制三层 CSS-only 尊重 `prefers-reduced-motion`；**响应式三断点**：≥1200 侧栏常驻 / 768–1199 折叠图标条 / <768 顶部紧凑栏（汉堡+页名+主题）+280px 抽屉（遮罩滑入）、卡片网格 auto-fill 单列、**弹窗手机端全屏 sheet**、触控目标 ≥40px。③**功能对齐 hfadmin 全量**：模型管理页（供应商/模型卡片双拖拽排序、模型编辑弹窗五分区含高峰期/安全阀实时预览/搜索 AI 总结、分组收纳全套、🔋余量监控编辑区 + 卡片头部内联余量、单模型测速、思考参数探测填入、gift 人工校准、预估费用齿轮）；模型池页（池卡片/嵌套子池条目 i 气泡/池卡+池内双拖拽/单模型锁定/兜底目标/重命名/自动择优·负载均衡·Switch 三开关随插件门控/延迟阈值 hint）；用量统计页（四维度分段、安全阀红线+红区、gift 余额口径 ⓘ 气泡、供应商/池嵌套外层系统组、付费视图、📊分组查看、🪴节省统计、5s 轮询 pollMgr 统一管理+加载序号守卫）；调用记录页（原因码全集含余量熔断、池筛选、按 id 增量刷新）；API 密钥页（CRUD/创建仅显一次/轮换审计/用量历史 SVG 单调三次插值曲线+悬浮参考线/专属池/管理员伪 Key#0）；插件中心（启停/扫描/config_schema 动态表单含 pools 勾选·slider/Headroom 设置/自定义插件指南）。鉴权复用 `localStorage.gw_key`（与旧面板互通），业务 API 全走既有 `/admin/*` 零改动。④**后端纯只读新增**：`app/admin/hmg_routes.py`（页面白名单路由 + assets 服务：扩展名白名单防目录遍历、mtime 缓存 + ETag/304 + `__GATEWAY_VERSION__` 替换同 `hfadmin_page` 模式）+ `paths.py` 一常量 + `main.py` `include_router` 一行——零行为改动；admin/hfadmin 两文件 git diff 为空（套件 T40l 锚点复核双面板零污染）。⑤回归 **270→282** 全绿（T40a-l：六页路由 200 + 307 落地 + 白名单外 404/资产 Content-Type + ETag + 目录遍历防护/页面 304 协商/六页壳挂载点 + 主题引导 + 版本替换/model·pools·stats·logs·keys·plugins 六页业务接线/共享资产单源/双面板零污染复核）；hmg.js + 六页内联 JS node --check 全过；8660 浏览器实测：六页渲染、添加供应商弹窗字段/新增态余量区隐藏、白天主题 token 反转（同 DOM 仅换色）、手机视口 390×844 顶栏 + 离屏侧栏 + 单列 + 抽屉滑入全过；实测发现并修复 keys 页用量行窄列断行一处（修复后复验）；`gui-test-screenshots/` 留双主题×桌面/手机四象限截图证据。生效说明：**需重启网关一次**（注册新路由），此后纯前端资产改动刷新即见（mtime ETag 自动失效）。
**`v2.16.10`** — **🔋 余量监控工位（自 hellonew v2.22.8 移植工作单整卡移植）**：六平台套餐/余额查询（MiniMax / 智谱 GLM / 小米 MiMo / 百度千帆 / DeepSeek / OpenRouter）。①**用户可见仅三处**（无页签、无卡片监控区块——中间批次 UI 已被源仓推翻，移植按 v2.22.8 最终态）：供应商卡片头部内联余量（bars 型逐条「5小时窗口 1,100/28,000」千分位+三色分级+悬停倒计时 / 余额型「剩余余额：¥124.11」两位小数 / error·unset·pending 弱提示 / 未绑定完全空白；**挂载点双面板位置分立**——hfadmin 白天版在协议徽章右侧、index 暗黑版在卡片标题供应商名+ID 右侧（徽章在按钮行，跟徽章放会带离名字）；供应商编辑弹窗「🔋 套餐/余额监控」配置区（主开关+获取方式自动/自定义+平台条件字段+全局轮询间隔，勾选随「保存」建/更绑定并立即查询，取消勾选随保存停用凭证保留，🗑 解绑删历史，新增供应商整块隐藏）；调度熔断（绑定供应商耗尽 → 该供应商整体 503 `provider_quota_exhausted` → reset_at 到期或余额回升自动恢复；error/unset 快照不熔断）。②**后端**：三张 SQLite 表（quota_bindings/ snapshots/history，存量库幂等升级补 script_id/params_raw 列）+ 11 个读写函数；`app/core/quota_providers.py`（841 行：PLATFORMS 六平台元数据驱动前端下拉零改动、`_epoch()` 兼容毫秒/秒/剩余时长、千帆 BCE v1 HMAC 签名、小米 302 登录链自动续期回写、智谱 401/403 Bearer 重试、provider 来源实时取 config 不做快照、refresh_binding 永不抛异常、`provider_circuit` 零状态表惰性重推）与 `app/core/quota_server.py`（174 行：8655 独立端口 /health·/refresh·/result，token 可选鉴权，**socket 预探测——端口被占优雅降级只记日志绝不拖崩 lifespan**）；九个 `/admin/quota/*` 管理端点（密钥脱敏前4…后4、更新显式提交才覆盖/空值保持原值、params_raw 原样落库不解析）；30 分钟轮询 quota_poll（照 db_maintenance 口径绝不阻塞调度，改间隔走 PUT settings 即时生效）；**每次刷新全量落盘仓库根 `cheackresult.json`（绝不含密钥）**。③**修源仓带入 bug 一处**：`build_result_document` 对 `list_quota_with_snapshots` 已解析成 dict 的 payload 二次 `json.loads`——TypeError 被吞成空对象，cheackresult.json 对外的 bars/balance/plan 恒 null（仅 status/error 幸存；即源仓工作单坑实录 #1 的镜像位），本仓改为直接用 dict。④**配置**：`quota_monitor` 节点七键全默认值（enabled/interval_minutes/circuit_enabled/server_enabled/host/port 8655/token，config.json 已显式落一份；改端口需重启网关，其余 PUT settings 即时生效）；根目录新增「开通余量刷新端口.bat」（netsh 放行 TCP 8655，管理员运行）。⑤回归 **258→270** 全绿（T39a-l：六平台元数据/custom 绑定即查 unset 执行器未启用/列表三合一/PUT 留空保持+停用凭证保留/DELETE 级联零残留+scripts 空目录/deepseek manual 建查 ok 余额 124.11+Key 掩码/结果文件无密钥+刷新端口三端点/熔断 503+决策原因码/error 不熔断/reset_at 到期恢复/circuit_enabled=false 旁路/双面板前端位置分立+四接线）；套件配套加固：mock 端口 8125 预清理（排除自身 PID）+ 刷新端口套件专用 18655（8655 可能被同机其他网关占用）+ finally server_close 防 shutdown 悬挂滞留毒化下轮 + T7 并发单请求网络异常降级为断言失败 + 网关 returncode 打点；双面板内联 JS node 语法检查过；8660 实测见提交说明。生效说明：**需重启网关**（新表/新端口/熔断挂点）；面板刷新即见。
**`v2.16.9`** — **用户用量历史全量记录 + 统计首开提速 + 按模型池嵌套池改版 + 调用记录筛选守卫（四项体验修复批次）**：①**用户 Key 用量历史恒记**（问题1）：`charge_key_usage` 原对管理员 Key 与未限额 Key 直接早退——`api_key_hourly_usage` 小时桶恒空，面板「用量历史」弹窗除管理员伪 Key#0（decision_log 口径）外全空；改为限额计量仍按原语义跳过、**小时桶记录对所有带 Key 的调用恒写**，`main.py` 四计费点（chat 流式/非流式 + embeddings + rerank）`is_user_key` 判定放宽为带 Key 即入账（管理员类型 Key 同样落账，服务器密钥无 key 记录仍走 decision_log 口径），`search_summary` 专门 Key 挂账注释同步。②**统计首开提速**（问题2）：`loadStats` 原 `await /stats` 后才并行拉视图元数据（providers/pools/models）——串行两轮往返，慢链路（远程/手机）首开白等一倍 RTT；改为 `/stats` 与元数据**同拍并行发起**（TTL 缓存照旧），双面板同构。③**按模型池视图嵌套池改版**（问题3）：原「取第一个收录该模型的全局池」导致嵌套池/多池归属的模型全页只显示一次；改为模型在其直接所属的**每个**全局池各出现一次（卡片键 `模型id@池名`，防增量刷新同卡被多组争抢）；父池组内的子池不再展开模型，以一张**子池计费卡片**代替（`stat-subcard`：虚线边框/紫色侧条/「子池」徽章/无进度条，聚合整棵子树去重后的今日 token，右下「含 N 个模型」，树聚合带防环），专属池仍不参与，未入池模型照旧落「未入池」组。④**调用记录筛选序号守卫**（问题4）：`loadDecisions` 原无并发守卫——切池筛选与 5s 轮询并发时旧筛选的晚到响应覆盖当前视图（具体池视图显示旧数据、"全部池比具体池更新得快"的观感）；加 `_logLoadSeq` 序号守卫（与 `loadStats` `_statLoadSeq` 同款），旧响应直接丢弃。⑤**顺修 index 统计卡片增量字段残留**：v2.16.6 移植引入的 `statUpdateCard` 用 `x.id/x.name`（stats 条目无此字段）两处漏网（v2.16.8 T37c 只修了卡片键），经典面板每 5s 增量刷新把模型名/ID 写成 "undefined"，修正为 `x.m.id/x.m.name`。⑥回归 **251→258** 全绿（T38a-h：无限额Key小时桶恒写/管理员类型Key入账/最近调用不回归/首开并行拉取接线/按池嵌套改版接线/筛选守卫接线/增量字段修正）；README 断言计数三处 248→258（v2.16.7/v2.16.8 两批漏同步）；双面板内联 JS node 语法检查过；8660 重启实测四项：hu（无限额）Key 真实调用后用量历史 18 tok + 调用记录可见、default 组四张子池卡（`pool:netmodel` 聚合 216 tok 正确）、`GLMAPI/glm-5.3-flash` 四处出现且增量刷新零重复、auto 池筛选视图轮询带出新记录、付费视图四请求 t=0 并行发出。生效说明：①需重启网关（keyauth/main 计费路径）；②③④⑤纯面板改动刷新即见。

**`v2.16.8`** — **分组管理入口改版 + 统计外层系统组嵌套（自 hellonew v2.22.3/v2.22.4 回移，纯面板零后端）**：①**设置弹窗交互重排**（双面板同构）：分组收纳每行开关右缘「⚙️ 管理」按钮——点击展开该维度自己的管理盒（模型管理/模型池互斥单开、按钮高亮、开关勾选态不受影响，label 内嵌 button 经 preventDefault 不误触开关），盒内 ↑↓ 排序（首末行禁用）、点名称重命名（复用 `grpRename`）、**🗑 删除分组**（confirm 确认，仅拆组——成员卡片回「未分组」，模型/池本身不动）；收纳开关与分组数据独立（关开关仍可管理删组）；v2.16.7 的弹窗底部合并管理区移除。②**统计页按供应商/按模型池嵌套「分组收纳」系统组**：外层组序=`ui_groups` 数组序（与设置弹窗 ↑↓ 调序联动）、组头=组名+模型数+今日 token 合计、折叠按维度持久化（`gw_statgrp.ocollapsed`）、未归组/auto/兜底/未入池/独立模型落「未分组」垫底、空组不渲染、无分组老用户零感知（回退平铺）；**外层随收纳开关联动**：对应维度开关关闭即不套系统组壳（回内层平铺）、重开原样恢复，两维度互不影响；内层 ⠿ 拖拽限同外层组内调序（dragover 同父守卫，跨外层=改收纳归属去分组页操作）。③**顺修 index 面板统计卡片增量键 bug**：v2.16.6 移植四视图时 index 的 `statCardEnsure/statRenderFlat/statRenderGrouped` 用 `x.id`（stats 条目无此字段），经典面板统计页每 5s 增量刷新退化为整卡销毁重建闪屏，修正为 `x.m.id`（hfadmin 一直正确）。④移植与验证：533 行补丁双面板零冲突整贴（v2.16.7 遗留的表单文案行本次随补丁归位）；回归 **248→251** 全绿（T37a-c：⚙️管理入口在位/outerOn 门+嵌套元素在位/index 增量键修正）；双面板内联 JS node 语法检查过；8660 浏览器实测 cog 互斥展开/开关不误触/删组/关开关外层消失与重开恢复。生效说明：纯面板改动刷新即见，无需重启网关。

**`v2.16.7`** — **分组收纳设置弹窗分组管理 + 「预估费用」按钮放开付费模型（自 hellonew v2.22.2 回移，纯面板零后端）**：①**分组管理**（双面板同构）：⚙️ 分组收纳设置弹窗新增「分组名称与展示顺序」管理区——「模型管理」「模型池」两列各列出已有分组（↑↓ 调序、首末行自动禁用；点击名称重命名复用主页面 `grpRename`；成员数展示；空列引导文案），数组换位即改 `grpWrapHtml` 渲染序，`grpSave` 即时镜像服务端（`ui_groups` 数组序即展示序，跨端共享），`loadModels`/`loadPools` 同步重渲两页；弹窗每次打开 `grpMgmtRender` 刷新，**只读展示不落空数组**（未启用收纳时开弹窗不写服务端）。②**预估费用按钮放开**（hfadmin）：公共卡片 `costBtn` 与付费视图 `paidCostBtn` 判定 `cost_enabled` → `(cost_enabled || !is_free)`——付费模型恒显示（与 v2.16.6 的付费默认启用计费统计对齐；显式关闭的付费模型点开弹窗可配单价，明细区空数据既有兜底提示在位），免费模型仍按勾选显示；表单勾选框说明补「（付费模型固定显示该入口）」。③移植与验证：190 行补丁 index 全干净、hfadmin 七 hunk 六过（被拒一处为表单文案行，上下文含本仓没有的 reasoning_auto 行，手工补齐）；回归 **248/248** 全绿（纯面板，T36 系列不受影响）；双面板内联 JS node 语法检查过；8660 刷新实测设置弹窗两列管理区与统计页付费按钮全显。生效说明：纯面板改动刷新即见，无需重启网关。

**`v2.16.6`** — **用量统计四视图重构 + 四项体验修正（自 hellonew v2.22.0/v2.22.1 回移）**：①统计页「计费量/调用次数/Token 用量」三选项改为「**按计费量/按供应商/按模型池/按付费量**」四视图（双面板同构）。按计费量=使用占比降序（分母=可用上限 k×上限，与卡片红/橙判定同源；并列按当日用量，未设限额垫底、余额返还制无上限视为已满排前）；按供应商/按模型池=分组展示（`provider_id`→/admin/providers 解析显示名，独立直连模型按协议归「独立模型」组；池取第一个收录该模型的全局池、用户专属池不参与、未入池单列），组头可折叠+⠿ 拖拽调序，均持久化 `localStorage.gw_statgrp`（**按维度合并写回**，provider/pool 两套互不覆盖，双面板共享），组内=计费量口径、组间默认=组今日用量降序（拖拽序优先）；按付费量=仅付费模型，有单价的按累计预估费用=（命中×价+未命中×价+输出×价）/1M 降序在前（平峰口径，高峰分段卡片注明仅费用弹窗计），未设单价按累计 token 垫后。②后端 /stats 扩展（+18 行纯增量，不动计费入账热路径）：`get_stats_snapshot` 新增 lifetime（model_daily_stats 全历史 SUM 次数/token）与 cache_totals（model_cache_stats 全历史 SUM 命中/未命中/输出）两聚合；get_stats 每模型透出 `total_requests_all / total_tokens_all / cost_tokens{calls,hit,miss,out} + provider_id`（原 `provider` 字段是协议名，供应商分组必需 provider_id）。③旧「调用次数/Token 用量」平铺视图移除（📊 分组查看弹窗兜底聚合口径）；卡片增量刷新完整保留（不闪屏、进度条过渡）；loadStats 加 `_statLoadSeq` 序号守卫（5s 定时器与切视图并发防错形卡片串形）；切视图清空网格重建（卡片形状不同）。四个实施坑随补丁原样带入：/stats 必须透出 provider_id（否则全落「独立模型」组）/持久化按维度合并写回/HTML5 拖拽 target 恒为可拖元素本身、手柄判定须 mousedown 打 dragOk 标记/渲染序号守卫。④**四项体验修正**：统计页提示语缩回一行（四视图口径挪悬停 title）、节省统计/手动刷新并入按钮行整簇贴右；「收费标注」切到付费自动勾选「缓存 Token 计费统计」+ 编辑付费模型且 config 从未写过该字段（undefined）时回填默认勾选（默认打开、显式关闭优先；该开关仅 hfadmin 表单有）；移除「不发 stream_options」表单选项三处（勾选框/编辑回填/保存提交，后端字段与注入逻辑保留——本仓 config 实测零模型勾选，行为不变）；新建模型两入口（添加模型/在供应商下添加）默认勾选「支持 JSON 格式输出」（编辑仍按已存值回填，回填更晚执行天然覆盖）。⑤回归 **244→248** 断言全绿（T36 四项：stats 累计口径全模型透出/cost_tokens=T26 精确累计 3次·命中80·未命中220·输出99/双面板四视图元素在位+旧维度按钮移除/付费排序口径在位）；双面板内联 JS node 语法检查过、f-nso 零残留。顺带修复：README「### 最新」标题在 v2.16.5 重复两行、回归提示仍写 8651（v2.16.5 已改 8678）。生效说明：后端字段需重启网关；面板刷新即见。

**`v2.16.5`** — **图片 MIME 归一化 + 图片格式不支持改判可切换（书生 image/jpg 同款根治，自 hellonew v2.20.1 回移）**：①**根因**：客户端内联 base64 图片 MIME 写成非标准 `image/jpg`（标准为 `image/jpeg`），`AnthropicProvider._convert_content` 原样透传给严格校验的 Anthropic 兼容上游（书生 `/v1/messages`）→ 400 `invalid_request_error`；该报错体命中 #30「确定性请求缺陷」分类 → 不冷却不切换不走兜底池直接透传。②**修复一（归一化）**：`format_adapter.normalize_image_media_type`（`image/jpg`→`image/jpeg`，空值缺省 `image/png`，大小写/空白容错）接入两个协议转换出口——`anthropic_to_openai` 与 `AnthropicProvider._convert_content`（客户端直发 Anthropic 图片块分支浅拷贝改写不动原请求对象）。③**修复二（改判可切换）**：`pool.py` 新增 `_is_image_unsupported_error`（"image"+"not supported"），**置于缺陷分类之前判定**（同含 `invalid_request_error` 否则被截胡）——非流式/流式两路径统一改判：不冷却换下一候选直至兜底池，整链失败才借道透传原文，决策标签 `image_format_unsupported`；真缺陷类（InvalidParameter 等）行为不变（T15 全绿）。④回归 238→244 断言全绿（T35 六项：MIME 归一化/两出口改写且不动原对象/兜底池接住 200+决策标签/整链失败透传原文）。⑤回归基建：回归端口 8651→8678 + 预清理安全化（先杀后复扫，外部进程占用报错中止不误杀——2026-10-09 实证无差别强杀 TV 助手 node 代理后被守护重占致断言连挂）。生效说明：需**重启网关**。

**`v2.16.4`** — **问题清单大修（12 项一次修复）：#45-#48/#50-#56 + #46 三项验证期新发现**。①**#45 Anthropic 完整工具调用**（/v1/messages 摘 400 门，format_adapter 补四段转换：入站 tools 定义 input_schema→function.parameters、消息内 tool_use/tool_result 历史往返→tool_calls/role:tool、出站 tool_use 块、流式 input_json_delta——Claude Code 类客户端可用；#34 随之关闭）。②**#46 配额包**：expired 粘滞位自动复位（调高 max_tokens>已用量即救回，clear_one_time_expired+复位日志）+ 46A 模型 POST/PUT 补 _sync_pool 热生效（连带消除 46B 注册表不新鲜重新置位）+ 46D 按 token_type 剥离跨类型字段 + 删除模型清状态行。③**#47+#48 提速包**：GZip 中间件（面板 HTML 271KB→77KB 降 72%，API JSON 同受益）+ 双面板 ETag/304 零传输 + 补 providers/reorder 的 _sync_pool 后删双面板全部 31 处冗余 /admin/reload（注册表重建 2→1）；根因修正入册（N+1 与逐候选探测均不成立，真凶传输量与前端串行）。④**#52+#53 图表包**：用量曲线补 Fritsch–Carlson 切线限幅（原实现漏限幅致爬坡/回落形态越界至 -259，非"Bezier 平滑"）+ 时间档位扩为 24h/3天/7天/30天（3d 按 6h 桶、7d/30d 按日）。⑤**#50+#55 前端小件**：节省统计按钮随 headroom 启停显隐（异常保持显示防误藏）+ 拖拽自动滚动自实现上下 1/4 视口检测区（原生边距不可调；监听捕获阶段兼容池内条目 stopPropagation）。⑥**#51 管理员用量可见**：伪 Key #0 特判按 decision_log caller='管理员' 小时聚合（不动计费热路径），密钥页固定「管理员（服务器密钥）」行。⑦**#54+#56 分组包**：分组数据上移 config.json（GET/PUT /admin/ui_groups，启动同步+保存镜像+本地静默迁移，手机跨端可见）+ 添加供应商弹窗可选入组。⑧自测修补：loadKeyUsage 的 !usageKeyId falsy 判空早退伪 Key #0、拖拽监听改捕获阶段。**核销四项**：valve_pct=0（T13e 断言证为"停用模型"有意设计）、daily_token_limit 三态（全后端一致 0=不限）、gift_grant_cap 优先级（不参与耗尽判定）、#49 分组拖拽（复核已实现）。回归 **218→238** 全绿（T32 配额复位/T33 工具调用全链含上游 payload 断言/T34 管理员用量）；8660 实测 gzip/304、跨端分组（手机 390px）、四档图表、拖拽滚动 112/155px。挂账区见 docs/问题待修复.md（批量加池/max_tokens 拆字段等六项）。生效说明：后端部分需重启网关；面板行为刷新即见。

**`v2.16.3`** — **#44 追加——缓存亲和设置表单升级（池清单勾选 + 滑杆）+ 修复回显 bug**：①**通用插件设置渲染器扩展**（双面板同步，manifest config_schema 新增 `ui` 渲染标记，其他插件可直接复用）：`ui:"pools"` 字段渲染为**池清单勾选**（`/admin/pools` 拉取按名排序，首项「🌐 全部池（默认）」总闸——勾选即清空并禁用具体池勾选，存量逗号串配置正确回显勾选态，清单接口取不到回退文本输入）；`ui:"slider"` 字段渲染为**可拖拽滑杆**（manifest min/max/step 驱动，右侧实时数字徽标随拖动更新）。②affinity 插件 manifest v1.1.0：pools 加 `ui:"pools"`，virtual_nodes 加 `ui:"slider"`（边界 10-200 步进 10，默认 40 不变，标题「虚拟节点数（分布均匀度）」，说明文案解释只影响均衡度/修改会重排一次落点）。③配套样式（勾选列表限高滚动、滑杆行布局）。④**修复实施中发现的回显 bug**：池清单解析三目方向写反（`val==null ? val : ''`），已保存的非空池清单重开设置时会错误显示为「全部池」——修正为空值兜底空串。验证：双面板 `node --check` 过；8651 浏览器实测（22 池勾选清单渲染/全部池总闸联动/勾选 auto+arkpro+滑杆 80 保存落库/重开正确回显/真按钮还原默认，API 权威核对 config 往返一致）。

**`v2.16.2`**

**`v2.16.2`** — **🧲 缓存亲和路由（`affinity` 插件）**：解决中转池路由打散请求导致的上游缓存命中率低——同一调用 Key 在同一池内始终优先路由到同一**模型条目**。①**插件本体**（`app/plugins/installed/affinity/`，managed 域、默认停用、面板热启停）：无状态一致性哈希（`hash(key_id)` 落在 `sorted(候选条目)` 哈希环上的虚拟节点，默认每条目 40 虚拟节点可调），不建记忆表——重启/热加载后亲和关系自动恢复（记忆表方案会丢，重启后第一波请求全部 miss）；候选集增删只影响相邻 Key 落点、多 Key 天然均匀分布；配置项 `pools`（逗号分隔池名清单，留空=全部池）与 `virtual_nodes`。**粘性粒度是条目而非模型名**：上游提示词缓存跟随 上游账号×模型×前缀，同名模型挂多供应商时只有条目级粘性能命中。②**核心 hook 点**（选模层首次对插件开放，模式照抄 `active_compressor` 成熟先例）：`GatewayPlugin.preferred_model(pool_name, key_id, candidates)` 协议方法 + `plugin_center.preferred_model()` 分发（单插件抛错只损失其建议，绝不影响选模主流程）；`pool._select_from_pool` 在单模型锁定之后、池内遍历之前询问——亲和只是**优先**：候选先按请求稳定约束预筛（模态/JSON/视觉），目标过完整 `_check_available` 不可用则记 `affinity_unavailable:<原因>` 落回池内原策略（sequential/auto_order/load_balance），恢复后自动回粘；Switch 定向请求与子池递归不参与（同 `single_override` 旁路理由，防静默跨侧）；管理员服务器密钥直连无 key 记录自然不亲和。③**调用方身份贯通**：`main.py` 三端点（chat/messages/responses 经 `_chat_handler`、embeddings、rerank）计算 `caller_key_id`（按 key **id** 而非名字，改名不打散亲和）传入四个 fallback 入口（非流式/流式/embedding/rerank，hook 在 `_select_from_pool` 一层全部覆盖，兜底池内同样亲和）。④回归 211→218 断言全绿（T31：插件关轮询打散基线/同 Key 六连粘哈希期望条目+`affinity_selected`/reload 后落点不变/目标停用 valve 记 `affinity_unavailable` 落回次序+恢复回粘/池清单外不亲和/switch=local 旁路无 affinity 步/embedding 模态预筛后粘同一条目≠chat 条目）。README 特性表加缓存亲和行、调用顺序节加亲和条目、原因标签表加两条、断言数 211→218、start_gateway.cmd 对齐 v2.16.2。验证：回归 218 全绿；8651 浏览器实测插件中心卡片与设置页。生效说明：插件默认停用，面板「插件中心」启用即热生效（无需重启）；`auto_order` 池不建议开启（亲和会覆盖其优先消耗快到期配额的排序语义）。

**`v2.16.1`** — **接口命名对齐图片官方名（OpenAI Responses / OpenAI Compatible / Anthropic）+ `openai_responses` 上游协议**：①**命名修正**：v2.16.0 误用了 Chat Completions/Messages 等非图片官方名，现按用户提供图片统一为 `OpenAI Responses` / `OpenAI Compatible` / `Anthropic`——双面板协议下拉改为三接口在前（按图片顺序）+ 千帆搜索/千帆网页搜索垫后，默认选中仍为 OpenAI Compatible（`selected` 固定，新增表单行为不变）；徽章新增 `.b-responses` 配色、`protocolLabel`/`provBadge` 三接口映射，内部枚举值不变（config 零迁移）。②**新上游协议 `openai_responses`**（第三接口的上游侧补齐）：新增 `app/providers/responses_provider.py`（与 AnthropicProvider 同构——`chat()`/`chat_stream()` 以内部 chat 格式进出、转换在本层完成，pool 的路由/计费/流式 usage 提取零改动；POST `{base_url}/responses`，429→RateLimitError，speedtest 用最小 Responses 请求）；`responses_adapter.py` 补上游方向三函数：`openai_to_responses_request`（messages→input items、system→instructions、nested tools→flat、tool 消息→function_call_output、reasoning_map 片段合并且 `reasoning_effort` 键转译至官方嵌套位）、`responses_to_chat_response`（message→content、reasoning.summary→reasoning_content、function_call→tool_calls、input_tokens_details.cached_tokens→cached_tokens）、`responses_sse_to_chat`（Responses 事件流→chat chunk 流，completed/incomplete/failed 终态 + usage 帧 + [DONE]，事件耗尽兜底补发）。③接入：`pool._build_provider` 分支、admin 协议白名单三处（POST/PUT providers + test_model 连通性测试走 ResponsesProvider）、probe_reasoning 对新协议自然跳过（与千帆同现状）。④回归 208→211 断言全绿（T30f chat 客户端→Responses 上游互转+计费 133：上游 payload 断言 input/instructions/max_output_tokens；T30g Responses 客户端→Responses 上游全链往返；T30h 上游 Responses 事件流→chat chunk 流式+决策日志 133）。README 命名全面修正（特性表/限制/端点表/上游适配表加 openai_responses 行）、断言数 208→211、start_gateway.cmd 对齐 v2.16.1。验证：回归 211 全绿；mock 上游新增 /responses 路径分支（非流式+事件流）；node --check 双面板全过；8651 浏览器实测三接口下拉顺序与官方名。生效说明：新协议路由需重启网关；面板下拉刷新即生效。

**`v2.16.0`** — **三接口官方命名 + OpenAI Responses API（含工具与状态化）**：①**接口命名统一为官方名**：面板协议下拉/徽章/详情显示 `Chat Completions`（原"openai（兼容格式）"）/ `Messages`（原"anthropic"），内部枚举值不变（config 零迁移，顺带修复 hfadmin 供应商表单与 admin 面板文案不一致）。②**新端点 `POST /v1/responses`**（官方路径，OpenAI SDK `base_url` 以 `/v1` 结尾即同一套配置直接 `client.responses.create()`）：新增 `app/core/responses_adapter.py` 纯函数适配（与 format_adapter 同构，内部经 chat 枢纽，路由/计费/决策日志零改动）——请求方向 `input`(str/items)→messages、`instructions`→system、flat `tools`→nested、`function_call`/`function_call_output` 历史往返、`tool_choice` 映射、`reasoning.effort`→`reasoning_effort`、`max_output_tokens`→`max_tokens`、`text.format`→`response_format`；响应方向 output items（`reasoning`/`message`/`function_call`）+ 官方 usage 形状；流式 chat chunk → Responses 事件流（`response.created`→`output_item.added`→`output_text.delta`/`reasoning_text.delta`/`function_call_arguments.delta`→`response.completed`，sequence_number 递增，幂等收尾兜底照抄 openai_sse_to_anthropic 成熟模式）。③**状态化**：新表 `responses_store`（CREATE TABLE IF NOT EXISTS，存量库重启自动建）存档链展开后的完整输入与响应对象，`store` 默认 true、`previous_response_id` 一次查库链展开（存档即展开结果无递归）、`item_reference` output 内一级解析；`GET /v1/responses/{id}` 检索、`DELETE` 删除（创建 Key 本人或管理员）；TTL config 键 `responses_store_ttl_hours`（默认 720h），存档时顺带清理过期行（不进 scheduler）。④不支持项明确 400：`background`/`conversation`/内置工具（仅 function）/未知 item 类型；`truncate`/`include`/`metadata` 忽略。⑤回归 203→208 断言全绿（T30：非流式转换与上游 payload 断言/工具定义-历史-结果与响应往返/流式事件序+completed 带 usage+计费落账 133/状态化存取-链拼接-删除-store=false-404/不支持项四连 400）；修复实施中发现的 previous_response_id + 字符串 input 的 list+str TypeError。生效说明：需重启网关生效。（注：本版命名后被 v2.16.1 修正为图片官方名）

**`v2.13.3`** — **池级 Switch 切换路由（配合 dsh 脱敏网关插件）**：①**池编辑新增「🔀 Switch 切换」开关**：config 池字典 `switch_enabled`，双面板池卡片接线（复用 auto_order/load_balance 开关模式），开启被拒（池内缺某侧模型）时 api() 自动 toast 服务端原因，失败也重拉池让勾选回弹为服务端真实状态 ②**本地/云端标注复用 `token_type`**：`local`=令牌类型为 local 的本地模型（v2.12.5），`net`=其余云端模型——模型管理零改动、存量零迁移 ③**新入站端点 `POST /{池名}`**（不走 /v1）：`switch=local|net`（query 参数优先、body 字段兜底）定向路由池内对应侧模型，校验链 404 池不存在 → 403 Key 无池权限（复用 allowed_pools，不新增权限位）→ 403 池未开 switch → 422 非法/缺失值（报错列出合法值）；body.model 一律改写为池名 ④**语义红线——绝不静默跨侧**：switch 请求旁路 single_override 与兜底池升级，同侧耗尽宁可 503 让客户端重试（脱敏场景下跨侧=安全事故）；同侧多模型仍可在同侧内按序重试/冷却，失败详情明确报"池内没有该侧模型" ⑤**路由可查零建表**：decision_log 复用现有表，`requested` 列写 `switch:local/net`，调用决策弹窗与 Key 最近调用记录两个现有界面零改动即显示走了哪侧 ⑥**开池校验**：开启 switch 时按本次提交的 model_ids 递归展开（含 pool: 子池）要求两侧齐全，缺侧 400 带指引；先校验后改配置，被拒改动不残留共享缓存 ⑦回归 142→162 断言全绿（T24 二十项：两侧路由精确且另一侧零调用、同侧耗尽 503 不跨侧、决策日志与 /admin/decisions 可查、授权 Key 计费 133、流式、body 传参与 model 补齐、开池校验 400/不残留/还原、双面板控件在位）

**`v2.12.7`** — **面板打磨：滑块位置统一 + 折线图反过冲 + 悬浮数据 + 手机端适配**：①**主题滑块位置统一**：admin 滑块桌面端对齐 hfadmin 顶栏右缘的精确坐标（实测右缘差 0px；媒体查询限定桌面端生效，移动端布局不受影响）②**用量折线图反过冲**：Catmull-Rom 在尖刺型计量数据（10M 单峰夹平缓小时）上会剧烈过冲振荡、甚至画出 0 以下的"负用量"，改为**单调三次插值（Fritsch–Carlson）**——曲线平滑且严格不过冲，不越过相邻数据点范围 ③**图表悬浮数据**：鼠标/触屏移动显示竖向参考线 + 数据框（昨日/今日 HH:00 · 用量 N tok），最近数据点放大指示 ④**手机端适配**：折线图宽度随容器 1:1 渲染（320-760px 自适应，字体不缩放），resize 自动按新宽度重渲 ⑤回归 122/122 断言全绿（新增 T21：双面板滑块接线与跳转目标、折线图与调用记录容器静态检查）；浏览器实测：滑块右缘差 0px、曲线包围盒完全在 0 线以上、悬浮数据框、390px 手机端渲染与触屏交互正常

**`v2.12.6`** — **面板体验三连：每池调用记录 / 密钥用量历史增强 / 明暗模式一键切换**：①**调用记录改为每池保留最近 50 条**：`trim_decision_log` 由全局 500 条改为按池裁剪（scheduler 每 60s）——大池高流量不再把其他池的记录冲掉，面板按池查询恒有据可查 ②**API 密钥用量历史增强**：新增 `GET /admin/keys/{id}/calls` 端点（decision_log 按 caller=Key 名过滤，`get_decisions` 加 caller 参数）；双面板用量历史弹窗新增「最近调用记录（≤50 条，仅本 Key）」表格（时间/池/请求模型/结果 ✓✗/tokens），模态加宽 ③**右上角明暗模式滑块**：admin=夜间、hfadmin=白天，滑块状态随访问页面自动呈现，点击即在两个面板间跳转（`.theme-switch` 双面板各配色）④**用量历史图表重做**：柱状图改为**近 24 小时平滑折线图**（Catmull-Rom 贝塞尔平滑，只平滑视觉不改数据；滚动窗口跨昨日/今日，每小时数据点带悬浮明细，x 轴标注昨/今，y 轴自适应刻度）；右上角「当日总量」保留 ⑤回归 115→119 断言全绿（T20：55 连发后每池≤50 裁剪、密钥调用记录端点与字段归属、limit 参数）；浏览器实测：滑块双向跳转、双面板用量历史折线图与调用记录表渲染正确；修复测试助手 `call_count` 未按当日日期过滤导致跨天 delta 恒 0 的问题

**`v2.12.5`** — **本地模型（local 令牌类型）+ 切换窗口透传 + 千帆双协议统一性 + 思考探测计量**：①**新增 `local` 令牌类型**——LAN 自建推理（如局域网千问）不计费、不限量，仅记录用量：`_quota_check` local 分支永不拒绝，usage 照写 daily 账本与统计口径，安全阀随 limit=0 自动豁免（复用 v2.12.0 边界逻辑）；双面板令牌类型下拉/列表徽章/卡片标签；config 预置 `local-qwen` 供应商 + `qwen3.8-27b-nvfp4` 模型（`max_concurrency: 2` 对齐引擎并发上限，超时由部署者按需设置，上游文档建议 ≥180s）；用户 Key 侧照常计量（Key 与模型记账本就独立）②**切换窗口透传**：上游 503 `qwen_switching`（显卡视频↔聊天切换，附 `retry_after`）属暂态而非故障——非流式/流式两处错误分支不冷却、不切换，借道 ContextOverflowPassThrough 通道原样透传给客户端由其按 `retry_after` 重试（决策徽标 `switching_passthrough`；教训同问题30/31：不该烧池的错误别烧池）③**千帆双协议统一性**：审计 `qianfan_search`/`qianfan_web_search` 两变体调用与输出格式，修复 summary 解析路径 12 处缺省兜底（上游字段缺失或为 null 时两变体结构保持一致，不再一边 5xx 一边正常）；流式帧形/错误路径/计费路径审计一致，非流式 7 键/references 恒 list/流式帧形序列两变体完全一致，baidusearch 池内切换对客户端无感 ④**思考参数探测计量**：「自动查找思考强度」的探测请求会消耗上游 token，现按目标模型 token_type 全类型入账（gift/one_time/5h/daily/request，request 型计 1），同步 model_daily_stats 统计口径；探测线程经 run_coroutine_threadsafe 投回主循环记账、CLI 走私有常驻循环；admin 两个触发点 + CLI 全覆盖；无 usage 不记账不估算 ⑤回归 87→115 断言全绿（T17 本地模型/切换透传九项、T18 双变体结构统一十一项、T19 探测计量八项）；真实调用验证：百度搜索池（summary 变体解析新路径）、结构统一性 mock 对比通过

**`v2.12.3`** — **百度搜索接入（千帆搜索/网页搜索 + 两步式 AI 总结）+ 问题30/31 网关健壮性修复**：①**新上游协议 `qianfan_search`**——百度千帆「智能搜索生成高性能版」（`POST {base_url}/v2/ai_search/web_summary`）：请求为 Chat Completions 近似子集但**无 `model` 字段**，响应多 `references` 引用列表；新增 `providers/qianfan_search.py` 适配（白名单构建 payload、429→RateLimitError、流式 SSE 行级透传、speedtest/close），pool 协议分发 / admin 三处枚举 / 双面板协议下拉与供应商徽章接入 ②**新上游协议 `qianfan_web_search`**——百度搜索裸结果（`POST /v2/ai_search/web_search`）：上游仅回 `{request_id, references}`，网关合成标准 chat 响应；上游不支持流式，流式由网关合成两帧 ③**搜索流式帧形统一**（`search_sse.py`）：两协议对同一请求返回完全一致的帧结构（仅值不同），请求方可据此判断搜索池内是否发生切换 ④**搜索 AI 总结两步式**：网页搜索裸结果（含 `web_fetch.py` 抓取的网页正文，纯标准库解析/SSRF 防护/失败即跳过）交给指定 LLM 池总结（`search_summary.py`，复用池的 fallback/冷却/计费/并发槽，内层 `allow_search_summary=False` 杜绝递归，任何失败降级返回原始列表）；模型新增 `summary_pool`/`summary_length`，双面板「搜索 AI 总结」配置区（仅网页搜索协议显示），`/admin/pools` 下发 `summary_candidates`；非流式 `references` 经 `ChatCompletionResponse.references` 透传、流式随帧形带出 ⑤`ChatCompletionRequest` 新增 `extra_params` 请求级参数透传（黑名单防覆盖核心字段）；probe_reasoning 跳过专用协议不探测思考参数 ⑥部署：config 预置 `QianFan-Search`/`QianFan-WebSearch` 供应商与模型（面板填 Key 拖池即生效），网页搜索默认 `summary_pool: mm`；`billing_mode:"request"` 即按次计费口径（安全阀预估自动记 1 次、统计卡单位"次"）⑦**问题30 修复——确定性缺陷 400 快速失败**：调用端携带 1×1 占位坏图等非法请求时（DashScope `InternalError.Algo.InvalidParameter`），新增 `_is_request_defect_error`，非流式/流式两处 400 分支命中即**不冷却、不切换、break 借道 last_overflow 通道原样透传**（决策徽标 `request_defect_400`），终结"同一坏请求整池串行重演"；admin 供应商校验 base_url 不以 `/chat/completions` 结尾（防双重路径，问题30 附带项）⑧**问题31 修复——连接池幽灵租约自愈**：流式响应弃置时 httpcore 租约不归还（实测物理 socket=0 而账本 100 连接全在租、每请求空耗 120s 只能重启进程）——`main.generate()`/`_wrap_key_stream`/`openai_sse_to_anthropic` 三处补 finally 确定性 aclose 逐层传播；`_note_pool_timeout` 连续 3 次 PoolTimeout 自动重建 client（`_get_provider` 协议分支提取为 `_build_provider` 共用工厂），堵死态数秒内自愈、无需重启 ⑨修复两处既有问题：`/admin/models` 对供应商制模型回填解析协议（修 hfadmin 徽章恒显 openai）；`AnthropicProvider.chat_stream` 补 `no_stream_options` 形参（修该开关下流式 TypeError）⑩超时参数（`httpx.Timeout` pool 分量）保持默认不变，交由部署者按需自行调整 ⑪回归 62→87 断言全绿（新增 T14 千帆搜索/网页搜索、T15 缺陷400 透传/不切换/不冷却、T16 连续 PoolTimeout 触发重建）；真实调用验证：百度搜索、网页搜索+AI 总结两步式链路通过


**`v2.12.0`** — **使用量安全阀（k 系数）+ 并发负载查询 + 流式占槽**：①安全阀：模型新增 `valve_pct`（0~100%，默认 100）——预检升级为 **`已用+预估 ≥ k×最大量限制` 即跳过路由**（k=100% 同样启用），预估 = 输入 + 输出 EMA×1.1（call_metrics 校准样本≥10 生效，未校准回退仅输入，规避 v2.10.6 max_tokens 虚高误杀死锁）；分母按类型同源：daily/rolling_5h=`daily_token_limit`、one_time=`max_tokens`、gift=min(当前可用总额池， 用户上限)（补账前后自动跟随）、按次计费预估记 1 次；阀门判定在 5s 配额缓存之外逐请求计算（快照入缓存值，不膨胀缓存键）②统计卡片：条刻度=原始上限，**红线画在 k% 处（= 可用上限 k×limit）**，红线右侧淡红"红区"为超出部分；fill 宽度与百分比按原上限口径、触及红线即用满可用额度；**颜色（warn/full）按可用上限判定**（用到可用额度即转红）；主数字=可用上限 k×limit + 小字"原值 × k%"；k=0 显示"已停用"（阀门停用该模型）③双面板编辑弹窗新增**安全阀滑块**（轨道按值填色、k 数字弹跳），实时显示可用上限；gift 按 min(当前余额池， 用户上限) 动态预览、随补账变化，以统计卡片实时为准 ④决策原因新增 `valve_exceeded`（安全阀触顶，detail 含 used/estimated/effective_limit），双面板调用记录徽标+detail 格式化 ⑤**并发负载查询**：`active/waiting` 精确计数 + `_gate` 占槽封装，**修复流式完全不受 `max_concurrency` 限制的缺口**（流全程占槽，aclose 传播释放）；新增三查询端点 `GET /admin/model/{id}/load`、`GET /v1/model/load?model=`（用户 Key）、`/stats` 附带字段，面板卡片显示"负载 x/n · 排队 m"，详见[并发负载查询说明.md](并发负载查询说明.md) ⑥冷却统一 **5s**（原 429→10s，其余本为 5s；上下文超限 400 不冷却）⑦边界防护：上限 0（不限量）阀门不生效、`valve_pct=0` 即停用该模型、四入口（load/POST/PUT/前端）钳位 0-100 ⑧回归 50→62 断言全绿（新增 T13：k=100 计入预估/未校准不误杀/k=80 触线/k=0 停用/上限 0 放行/EMA 提前拦截/stats 带出）；UI 实测双面板+390px，修复 hfadmin `.stat-bar` 缺 `position:relative`（标线贯穿卡片）与增量新增卡 fill 宽度永空两个既有 bug

**`v2.11.43`** — **轮询暂停守卫加固**：switchTab 清除定时器后同步置 null；visibilitychange 显示分支加 null 守卫防重复叠加定时器；UI 实测 hfadmin 五页签+密钥 CRUD 全链路、admin 五页签、390px 手机端无横向滚动

**`v2.11.38~42`** — **性能与并发改造五批次（详见《优化改造计划.md》）**：①bulk() 写合并并发语义修复——全局计数改 ContextVar 任务本地深度，修"延迟持久/回滚越界/部分提交/取消悬空"四缺陷，bulk 全程持锁、他协程 DB 操作排队到事务外 ②流式结算写合并——收尾四段（入账兜底/缺失usage补记/校准样本/决策日志）+ `_settle_stream_tokens` 整组 + 流建立预扣合并为单事务（6~8 commit → 1~2），修中途半写失败后兜底重试重复记账的隐性边界 ③RPM/TPM 内存化——request_log 表退役改每模型 60 秒内存滑窗（每请求省 1 INSERT + 1 全表扫 DELETE），decision_log/call_metrics 逐笔裁剪移至每 60s 批量；新增 RPM 触顶回归（回归 46→50 断言）④/stats 批量化——单临界区快照（token_usage 懒重置 + 今昨统计 + 状态小表），面板轮询 ~250 次锁内查询 → 4 条 SQL，实测净耗时 41ms → ≈0ms ⑤传输层——上游 keepalive_expiry=300（空闲 5s 断连致重复 TLS 握手 → 长连接复用）、key 流 usage 子串预筛、_has_images 请求级缓存、reload 后台关闭旧连接池（修泄漏）、每 10 分钟 WAL TRUNCATE 回收、面板 HTML mtime 缓存读、前端 visibilitychange 暂停后台轮询、删除死代码

**`v2.11.31~37`** — 余额返还制账本/展示/校准多轮收尾：昨日剩余落库快照（yesterday_leftover）、人工校准优先级（同步改写自然日统计）、当前可用总额池口径（min(池,用户上限)）、预计补账按每日返还上限计算、切页回顶部等 UI 修复

**`v2.11.1`** — **管理面板模型编辑重构 + JSON 徽章 + 手机适配修复**：①双面板（admin/hfadmin）模型列表新增 `JSON` 徽章标识 `json_output` 能力（embedding/rerank 不显示），无需逐个点开查看 ②编辑弹窗移除"自定义参数名/值"（`extra_params` 废弃，保存时显式提交空对象避免 PUT 合并语义保留旧值；现网无模型使用该字段）③编辑弹窗重构：按"计费与额度/配额与限流/流式与输出选项"分组带标题分隔，智能估算/不发 stream_options/支持 JSON 三选项改为勾选高亮的选项卡片（原挤在超时时间/模型模态标签内），底部操作栏吸底（桌面/手机双档内边距贴合）④手机适配根因修复：index.html 的 `@media(max-width:640px)` 块原写在基础样式之前，`.f2` 单列等规则被后置同权重规则覆盖致手机版布局从未生效——移至样式表末尾 ⑤交互提速：点击"编辑"改为复用页面已加载数据 0 请求直开（原为 2 个串行请求先行），卡片入场动画延迟封顶 400ms（50 模型时原最长 2s 才显完），修复 hfadmin 编辑模式按钮文字（添加→保存）与重复 `onSmartToggle` 调用 ⑥计费回归套件 33/33 全绿（改动前后各跑一遍），UI 层经浏览器实测两面板桌面+手机视图

**`v2.11.0`** — **计费安全网 + 中转提速 + 策略精简（整合 v2.10.6~v2.10.10）**：①新增 `test_billing_regression.py` 计费回归套件（33 断言，隔离实例+mock 上游：流式/非流式入账、缺失 usage 记 0、request/one_time/5h 语义、用户 key 计费、并发 10 路无重复无丢失、思考透传、智能估算、stream_options 开关），任何改动先跑套件再上线 ②流式热路径提速：`<think>` 切分器 done 快速路径 + 仅 usage 行解析，每 chunk JSON 处理 3 次→0~1 次；输入估算按请求缓存；决策日志后置（流结束一次写入，移出首包路径）；`db.bulk()` 事务上下文，非流式 5 次串行 commit 合并为 1、keyauth 两写合一 ③决策明细新增 route_ms/upstream_ms 耗时分解 ④模型级 `no_stream_options` 开关、空 api_key 不发鉴权头、无 tools 不下发 tool_choice（修本地模型偶现 400）⑤修复 one_time 未校准模型被窗口估算永久误杀的死锁 ⑥回退问题23：配额预检恢复 `used >= limit` 旧口径，est_effective 仅用于决策日志展示

**`v2.10.10`** — **回退问题23（配额预检不再计入本次估算）**：应用户研判，配额预检恢复旧口径——仅 `used >= limit` 才拒绝，不再计算"调用后是否超额"（省估算计算、逻辑更简、消除未校准误杀风险）；est_effective 保留仅用于决策日志展示；修复过程中曾发现并保留 one_time 死锁防护

**`v2.10.9`** — **中转提速（问题21-A）+ 本地模型 400 修复（问题21-B），计费回归套件 33 断言全绿**：①`test_billing_regression.py` 计费回归套件（隔离实例+mock 上游，固化问题24 全部计费不变量与并发场景，任何改动先跑套件再上线）②流式热路径：`<think>` 切分器 done 快速路径 + 仅 usage 行解析，每 chunk JSON 处理 3 次→0~1 次 ③输入估算按请求缓存 ④流式决策日志后置（结束时一次写入含 actual_tokens，INSERT 移出 TTFB）⑤`db.bulk()` 事务上下文，非流式 5 次串行 commit 合并为 1、keyauth 两写合一 ⑥决策明细新增 route_ms/upstream_ms 耗时分解 ⑦模型级 `no_stream_options` 开关、空 api_key 不发鉴权头、无 tools 不下发 tool_choice ⑧修复 v2.10.4 引入的 one_time 未校准死锁

**`v2.10.5`** — **冷却时间策略统一**：应用户要求全部缩短——chat 非流式/流式"其他异常"15s→5s（429 保持 10s、5xx/网络保持 5s、上下文超限 400 保持不冷却）；embedding/rerank"其他错误"从一刀切 15s→5s；最坏不可用窗口 15s×N→5s×N

**`v2.10.4`** — **问题22/23/24 本地重做（以本地主线为基，含全部思考控制功能）**：①问题24 流式计费静默漏记修复移植——流式计费从 finally 移到"usage 到达即记"（billed 防重+finally 兜底+缺失 usage 告警记次数），decision_log 新增 actual_tokens 列（幂等迁移）并新增 `_settle_stream_tokens` 统一计费口径（request 型按次/token 型按真实用量），前端"估算/实际"同屏 ②问题22 Token 智能估算——新增 call_metrics 校准表，估算拆 est_window（窗口预检保守）/est_effective（输入+平均输出 EMA）两路，smart_estimate 模型非流式动态超时（吞吐 EMA×1.3 缓冲，样本<10 回退固定值），`GET /admin/model/{id}/metrics` + 双面板智能超时开关与内联 live 区 ③问题23 配额预检计入本次有效估算——daily/rolling_5h/one_time 三段均 `used+估算>=limit → 拒绝`（修复"151.6w/150w 仍放行单次冲线"），one_time 估算触顶不误标过期，配额缓存键带上估算值

**`v2.10.3`** — **请求级 `reasoning_effort: "auto"`**：客户端可显式传 auto 表示"本次请求不注入思考参数、用模型默认行为"（优先级高于服务端 default_reasoning_effort，含 auto/low 等任何默认档），作为默认档 low 的单次豁免；422 提示文案同步列出 auto

**`v2.10.2`** — **默认思考档位 low**：请求不传 `reasoning_effort` 时不再放任模型默认（多数思考模型默认开思考且深度自选），改为注入默认档 `low`（按各模型 reasoning_map 映射；未配置映射的模型不受影响）。config.json 顶层新增可选 `default_reasoning_effort`（六档任一，缺省 low；设 `auto`/`""` 恢复旧的"模型默认"行为；非法值回落 low），/admin/reload 热生效

**`v2.10.1`** — **新增模型自动探测思考档位**：① 非 embedding/rerank 模型新增保存后自动探测——上游组合已有探测缓存时瞬间套用映射，新组合后台探测（约 1~3 分钟）完成后自动写入 reasoning_map 并热重载，面板按探测状态提示 ② "思考映射"输入框对 embedding/rerank 模态自动隐藏，模态改为 embedding/rerank 保存时自动剥离已有映射 ③ probe_reasoning.py 新增 cached_suggestion/probe_single 接口供管理端复用

**`v2.10.0`** — **统一思考控制（reasoning_effort）**：① `/v1/chat/completions` 顶层新增 `reasoning_effort` 六档（off/minimal/low/medium/high/max，非法值 422），`/v1/messages` 的 `thinking{type,budget_tokens}` 自动归一化到同档位 ② 模型条目新增 `reasoning_map` 配置（档位 → 透传上游的请求体片段，缺档自动回落到更低档），在 pool 层统一注入，未配置的模型不注入 ③ `probe_reasoning.py` 全池实测各上游思考参数写法与档位并生成《思考参数探测报告》，`--apply` 一键写入 config.json ④ 思考内容回传统一口径：非流式 OpenAI 响应统一带 `reasoning_content`（MiniMax 等 `<think>` 内联自动提取）、流式 `<think>` 内联自动转 `reasoning_content` 增量并补发缺失的 `[DONE]`、Anthropic 客户端方向转 thinking 块/thinking_delta（此前非流式与 anthropic 协议上游的思考内容均被丢弃）⑤ 双面板模型编辑新增"思考映射"JSON 框 + 列表徽章

### 历史（按时间倒序）

| 版本 | 主要变更 |
|:---|:---|
| **v2.9.2** | updater 更新提速：版本查询并行化、fetch+ff-only 合并、requirements 未变跳过 pip install、启动/重启探测 1 秒粒度 |
| **v2.9.1** | 守护面板交互安全加固：诊断卡片移至操作下方；所有操作按钮二次确认；强制释放端口需输入操作密码（服务端校验） |
| **v2.9.0** | 双管理面板手机适配：hfadmin（≤760px）与 index（≤640px）完整响应式，桌面端零改动 |
| **v2.8.5** | "重启 updater"交互闭环：按钮"重启中"态 + 已等待秒数倒计时 + 轮询 /health 自动探测恢复 + 45s 超时排查指引，修复静默失联 |
| **v2.8.4** | 守护面板手机适配：≤560px 断点（状态卡 2×2、行上下堆叠、按钮双列满宽加高、Toast 通栏） |
| **v2.8.3** | updater 守护服务加固：git 自愈（unmerged 温和恢复 + 硬重置云端兜底，杜绝死循环）；端口强杀（SIGKILL 升级 + 残留进程清理 + 面板按钮）；诊断增强（systemctl 状态行/journalctl 尾部/错误时间线上屏）；仪表盘重做（苹果浅色主题）；新增 /action/restart-updater |
| **v2.8.2** | 模型级超时时间设置（120=默认/0=无限等待/指定秒数，解决本地非流式模型被 120s 误判超时）；OpenAI/Anthropic 适配器按模型独立超时；双面板表单同步；旧配置完全兼容 |
| **v2.8.1** | 池配置「加入模型」下拉按供应商分组排序（供应商持久化顺序 + 组内原顺序，独立模型排最后），新增 `====模型接口====` / `====子池====` 分割标题；移除「接口测速」标签页（⚡ 测速本池已覆盖，`/speedtest` 保留） |
| **v2.8.0** | rerank（重排）模型支持：`/v1/rerank` 端点（Jina/Cohere/SiliconFlow 兼容）；第三种专用模态进模型池（双向硬门槛 + 自动切换 + 计费复用）；test_model 按模态分流；双面板 rerank 徽章；anthropic 协议 embedding/rerank 误配拦截 |
| **v2.7.2** | 池配置引用失效显式记录（ref_not_found）；test_model 按模态分流（embedding 直测）；双面板 embedding 徽章；load_balance 轮转语义确认 |
| **v2.7.1** | json 严格路由：移除兼容降级，无 json 模型时 503 + 勾选指引 |
| **v2.7.0** | embedding 支持 + json 输出门槛；Ollama/WebUI 探测端点兼容；日志体系升级 + --port 参数；前端 json_output/extra_params |
| **v2.5.1** | 后端日志统一加时间戳；负载均衡提示框+滑块浅蓝色；hfadmin 补自动择优提示 |
| **v2.5.0** | 每日额度跨天惰性重置 + 负载均衡（round-robin 互斥自动化择优）+ models reorder 路由 + 5h 展示与 Token 单位 |
| **v2.4.1** | 代理连接测试 / 供应商排序 / 节点解析 / 缓存隔离等 5 项修复 |
| **v2.4.0** | VPN/clash proxy 支持（每供应商独立代理，httpx.AsyncClient proxy 参数） |
| **v2.3.8** | 429 冷却 30s → 20s；统一流式路径 detail |
| **v2.3.7** | 移除延迟阈值预设按钮（保留输入框+单位+hint） |
| **v2.3.6** | 美化延迟阈值输入框（玻璃拟态 + 内嵌单位 + 含义提示） |
| **v2.3.5** | 冷却时长按错误类型分级（429=30s / 5xx=15s / 网络=10s） |
| **v2.3.4** | 调用记录 step.detail 透传具体数值（限流/RPM/错误类型/HTTP 状态/延迟） |
| **v2.3.3** | 新建模型默认免费（`is_free=true`） |
| **v2.3.2** | 兜底池可配置化 + 延迟阈值可配置 + 新建模型并发默认0 + admin closeModal bug |
| **v2.3bate** | 修复问题 1/2/3/4/5/6 诊断（统计双维度 / Anthropic 格式 / API 密钥轮换 / 拖拽 / 模型顺序 / 内容空诊断） |
| **v2.2.1bate** | hfadmin 窄屏版本号紧凑显示 |
| **v2.2bate** | 密钥限额窗口归一化、授权池复选框对齐、供应商拖拽排序 |

> 💡 **版本号机制**：由 `git tag` 决定，启动时通过 `/version` 接口读取并展示在前端页脚。新版本通过 `git tag v2.3.X` 打 tag 后，前端立刻能看到。

---

## ⚠️ 已知限制

- ⚠️ 流式请求的 **TPM** 统计为近似值（RPM 与每日配额正常）
- ⚠️ 服务器在刷新时刻宕机会错过当次重置（不补跑）
- ⚠️ 上游提供商需支持 OpenAI 兼容或 Anthropic 协议
- ⚠️ 部分上游服务对纯文本块列表 `content: [{type:text, text:"..."}]` 兼容性差，本网关已自动合并为字符串（v2.3bate+）

---

<div align="center">

**Made with ❤️ for the local LLM community**

如果这个项目对你有帮助，欢迎 ⭐ Star！

</div>