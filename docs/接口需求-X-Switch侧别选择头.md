# 接口需求：/v1 支持 X-Switch 侧别选择头（供 switch_pool 插件扩展）

> 提出方：DSH 脱敏网关（dsh-plugin-redact-gateway）
> 背景：DSH 的 openai-completions 适配器 URL 恒为 `baseURL + /chat/completions`，无法调用单段路由 `POST /{池名}`；query 参数也无法承载。但适配器**支持 provider 级自定义请求头**（已验证 `llm-pi-ai` config `headers: z.dict(z.string())`，随请求发出）。
> 需求：让 DSH 能按策略（net/local 模式）为同一池指定侧别，语义与现有 `POST /{池名}?switch=` 完全一致。

---

## 需求内容

在 `/v1/chat/completions` 上支持**可选请求头**：

```
X-Switch: local    # 或 net（大小写不敏感，首尾空格忽略）
```

**生效条件**：`model` 字段命中一个开启了「🔀 Switch 切换」的池名（如 `default`）。

**行为**：与 `POST /{池名}?switch=` 完全一致——
- 只路由到该池对应侧（含 `pool:` 子池穿透），**绝不静默跨侧**（该侧全不可用 → 503）；
- 缺省该头时保持现 `/v1` 行为（不区分侧别自由选模），完全向后兼容；
- 鉴权同 `/v1`（Key 需对该池有授权）；
- 响应 `model` 字段 = 实际命中模型接口名（调用方核对侧别用）。

**错误语义**：沿用 Switch 插件速查表（404 池不存在/未启用、403 无权限/池未开 Switch、422 头值非法、429 限额、503 该侧无候选）。

## 为什么用头而不是 body/query

- body 附加字段会被 DSH 适配器按已知字段白名单构造请求体，无法保证透传；
- query 无法通过 baseURL 携带（会被 SDK 拼接破坏，T-GW 已实测）；
- 请求头是 DSH provider 配置原生支持且稳定透传的唯一通道。

## DSH 侧将这样使用（中转站实现后即插即用）

```yaml
# settings.yaml 两个 provider，同一池、同一 Key，仅头不同：
default-net:
  baseURL: http://192.168.5.106:8650/v1
  headers: { X-Switch: net }
  models: [{ id: default }]
default-local:            # 白名单成员（真本地侧才可进）
  baseURL: http://192.168.5.106:8650/v1
  headers: { X-Switch: local }
  models: [{ id: local256, contextWindow: 262144 }]
```

当前过渡期 DSH 已直接使用稳定池名（netmodel=云端侧 / local256=本地侧），头到位后无需再改 DSH 也能立即受益（头为池路由提供显式侧别保障）；若要收敛为单一 `default` 模型名，仅需把两个 provider 的 model id 都改为 `default`。

## 验收清单（中转站实现后）

1. `curl -X POST /v1/chat/completions -H "X-Switch: local" -d '{"model":"default",...}'` → 200，响应 `model`=本地侧模型；
2. `X-Switch: net` 同理命中云端侧；
3. 无头 → /v1 原行为不变（回归）；
4. 池未开 Switch 或 Key 无权限 → 403；该侧全不可用 → 503 不跨侧；
5. 调用记录 `requested` 列体现 `switch:local|net`。
