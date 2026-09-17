# 池级 Switch 切换路由说明（v2.13.3+）

为配合 **dsh 脱敏网关插件** 提供的定向路由能力：对开启了「Switch 切换」的模型池，调用方**不走 `/v1`**，而是直接 `POST /{池名}` 并携带 `switch=local|net` 参数，网关把请求定向路由到池内**本地侧**或**云端侧**的模型。

- 典型池组合：**一个本地模型 + 一个云端模型**（如局域网千问 + 火山方舟），dsh 按脱敏策略决定本轮走哪侧。
- **本地/云端标注复用模型的「令牌类型」**：`local` = 令牌类型为 **local** 的模型（v2.12.5 引入的本地模型类型），`net` = 池内**其余所有模型**。无需新增任何标注，存量模型零迁移。
- **语义红线——绝不静默跨侧**：`switch:local` 的请求在本侧全部不可用时返回 503，**不会**滑到云端侧、不会进兜底池、不受"单模型锁定"影响。脱敏场景下"以为在用本地、实际走了云"属于安全事故，宁可失败让调用方重试。

---

## 1️⃣ 管理端配置（两步）

### 步骤一：确认池内模型的侧别标注

模型管理里每个模型的**令牌类型**就是它的侧别：

| 令牌类型 | 侧别 |
|:---|:---|
| `local`（本地） | **local 侧** |
| 其余任意类型（daily / gift / one_time / rolling_5h / request…） | **net 侧** |

已在池内的模型无需任何改动；开启开关前请确认池内**两侧都有**模型（一般是一本地一云端）。

### 步骤二：池卡片开启「🔀 Switch 切换」开关

模型池管理 → 目标池卡片右上角 → 打开「Switch 切换」开关（hfadmin 与 admin 双面板均有）。

开启时服务端立即校验（按本次池内成员，含 `pool:` 子池递归展开）：

- 池内没有 local 侧模型 → `400`："开启 Switch 需要池内含本地模型：请先将模型令牌类型设为 local（本地）再加入池"
- 池内没有 net 侧模型 → `400`："开启 Switch 需要池内含云端模型：当前全部为本地模型（local），请加入至少一个云端模型"
- 存在断裂引用/不存在的模型 → `400` 带具体名单

校验通过后开关即生效（保存即热生效，无需重启）。**关闭开关**则该池拒绝一切切换请求，但池本身照常通过 `/v1` 调用，互不影响。

---

## 2️⃣ 调用方式

```http
POST /{池名}?switch=<local|net>
Authorization: Bearer <API Key>
Content-Type: application/json
```

- **鉴权**：与 `/v1` 完全一致。用户 Key 需要**对该池有授权**（allowed_pools 包含此池名）；管理员 Key / 服务器密钥天然放行。
- **`switch` 参数**：`local` 或 `net`（自动忽略大小写与首尾空格，建议小写）。两种传法任选：
  - **query 参数**（推荐）：`POST /default?switch=local`
  - **body 字段**：`{"switch": "local", "messages": [...]}`
- **请求体**：标准 OpenAI chat completions 格式。`model` 字段**可省略**——即使携带也会被网关改写为池名（池名即 URL 路径）；`stream`、`max_tokens`、`temperature` 等全部照常透传。

**curl 示例（query 传参）**

```bash
curl -X POST "http://127.0.0.1:8650/default?switch=local" \
     -H "Authorization: Bearer mg-xxxxxxxx" \
     -H "Content-Type: application/json" \
     -d '{"messages":[{"role":"user","content":"你好"}]}'
```

**curl 示例（body 传参 + 流式）**

```bash
curl -X POST "http://127.0.0.1:8650/default" \
     -H "Authorization: Bearer mg-xxxxxxxx" \
     -H "Content-Type: application/json" \
     -d '{"switch":"net","stream":true,"messages":[{"role":"user","content":"你好"}]}'
```

**返回 `200`**：标准 OpenAI chat.completion 结构（流式为 SSE，与 `/v1/chat/completions` 一致）：

```json
{
  "id": "chatcmpl-x",
  "object": "chat.completion",
  "model": "qwen3.8-27b-nvfp4",
  "choices": [{"index": 0, "message": {"role": "assistant", "content": "…"}, "finish_reason": "stop"}],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
}
```

> 响应里的 **`model` 字段 = 实际命中的模型接口名**，dsh 侧可借此核对本次请求真实走了哪一侧。

---

## 3️⃣ 路由规则

1. **侧别过滤**：进入选模前，先把池内（含子池）成员按标注分成两侧，只允许请求侧的模型成为候选。
2. **同侧内照常重试**：过滤后按池的既定策略（顺序 / 自动择优 / 负载均衡）选模；第一个候选 429、出错、冷却时，**在同侧内**换下一个继续试（所以池内同侧放多个模型完全可以）。
3. **绝不跨侧**：同侧候选全部不可用 → 直接 `503`（含"该侧没有模型"的明确文案），**不升级兜底池、不受单模型锁定影响**——这两条旧逻辑会绕过侧别过滤，对 switch 请求一律旁路。
4. **计费与限额与 `/v1` 完全一致**：模型侧配额（每日量 / RPM / TPM / 安全阀）、Key 侧限额（daily / rolling_5h / one_time，按 token 或按次）全部照常执行；Headroom 压缩、搜索 AI 总结等按模型配置的功能照常生效。
5. **Anthropic 格式兼容**：带 `anthropic-version` 头的请求体自动识别并转换（与 `/v1/messages` 同一套管线），响应自动转回 Anthropic 格式。

---

## 4️⃣ 错误码

| 状态码 | 场景 | 报错文案示例 |
|:---|:---|:---|
| `400` | 请求体不是合法 JSON | Invalid JSON body |
| `401` | API Key 缺失/错误 | Invalid API key |
| `403` | Key 无该池权限 | 该 API Key 无权访问模型池 'default' |
| `403` | 池未开启 Switch | 模型池 'default' 未开启 Switch 切换，无法按 local/net 定向调用 |
| `404` | 池名不存在（或误把单模型名当路径） | 未知模型池 'xxx'：对外仅可调用模型池，不能直接指定单个模型 |
| `413` | 请求体超 20MB | 请求体过大（上限 20MB） |
| `422` | `switch` 缺失或值不是 local/net | 非法 switch 参数 'cloud'：仅支持 local（本地模型）或 net（云端模型） |
| `429` | Key 用量达限额（与 /v1 一致） | API Key 用量已达限额: … |
| `503` | 该侧模型全部不可用（不跨侧、不兜底） | Switch 切换无候选：池内没有该侧模型… / 所有候选模型均不可用… |

**503 重试语义**：503 多为暂态（同侧模型冷却 5 秒 / 本地引擎切换窗口）。dsh 侧收到 503 后按自身策略延后重试即可；若持续 503 且文案为"池内没有该侧模型"，则是池配置缺侧，需要管理员补模型。

---

## 5️⃣ 调用记录查询

每次切换路由都会照常写入调用决策记录（decision_log），**`requested` 列固定写为 `switch:local` 或 `switch:net`**，一眼可见本次走了哪侧：

- **调用决策弹窗**（hfadmin / admin 用量页 → 调用记录）：该请求的徽标区显示「指定 switch:local」，右侧显示实际命中的模型 `✓ 接口名`；
- **密钥最近调用记录**（密钥编辑 → 用量历史 → 最近调用记录表）：「请求模型」列即 `switch:local` / `switch:net`；
- **接口查询**：`GET /admin/decisions?pool=default&limit=50`（管理密码鉴权），返回行的 `requested` / `selected` 字段分别是切换侧别与实际命中模型。

---

## 6️⃣ dsh 侧对接要点

1. **请求模板**：`POST http://<网关>:{端口}/{池名}?switch={local|net}`，Bearer Key 鉴权，body 为标准 OpenAI messages 结构，`model` 字段不用关心（会被改写）。
2. **核对走向**：读响应 `model` 字段（= 实际命中的模型接口名）；如需程序化对账，用 `GET /admin/decisions` 按池拉取 `requested`/`selected`。
3. **重试语义**：`503` = 本侧暂态不可用，延后重试；`429` = Key 限额触顶；`403/404/422` = 配置或权限问题，重试无意义。
4. **同一 Key 也可照常走 `/v1` 调同一池**（不区分侧别、按池策略自由选模）——两条入口互不影响，便于灰度切换。

---

## 注意事项

1. **池改名**：开关状态随池配置整体迁移，改名后依然生效。
2. **未开启的池**：`POST /{池名}` 会被 `403` 明确拒绝；这些池的行为与旧版本完全一致。
3. **行为变化提示**：v2.13.3 起任意未知的单段 POST 路径（如 `POST /foo`）返回 `404 未知模型池` 的 JSON（此前为 FastAPI 默认 404），状态码不变、语义更明确。
4. **子池**：池内引用 `pool:` 子池时，侧别校验与路由过滤都会递归展开到子池成员。
