# 中转站开发计划 · auto 模式（敏感信息脱敏 + 云端模板 + 本地终稿）

> 项目：`D:\AIcoding\model-gateway`（Model Gateway，对外 OpenAI / Anthropic 兼容）
> 状态：**计划，未实施**
> 配套文档：`终端开发计划-喜粤TV运营助手.md`（终端侧）、`喜粤TV运营助手-总体改造计划.md`（历史合并稿）

---

## 1. 范围与目标

### 1.1 本文档负责

1. **auto 模式**：把 `auto` 池升级为「自动识别敏感信息 → 可还原脱敏 → 云端出模板与处理建议 → 本地模型用真实数据出终稿」的双链路路由。
2. **更新分发通道**：为终端（喜粤TV运营助手）提供更新包托管、推送、上报与回滚的后台与端点。

### 1.2 本文档不负责

终端 Electron 壳、双 Chromium 槽、浏览器控制桥、安装包制作（见配套终端文档）。

### 1.3 与终端的契约（终端依赖这些接口，必须先冻结）

| 契约 | 值 |
|---|---|
| Base URL | `http://192.168.5.106:8650/v1` |
| 默认模型 | `auto` |
| 鉴权 | `Authorization: Bearer <Key>` 或 `x-api-key`（两条都已在 `verify_key` 支持） |
| 终端专用 Key | 新建 `xiyue-terminal`，`allowed_pools` 含 `auto`（**注意**：现网 Key 曾被记录对 `auto` 返回 403，实施前必须实测） |
| 更新清单 | `GET /updates/manifest.json` |
| 更新包 | `GET /updates/{full,patch,notes}/…` |
| 终端上报 | `POST /updates/report`（机器码、当前版本、状态、错误） |
| 健康检查 | `GET /health`（已存在） |

---

## 2. 现状审计（实测，非推测）

| 事实 | 证据 |
|---|---|
| 网关在线 | `GET http://127.0.0.1:8650/health` → 200；`config.json` 为 `host 0.0.0.0 / port 8650 / api_key 123456` |
| 对外只暴露**模型池** | `main.py::_resolve()`：非池名一律 404；`/v1/models` 返回池名列表 |
| 池清单（实测） | `auto`（strategy=sequential，fallback=`兜底池`）、`arkpro`、`arkflash`、`netmodel`、`openrouter`、`mm`、`deepseek`、`兜底池`、`arktext`、`glm`、`qwenemb`、`bgererank`、`baidusearch`、`local105`、`local99`、`local106`、`local256` |
| 本地模型确实可用 | `192.168.5.105:5000/v1/models`、`192.168.5.99:30000/v1/models`、`192.168.5.106:30001/v1/models` 全部 200 |
| 本地池构成 | `local105` = [Qwen3.8-27B, GLM-4.7-Flash]；`local99` = [GLM-4.7-Flash, Qwen3.8-27B]；`local106` = [Ling-3.0-tiny, GLM-4.7-Flash] |
| **auto 模式不存在** | 全仓 grep `脱敏｜desens｜mask｜redact` **零命中**；`main.py::_chat_handler` 无任何请求改写钩子 |
| 请求模型无扩展位 | `models.py::ChatCompletionRequest` 无 `metadata` / 自定义字段 |
| 更新分发不存在 | 全仓 grep `/updates｜updateBaseUrl` **零命中**（只有终端侧的旧 `updates/manifest.json` 占位） |
| 已有可复用基建 | `pool.py`（`execute_with_fallback` / `execute_stream_with_fallback` / 并发闸门 / 计费落盘）、`format_adapter.py`（Anthropic↔OpenAI + SSE 转换）、`reasoning.py`（六档思考映射）、`admin.py`（`/admin/*` 45 个端点 + `/hfadmin` 面板）、`database.py`、`keyauth.py`、`updater.py`、`test_billing_regression.py`（计费回归套件） |
| `updater.py` 是**网关自身**的 git 更新器 | `git_pull` / `perform_update` / `perform_rollback` / 服务重启 / 端口释放 / systemd 单元探测 —— 与终端更新通道**不同层**，不要混用 |

---

## 3. auto 模式设计

### 3.1 数据流（两阶段）

```
用户请求（含真实数据）
        │  (model = auto, 命中 auto_mode)
        ▼
┌──────────────────────────────────────────────┐
│ 阶段 A：脱敏引擎 sensitive.py                 │
│   原始文本 ──▶ MaskResult{ masked_text,       │
│                PhMap(token→原文), stats }      │
└───────────────┬──────────────────────────────┘
                │
      ┌─────────┴──────────┐   （并行，asyncio.gather）
      ▼                    ▼
┌───────────────┐   ┌───────────────────────────┐
│ 云端链路       │   │ 本地链路（阶段一：草稿）    │
│ cloud_pool    │   │ local_pool                │
│ 输入=脱敏文    │   │ 输入=**原始数据**（不出网）  │
│ 任务=出模板+   │   │ 任务=先用真实数据做草稿      │
│     处理建议   │   │ （首 token 延迟 = 本地延迟） │
└───────┬───────┘   └─────────────┬─────────────┘
        │  云端结果实时注入本地上下文  │
        └──────────────┬────────────┘
                       ▼
        ┌──────────────────────────────────┐
        │ 阶段 B：本地链路（终稿）            │
        │ 输入 = 原始数据 + 云端模板 + 建议   │
        │      + 自己的草稿                  │
        │ 输出 = 终稿（含真实数据）           │
        └──────────────┬───────────────────┘
                       ▼
             rehydrate.py 流式还原（兜底）
                       ▼
         SSE：reasoning_content = 【云端链路】…【本地链路】…
             content          = 终稿
```

### 3.2 双思考链路的呈现

- `reasoning_content` 分两段拼接：`【云端链路】<云端模板与建议摘要>` + `【本地链路】<本地推理>`。
- DSH 原生支持 `reasoning_content`（`models.py::ChoiceMessage` 已有该字段），GUI 会直接展示，**不需要改动 dsh**。
- `auto_mode.show_dual_trace=false` 时只输出本地链路，避免刷屏。

### 3.3 终稿写手

| 取值 | 行为 | 适用 |
|---|---|---|
| `local`（默认） | 本地模型拿真实数据 + 云端模板写终稿；**数据零出网** | 你的原描述 |
| `cloud_then_rehydrate` | 云端基于脱敏骨架写终稿，本地仅做占位符还原 | 本地模型能力不足时的质量兜底 |

### 3.4 计费与审计

- 两条链路分别入账：`caller = "auto:cloud"` / `"auto:local"`，沿用 `pool` 现有结算路径与 `_wrap_key_stream`，**不改结算核心逻辑**（保证 `test_billing_regression.py` 断言路径不变）。
- 审计只留元数据：命中规则 id、占位符数量、是否有 `fail_closed` 触发、各链路耗时与 token 数。**日志绝不落原文**。

---

## 4. 脱敏引擎 `sensitive.py`

### 4.1 输出结构

```python
@dataclass
class MaskResult:
    masked_text: str          # 送云端的文本
    phmap: dict[str, str]     # "⟦PH:1⟧" -> "13800138000" / "123.45万元"
    stats: dict[str, int]     # 规则 id -> 命中数
    fail_reasons: list[str]   # fail_closed 触发原因
```

### 4.2 规则表（配置化，后台可开关）

| 规则 id | 目标 | 说明 |
|---|---|---|
| `mobile` | 手机号 | `1[3-9]\d{9}`，带边界断言 |
| `tel` | 固定电话 | 区号+号码（含 `-`） |
| `email` | 邮箱 | 标准模式 + 边界 |
| `idcard` | 身份证 | 18 位（含 X），带校验位可选 |
| `bankcard` | 银行卡 | 16~19 位连续数字（排除身份证） |
| `uscc` | 统一社会信用代码 | 18 位大写字母+数字 |
| `number_ge2` | **≥2 位数字** | 核心规则，也是误伤最大来源，见 4.3 |
| `percent` | 百分比 | `12%`、`12.5％`、`环比+8%` |
| `money` | 金额 | `￥1234.56`、`1234元`、`12.5万元`、`3亿元` |
| `money_cn` | 大写金额 | `壹佰贰拾叁万肆仟伍佰陆拾柒元捌角玖分` / `…整` |
| `trend` | 环比 / 同比 / 上涨 / 下跌 | 连同比率一起替换，保留方向词 |
| `datetime` | 日期时间 | `2026-09-09`、`2026年9月9日`、`14:30` |
| `netaddr` | IP / 内网地址 | IPv4、`\\host\share`、`http://内网IP` |
| `table_cell` | 表格数字 | 表格模式：表头保留、单元格数值整块替换 |

占位符格式：`⟦PH:n⟧`。选它的理由：不会出现在自然语言与常见代码里、UTF-8 安全、正则匹配无歧义、可以保证"token 前缀唯一"从而支持流式挂起。

### 4.3 结构感知白名单（决定成败的部分）

`number_ge2` 若无差别替换，会改烂 HTML/CSS/JS/版本号/端口/Base64。以下区域**一律不脱敏**：

1. fenced code block（```lang … ```）与 inline code（`` `…` ``）
2. HTML/XML 标签名与属性名（属性**值**按规则处理）
3. URL、文件路径（`C:\…`、`\\server\share`、`/usr/local/…`）
4. 语义化 ID / 版本号（`v1.2.3`、`1.2.3-rc.8`、`sha256`）
5. 单字符数字（`第1项`、`图2`）与序数词序号（1. 2. 3. 列表序号）
6. 已存在的占位符本身
7. JSON key 名（只处理 value）

表格模式：识别 Markdown 表格 / CSV / TSV / TSV-like 制表符块 → 表头整行保留、数据行中的数值列替换为占位符（保留列结构，云端看到的是"结构 + 类型"，而非数值）。

### 4.4 可切换的脱敏强度

| 档位 | 行为 |
|---|---|
| `strict` | 全部规则开启（默认） |
| `balanced` | 关闭 `number_ge2`，仅处理手机号/邮箱/证件/金额/表格 |
| `loose` | 仅处理手机号/邮箱/证件/银行卡 |

会话级覆盖：请求体 `metadata.auto_mode = { strength, disable_rules }`（需要在 `ChatCompletionRequest` 增加 `metadata: Optional[dict]`，见 §8.3）。

### 4.5 编号一致性

同一实体的重复出现映射到同一 token（`13800138000` 在任何位置都是 `⟦PH:1⟧`），保证云端看到的文本内部自洽（例如"总量与各项之和相等"这类关系仍然成立）。

---

## 5. 流式还原 `rehydrate.py`

### 5.1 问题

SSE chunk 会把占位符切断：`…⟦PH:` + `1⟧…`。天真 `str.replace` 会漏还原或还原错位。

### 5.2 方案：带尾缓冲的状态机

- 维护 `pending` 缓冲；每次追加新 chunk 后，用**最长可能前缀**判断尾部是否为某个 token 的前缀（`⟦`、`⟦P`、`⟦PH`、`⟦PH:1`…）。
- 若是前缀 → 该部分留在缓冲，不输出；否则按 `PhMap` 还原后输出。
- 缓冲上限 `auto_mode.restore_tail_guard`（默认 256 字符）：超过则强制冲出，避免长尾卡顿。
- 单位衔接：`⟦PH:1⟧元` → `123.45万元`（token 内部已含单位），避免出现 `123.45万元元`。

### 5.3 单测必覆盖

1. token 被切成 2~3 段
2. 一行里多个 token 连续
3. token 紧邻中文标点、括号、单位
4. 缓冲超限强制冲出
5. `finish_reason=length` 中断时缓冲的正确收尾

---

## 6. 双链路编排 `auto_route.py`

### 6.1 接口

```python
async def run_auto_stream(req, *, cloud_pool, local_pool, cfg, caller) -> AsyncIterator[str]
async def run_auto_once(req, *, cloud_pool, local_pool, cfg, caller) -> ChatCompletionResponse
```

在 `main.py::_chat_handler` 中插入：

```python
if pool_name == "auto" and auto_cfg.enabled and auto_cfg.enabled_for_pools_hit(pool_name):
    return await run_auto_stream(...)   # 流式
    # 或 return await run_auto_once(...) # 非流式
```

### 6.2 阶段编排细节

- **并行发起**：`asyncio.create_task` 同时发云端（脱敏文 + "输出模板与处理建议"指令）与本地（原始数据 + 草稿指令）。
- **实时注入**：云端流每收到一段就追加进本地阶段二的 messages（本地阶段二在 `strategy=two_pass` 下等待云端完成或到达 `cloud_first_token_timeout`）。
- **等待唤醒**（对应你描述的"本地思考后等待云端返回后继续"）：`strategy=two_pass` 显式实现"本地草稿完成 → 挂起 → 云端到达 → 唤醒继续"；`strategy=parallel_draft` 则本地先出草稿、云端到达后追加修正。
- **超时与降级**：
  - 云端超时 → 记录审计，仅本地直答（**不把原文补发云端**）；
  - 本地不可用 → **fail_closed**，返回明确错误，绝不回退到"云端收原文"；
  - 两者都不可用 → 沿用现有 `failure_detail` 报错格式。

### 6.3 SSE 事件顺序

```
data: {"choices":[{"delta":{"reasoning_content":"【云端链路】…"}}]}
data: {"choices":[{"delta":{"reasoning_content":"【本地链路】…"}}]}
data: {"choices":[{"delta":{"content":"终稿分片…"}}]}   ← 已过 rehydrate
data: {"usage":{...}}、data: [DONE]
```

Anthropic 路径（`/v1/messages`）经 `openai_sse_to_anthropic` 自动转换，无需另写一套。

---

## 7. 配置与后台

### 7.1 `config.json` 新增段

```json
"auto_mode": {
  "enabled": true,
  "enabled_for_pools": ["auto"],
  "cloud_pool": "auto",
  "local_pool": "local105",
  "final_writer": "local",
  "strategy": "two_pass",
  "strength": "strict",
  "disabled_rules": [],
  "show_dual_trace": true,
  "cloud_first_token_timeout": 20,
  "local_first_token_timeout": 20,
  "restore_tail_guard": 256,
  "fail_closed": true,
  "audit": true
}
```

### 7.2 后台页面（`/admin` 与 `/hfadmin` 共用后端 API）

新增「auto 模式」页：

- 总开关、生效池、云端池 / 本地池选择（下拉读 `/admin/pools`）
- 终稿写手、编排策略、脱敏强度、单规则开关
- **脱敏预览**：左侧粘原文 → 右侧实时显示脱敏文 + 命中规则 + 占位符表（上线前把误伤摆到明面）
- 命中统计：按规则 id 的日/周命中数、fail_closed 次数、云端/本地耗时分布
- 一键"灰名单"：把某些字段名加入 `number_ge2` 白名单

### 7.3 新增/修改端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/PUT | `/admin/auto-mode` | 读写 `auto_mode` 配置 |
| GET | `/admin/auto-mode/rules` | 规则清单与默认值 |
| POST | `/admin/auto-mode/preview` | 传入原文，返回脱敏结果 + 命中明细（**不记入审计统计**） |
| GET | `/admin/auto-mode/stats` | 命中统计与审计摘要 |
| GET | `/updates/manifest.json` | 终端拉取的更新清单（静态托管） |
| POST | `/admin/updates/publish` | 上传 full/patch → 算 sha256 → 生成 manifest → 推送 |
| GET | `/admin/updates/list` | 版本历史与推送记录 |
| POST | `/admin/updates/rollback` | 回滚某版本（改 manifest 指向） |
| POST | `/updates/report` | 终端上报（无需 admin 权限，按 Key 或机器码限流） |

---

## 8. 文件级改动清单

### 8.1 新增

| 文件 | 内容 |
|---|---|
| `sensitive.py` | 规则引擎、`PhMap`、结构感知白名单、表格模式、`MaskResult` |
| `rehydrate.py` | 流式还原状态机（尾缓冲） |
| `auto_route.py` | 两阶段双链路编排、双 trace 拼接、超时与降级 |
| `auto_config.py` | `auto_mode` 段读写、默认值、校验 |
| `tests/test_sensitive.py` | ≥60 例：召回 + 误伤（代码块/URL/版本号/CSS/JSON key） |
| `tests/test_auto_route.py` | mock 云端 + 本地；断言"云端报文 0 原始数字/手机号/邮箱"、还原正确、fail_closed 生效、计费分别入账 |
| `docs/auto模式设计.md` | 设计文档（规则表、占位符协议、失败矩阵） |
| `docs/更新分发协议.md` | 终端更新 manifest / patch / notes / report 协议 |

### 8.2 修改

| 文件 | 改动 |
|---|---|
| `main.py` | `_chat_handler` 插入 auto 分支；更新相关端点挂载（或在 `admin.py`） |
| `models.py` | `ChatCompletionRequest` 增 `metadata: Optional[dict]`（会话级覆盖用） |
| `admin.py` | auto 配置 / 预览 / 统计 / 更新推送端点；`/admin/` 首页加导航 |
| `static/admin.html`、`static/hfadmin.html` | 新增「auto 模式」与「终端更新」两页 |
| `config.json` | 新增 `auto_mode` 段（含注释性字段说明） |

### 8.3 兼容性注意

- `ChatCompletionRequest` 增字段是**向后兼容**的（pydantic 默认忽略未知字段？→ 需实测：当前模型未设 `model_config`，加显式字段最稳）。
- 计费回归：auto 模式走两条链路会产生两条账；必须保证 `_wrap_key_stream` 的用户 Key 计量路径不被绕过（云端链路与本地链路都按各自 token 计）。

---

## 9. 验收标准

| 用例 | 通过条件 |
|---|---|
| 脱敏召回 | `tests/test_sensitive.py` 全绿；手机号/邮箱/身份证/金额/大写金额/环比同比/表格数字全部命中 |
| 脱敏误伤 | 代码块、URL、版本号、CSS、JSON key、列表序号**零改动**（逐字符 diff 断言） |
| 云端零泄漏 | `tests/test_auto_route.py` + 实机抓包：云端请求体内 **0 个原始数字/手机号/邮箱** |
| 双链路可见 | 对话里能看到【云端链路】与【本地链路】两段 reasoning |
| 终稿正确 | 终稿 HTML/表格里的数值与原始数据**逐字段一致**（脚本比对） |
| fail_closed | 手动停掉 `local105` 上游 → 请求返回明确错误，**不出现原文发往云端** |
| 计费不破 | `test_billing_regression.py` 全绿；用户 Key 用量含两条链路 |
| 后台可用 | `/admin/auto-mode/preview` 预览准确；统计页数字与实际命中一致 |
| 更新通道 | `GET /updates/manifest.json` 可访问；`/admin/updates/publish` 上传后 manifest 立即生效 |

---

## 10. 风险与对策

| 风险 | 后果 | 对策 |
|---|---|---|
| `number_ge2` 误伤 | 云端模板失真、代码需求被改烂 | 结构感知白名单 + 强度档位（balanced 关闭该规则）+ 后台上线前预览 |
| 占位符被模型改写 | 还原失败 | 指令里明确"占位符必须原样保留"；还原阶段对近似形式（去掉空格/换全角）做容错匹配 |
| 流式踩断 | 花屏 | 尾缓冲状态机 + 切块单测 |
| 本地模型能力不足 | 终稿质量低 | `final_writer=cloud_then_rehydrate`；本地池可切 `local99`（GLM-4.7-Flash 优先） |
| 双链路延迟叠加 | 用户等待变长 | 阶段一首 token = 本地延迟；阶段二复用已建立的连接与缓存；后台暴露耗时分布 |
| 并发放大 | 一次请求占两条上游配额 | 复用 `pool` 并发闸门；后台加"auto 模式最大并发"开关（超限退化为单链路本地直答） |
| `auto` 池本身含兜底池链路 | 可能把脱敏文再次路由到任意上游 | 云端链路固定用 `cloud_pool`，**禁止 fallback 到含原文的风险路径**；云端指令不含原文 |
| 审计泄露 | 原文落日志 | 审计只存元数据；断言日志中不出现原始 token 值 |

---

## 11. 工作量与顺序

| 阶段 | 内容 | 估算 |
|---|---|---|
| R1 | `sensitive.py` + 单测 + 预览接口 | 1~1.5 天 |
| R2 | `rehydrate.py` + 状态机单测 | 0.5 天 |
| R3 | `auto_route.py` + `main.py` 接入 + 双链路 trace | 1~1.5 天 |
| R4 | 后台页（配置/预览/统计） | 0.5~1 天 |
| R5 | 更新分发端点 + `updates/` 托管 + 上报 | 1 天 |
| R6 | 端到端验收（含抓包证据、计费回归） | 0.5 天 |

合计约 **5~6 天**。与终端侧（M1 起）无文件冲突，**可并行开工**。

---

## 12. 实施前需确认

1. `auto_mode.local_pool` 默认用 `local105`（Qwen3.8-27B 优先），还是 `local99`（GLM-4.7-Flash 优先）？
2. 默认脱敏强度用 `strict` 还是 `balanced`（strict 更安全但更可能误伤代码）？
3. 是否允许我在后台新建生产 Key `xiyue-terminal` 并授予 `auto` 池（现网 Key 曾被记录 auto=403）？
4. 更新包托管目录放在 `model-gateway/updates/`（由网关静态托管）还是你指定的独立 Web 目录？
5. auto 模式是否需要"会话级不脱敏"白名单 Key（例如管理员自用 Key 直连不走脱敏）？
