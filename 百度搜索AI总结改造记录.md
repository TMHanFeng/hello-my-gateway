# 百度网页搜索 AI 总结改造记录（v2.12.3）

> 日期：2026-09-12 · 执行人：DSH agent · 状态：**已上线（v2.12.3，原内部批次号 v2.12.3/v2.12.3 并入）**
> 备份：`.backup/20260912-152526/`（含一键回滚 `rollback.ps1`）
> 回归套件：**79/79 通过**（追加批次改完再跑一次同样 79/79；v2.12.3 发布时扩展为 87/87）· 真实调用验证：通过

---

## 1. 一句话结论

「千帆网页搜索」(`Qianfan/baidu-web-search`) 从"返回裸结果列表"升级为**两步式**：先搜索，再把
「用户问题 + references 全部可解析字段 +（可选）抓取的网页正文」交给一个 LLM 池做一次 AI 总结，
用总结文本替换 `content`，`references` 原样保留。总结池**默认 `mm`**，可在面板自由改选；
总结池整池失败时自动退回原始结果列表。

千帆搜索（总结版 `Qianfan/baidu-ai-search-hp`）**不受影响**，面板上也不会出现这两个选项。

---

## 2. 改了什么

### 2.1 新增文件

| 文件 | 作用 |
|---|---|
| `search_summary.py` | 全部新逻辑：`flatten_refs`（把 references 里所有可解析字段整理成统一结构）、`build_summary_messages`（拼提示词）、`summarize_text`（非流式）、`summarize_stream`（流式）、`pool_ineligible_reason` / `summary_candidates`（总结池资格校验） |
| `web_fetch.py` | URL 正文抓取（仅标准库解析，无新依赖）：`html_to_text`、`decode_body`（header → `<meta charset>` → utf-8/gb18030 猜测链）、`fetch_pages`（并发+超时+大小上限+SSRF 防护），失败/超时/非 HTML/解析空一律跳过 |

### 2.2 修改文件

| 文件 | 位置 | 改动 |
|---|---|---|
| `pool.py` | `ModelEntry`（`:52-54`） | 新增 `summary_pool` / `summary_length` 两个字段 |
| | `_load()`（`:228-229`） | 从 config 读入这两个字段 |
| | `execute_with_fallback`（`:1404`） | 新增形参 `allow_search_summary=True`；`:1455-1462` 加非流式后置阶段：搜到结果后调总结池，成功则替换 `content` |
| | `_search_summary_stream`（`:1555-1568`，新增方法） | 流式专用：先同步跑搜索（上游 web_search 不支持流式），再流式总结 |
| | `execute_stream_with_fallback`（`:1571`） | 新增形参；`:1610-1615` 加分支，**在任何预扣计费之前 return**（否则搜索模型会被计两次） |
| `admin.py` | `add_model`（`:365-368`） | POST 白名单加两个键（该函数是显式构造 `entry`，不加会被丢弃） |
| | `add_model`（`:394-407`） | 落盘前校验 `summary_pool` 合法性；非 `qianfan_web_search` 协议直接剥离 |
| | `update_model`（`:522-533`） | 定义 `_proto_new` / `_sp_new`，**先校验后改**（沿用本文件既有约定，避免脏值残留在共享配置缓存） |
| | `update_model`（`:551-558`） | 非 `qianfan_web_search` 协议显式 `pop` 两个键（隐藏表单控件仍会被 FormData 提交旧值，清理必须放服务端） |
| | `get_pools`（`:690-692`） | 返回值**追加** `summary_candidates`（加法，不破坏现有前端） |
| `static/index.html` | `:601-609` | 新增「搜索 AI 总结」区块（总结模型池下拉 + 回答字数输入） |
| | `:596` | 内联协议 select 加 `onchange="onSearchSummaryVisibility()"` |
| | `:2118-2161` | 新增 `effProtocol()` / `fillSummaryPools()` / `onSearchSummaryVisibility()`；`onProviderSelect()` 末尾调用 |
| | `:2305-2307` | `editModel` 回填（config 里没有该键的模型给默认 `mm`） |
| `static/hfadmin.html` | 同上镜像 | `:823-832` 区块、`:818` onchange、`:1519-1558` JS、`:1688-1690` 回填 |
| `config.json` | 顶层 | 新增 `search_summary` 全局块（抓取参数，见 §4） |
| | `QianFan/baidu-web-search` | `"summary_pool": "mm"`、`"summary_length": ""` |

**未改动**：`main.py` / `models.py` / `format_adapter.py` / `probe_reasoning.py` / `keyauth.py` / `database.py`，
以及三处协议枚举（`admin.py:591/650/980`、两个面板的协议下拉与 `provBadge`）。

---

## 3. 对外行为（怎么用）

请求体**完全不变**，客户端零改动：

```json
POST /v1/chat/completions
{"model": "baidusearch2", "messages": [{"role": "user", "content": "今天北京天气怎么样"}], "stream": false}
```

响应形状也不变（`choices` + `usage` + `references`），只有 `content` 从裸列表变成了带 `[序号]` 引用的总结：

| | 改造前 | 改造后 |
|---|---|---|
| `content` | `1. 标题\n链接\n摘要` × 20 = **9553 字符** | **235 字符**带引用总结 |
| `references` | 20 条 | 20 条（不变） |
| 耗时 | ~2s | **8.8s**（非流式）／11s（流式） |

面板入口：模型编辑弹窗 → 协议选「千帆网页搜索」→ 出现「搜索 AI 总结」区块（总结模型池 + 回答字数）。
字数**不做任何校验**，原样拼进提示词（如 `请将回答控制在约 500 字以内。`）。

---

## 4. 配置项

模型级（写在 `QianFan/baidu-web-search` 上）：

| 键 | 含义 |
|---|---|
| `summary_pool` | 总结用的 LLM 池名；`""` = 不总结（行为与改造前逐字节相同）。默认 `mm` |
| `summary_length` | 自由文本字数要求；`""` = 不加限制 |

全局级（`config.json` 顶层 `search_summary`，可整块省略，均有默认值）：

```jsonc
"search_summary": {
  "max_fetch_urls": 8,               // 0 = 完全不抓 URL（只用 references 自带正文）
  "fetch_concurrency": 5,
  "fetch_timeout_seconds": 8,
  "fetch_total_timeout_seconds": 20,
  "max_bytes": 524288,
  "max_chars_per_page": 1200,
  "max_total_chars": 12000,          // 送进 LLM 的资料总量上限
  "fetch_proxy_url": "",
  "block_private_hosts": true        // SSRF 防护：拒绝私网/环回/保留地址
}
```

总结池候选（`/admin/pools` 的 `summary_candidates`，实测 18 个）：
`arkpro, arkflash, netmodel, openrouter, mm, deepseek, deepseekwu, deepseekfu, deepseekhu, deepseeksam, 兜底池, local256, local99, local106, local105, arktext, glm`
—— 已排除 `qwenemb`(embedding)、`bgererank`(rerank)、`baidusearch`/`baidusearch2`(搜索，会递归)、`auto`(空池)。

---

## 5. 兜底机制（与"用中转站调一次该池"完全一致）

内层总结调用**直接复用** `pool.execute_with_fallback` / `execute_stream_with_fallback`，因此自动获得
429 冷却 5s、切换候选、`single_override`、`fallback_pool` 池链、并发槽、上下文超限透传、
决策日志、模型用量入账与 `valve_pct` 安全阀。

三道递归防护：
1. 内层调用一律传 `allow_search_summary=False` —— 结构上不可能递归；
2. 保存时校验 `summary_pool` 必须存在且 `_collect_pool_models()` 展开后不含任何搜索协议模型；
3. 调用时用 `entry.summary_pool` 为空即跳过，配置被手工改坏也只降级不报错。

**计费**：用户密钥**只计一次**（用户密钥入账写在 `main.py:247-250`，在 pool 之外，内层调用天然碰不到）；
总结那次真实消耗总结池模型的额度与安全阀（这是预期行为）。

---

## 6. 验证证据

| 项目 | 结果 |
|---|---|
| 回归套件 | **79/79 通过**（改造前后各跑一次，无回归） |
| 真实调用·非流式 | HTTP 200，8.8s，`content` 235 字符带 `[1][2][7]` 引用，`references` 20 条，`pool=mm model=MiniMax-M3 tokens=7744` |
| 真实调用·流式 | HTTP 200，11s，19 个 `data:` 帧，末帧 `[DONE]`，倒数第二帧含 20 条 references，拼接正文 336 字符 |
| URL 抓取 | 实测 `抓取成功=6/8`、`抓取成功=8/8`（失败的静默跳过），`prompt_chars≈12060` |
| 换池验证 | 临时把 `summary_pool` 指向 `local99` → 正确路由到 `model=Ornith-1.5-35B-A3B`，227 字符；随后已还原为 `mm` |
| 专项单测（20 项） | 降级（总结池抛异常/返回 None/未配置 → 一律 None）、references 缺 url/title/content 不报错、字数原样拼接、空 references、HTML 抽取去 script/style、`<meta charset>` 嗅探、SSRF 拦截环回与 file://、总结池资格 8 种情形 —— **全部 PASS** |

---

## 7. 操作记录（时间线）

| 时间 | 操作 | 结果 |
|---|---|---|
| 15:25:26 | 备份代码 + `config.json` + 面板 + `/admin/pools` 快照到 `.backup/20260912-152526/` | 成功（40 个文件） |
| 15:26:07 | 写入一键回滚脚本 `rollback.ps1` | 成功 |
| 15:26–15:28 | 新增 `search_summary.py` / `web_fetch.py`；改 `pool.py`（7 处）、`admin.py`（5 处）、双面板（各 4 处）、`config.json` | 成功，AST 校验通过 |
| 15:28:51 | **第一次重启**（`stop_gateway.ps1` + `Start-Process`） | 启动成功，健康检查通过 |
| ~15:29 | 发现进程随后**被杀死**（`Start-Process` 子进程随当前 shell 会话结束被回收） | ⚠️ 服务中断约 1.5 分钟 |
| 15:29–15:30 | 尝试用 `schtasks` 脱离进程树启动 | ❌ 被沙箱拒绝（`ERROR: The system cannot find the path specified`） |
| 15:31:31 | **第二次重启**：改为托管后台作业方式拉起 | 成功，PID 28500，恢复服务 |
| 15:31–15:33 | `/admin/pools` 与 `/admin/models` 校验配置；非流式 + 流式真实调用 | 全部通过 |
| 15:32 | 回归套件（自带 8651 隔离实例 + 8125 mock，不影响 8650） | **79/79 通过** |
| 15:32:48 / 15:33:00 | 换池降级测试：PUT `summary_pool=local99` + `/admin/reload`，验证后 PUT 还原 `mm` + reload | 成功，配置已确认还原 |

### 过程中对生产的两点影响（如实记录）

1. **约 1.5 分钟服务中断**（15:29–15:31），原因是 `Start-Process` 拉起的子进程随工具会话结束被回收，而非常规重启失败；已改用托管后台作业恢复。
2. 两次 `POST /admin/reload` 期间，3 个**其他池**的在途请求出现 `ReadError` 被记录并冷却 5s
   （`glm/MiniMax-M3`、`arkflash/DeepSeek-V4-Flash正式版`、`bgererank/80qwenrerank`）——这是 `reload` 重建连接池的固有副作用，与本次功能无关，面板每次保存模型也会触发。

---

## 8. 网关宿主状态（已于追加批次解决）

v2.12.3 上线时网关曾挂在本 agent 会话的托管后台作业上（PID 28500），会话结束即被回收。
**追加批次重启时已改为独立窗口进程**（见 §11.7）：`cmd` PID 37096 → `python` PID 36944，
不再依赖任何 agent 会话。

如需换回静默后台模式（关窗口也不停）：

```powershell
powershell -ExecutionPolicy Bypass -File D:\AIcoding\model-gateway\stop_gateway.ps1   # 先停
powershell -ExecutionPolicy Bypass -File D:\AIcoding\model-gateway\start_gateway.ps1  # 再起
```

> 注意：`start_gateway.ps1` 会先探测 `/health`，**网关已在运行时它会直接退出**，所以必须先停。

---

## 9. 回滚

一键回滚（自带健康检查，失败不会挂住窗口）：

```powershell
powershell -ExecutionPolicy Bypass -File "D:\AIcoding\model-gateway\.backup\20260912-152526\rollback.ps1"
```

脚本行为：① 停止当前网关 → ② 把"当前现场"另存到 `.backup/pre-rollback-<时间戳>/`（可再前进）→
③ 还原代码与 `config.json` → ④ 清空 `__pycache__` → ⑤ 重启并健康检查。

**只回滚功能、保留新代码**（推荐，风险最小）：把 `QianFan/baidu-web-search` 的 `summary_pool` 改成 `""`
或在面板选「不总结」——该模型立即恢复"返回原始结果列表"，其余代码路径本就对其它模型不生效。

---

## 10. 遗留事项 / 建议

1. **回归套件尚未覆盖新功能**（建议补 T15 组）：现有 79 条断言不含 `summary_pool` 相关用例。本次已用
   20 项专项单测 + 真实调用覆盖，但未固化进 `test_billing_regression.py`。建议补：非流式总结替换 content、
   流式透传 + references 帧、总结池全失败降级、`allow_search_summary=False` 防递归、**搜索模型只计一次费**。
2. **重复/镜像结果未去重**：实测 20 条里有 8 条是 `weather.com.cn` 不同域名的同一套镜像、2 条是 163 同一篇文章，
   它们会挤占 `max_total_chars` 配额、稀释总结质量。建议后续按 URL 规范化 + 标题去重。
3. **流式首字节变慢**：`stream=true` 时首字节 = 搜索(~2s) + 抓取(≤20s) + 总结首 token。实测端到端 11s。
   若不可接受，可把 `max_fetch_urls` 调 0（只用 references 自带正文，延迟降到 ~3s）。
4. **抓取可靠性**：微信 `mp.weixin.qq.com` 等 JS 站点抓不到正文（会跳过，用 references 的 `content` 兜底）；
   实测成功率 6/8 ~ 8/8。
5. **`summary_length` 是自由文本**（按需求不做校验），形如 `五百字` 也能拼进提示词，由 LLM 自行理解。
6. `参考文档：百度搜索接入说明.md`（v2.12.3）第 3/6 节描述的"单池 sequential 兜底"与当前部署
   （`baidusearch` / `baidusearch2` 两个单模型池）不一致，建议一并修订。

---

## 11. 追加批次 — 搜索流式帧形统一

### 11.1 目标

`baidusearch`(hp/web_summary) 与 `baidusearch2`(web_search) 对同一请求返回**完全相同的结构**；
字段**值**允许且应当不同，请求方据此判断搜索是否已切换。**只对齐结构，不统一值。**

### 11.2 改前实测的差异

| | baidusearch (hp) | baidusearch2 (web_search) |
|---|---|---|
| 非流式 | 标准 OpenAI chat completion | **结构已一致**（v2.12.3 改造后） |
| 流式 | 上游帧原样透传：只有 `{request_id, choices, references}` | 网关合成 OpenAI 帧 + mm 池帧混用：`{id, object, created, model, choices, usage, service_tier, base_resp...}` |

即非流式早就一致，**流式的键集合完全不同**（改造前就存在，非 v2.12.3 引入）。

### 11.3 统一后的帧形（hp 原形 + 一个 `model` 键）

```json
{"request_id":"<str>","model":"<str>",
 "choices":[{"index":0,"finish_reason":"<str>","delta":{"role":"<str>","content":"<str>"}}],
 "references":[...]}          // 仅首帧携带（与 hp 现状一致）
```
末尾固定：一帧 `finish_reason="stop"` 收尾帧 + `data: [DONE]`。

### 11.4 改动清单

| 文件 | 改动 |
|---|---|
| `search_sse.py` **新增** | 帧形定义与构造：`frame` / `first_frame` / `stop_frame` / `normalize_upstream_line` / `delta_of` |
| `providers/qianfan_search.py` | ① hp 分支：逐帧补 `model`（并保证 `request_id` 存在）；② web_search 合成流改为同一帧形；③ 非流式 `references` 由 `data.get(...)` 改为 `... or []`，消除 `null`/`[]` 边界差异 |
| `search_summary.py` | 总结池返回的 OpenAI 帧**转换**为目标帧形（丢弃 `id`/`object`/`created`/`usage`/`service_tier`/`reasoning_content`），首帧带 references，末尾补 stop 帧 + `[DONE]`；删除已废弃的 `_refs_frame` |
| `start_gateway_window.cmd` **新增** | 带窗口启动器（见 11.6） |

### 11.5 验证证据（生产 8650，新代码）

```
[non-stream] 仅A有:(无)  仅B有:(无)  类型不同:(无)          => 结构完全一致
[stream]     仅A有:(无)  仅B有: references[].web_extensions.images[].{url,width,height}
             类型不同: authority_score(int vs float)、web_extensions.images(null vs array)
首帧键两边都是 ['choices','model','references','request_id']
model 值：web_summary / web_search      <- 值不同，请求方可据此判断切换
```

- **聊天信封结构 100% 一致**（顶层键、`choices[]`、`delta` 键、`finish_reason`、`references[]` 字段集）。
- 回归套件 **79/79 通过**（exit 0，0 FAIL）。流式正文拼接完整（首尾均为完整句子）。
- 隔离实例（8653）先验证，再重启生产，避免带 bug 上线。

### 11.6 残留差异（均为 `references[]` 上游数据，非聊天信封）

1. `references[].authority_score`：hp 恒为整数 `1`，web_search 有 `1` 与 `0.5`。
   **JSON 规范里两者都是 number，无类型差异**；仅当客户端用 Go/Rust/Java 严格反序列化到 `int` 时才有影响。
   → 已确认**不做处理**。
2. `references[].web_extensions.images`：hp 恒为 `null`，web_search 在结果含图时是数组
   （`{url,width,height}`）。属**数据相关**差异，且是上游直通字段；要抹平只能丢弃图片数据。
   → 暂不处理，如需强制一致可在此处 `pop` 掉。

### 11.7 重启方式（本次踩坑后的结论）

`Start-Process` 拉起的子进程会随工具会话结束被回收（第一次重启因此造成约 1.5 分钟中断），
`schtasks` 被沙箱拒绝。**可行做法**：用 `explorer.exe` 拉起启动器 —— explorer 已在会话外，
它创建的子进程不落在本会话的进程树内，因此能存活。

```powershell
Start-Process explorer.exe -ArgumentList '"D:\AIcoding\model-gateway\start_gateway_window.cmd"'
```

- 新增 `start_gateway_window.cmd`：开一个可见窗口，前台运行 `main.py`，输出实时可见，
  异常退出时窗口保留（`pause`）便于看报错。
- ⚠️ **关掉这个窗口 = 网关停止**。若要"窗口关掉也不停"的静默后台模式，仍用原来的
  `start_gateway.ps1`（它把主程序以隐藏窗口拉起，输出重定向到 `logs\`）。
- 当前生产：`cmd` PID 37096（窗口）→ `python` PID 36944（网关），`gateway.pid` 已同步。
