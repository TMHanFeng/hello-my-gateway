# 终端开发计划 · 喜粤TV运营助手（Electron 壳 + 双 Chromium + 自动更新）

> 项目：`W:\AIcoding\喜粤TV运营助手`（开发树：`D:\AIcoding\model-gateway\xiyue-terminal`，完成后镜像落地到 W:）
> 依赖本体：`D:\deepseek-harness`（dsh `0.1.0-rc.8`）
> 状态：**计划，未实施**
> 配套文档：`中转站开发计划-auto模式与更新分发.md`（中转站侧）

---

## 1. 范围与目标

交付一个**双击安装即用**的 Windows 桌面程序：

- 窗口 = **左 dsh 全功能面板 + 右浏览器**（不是运营后台在左）。
- 浏览器有两个槽：**新版**（现代 Chromium，默认）与 **旧版**（Chromium 77），面板上一个按钮切换。
- dsh（即模型）能通过工具**完整操作右侧浏览器**（含收藏、历史记录搜索、开关标签），但**不能关掉自己**；关光所有窗口后进入空白页。
- **自动更新**：dsh 本体 / 插件 / 浏览器 三部分都能被后台推送热更新；使用中则关闭后生效；更新后弹更新日志。
- 开发版与正式版同一套工程：`scripts/dev.mjs` 热开发 → `scripts/build.ps1` 出安装包 → `scripts/release.ps1` 出更新包与推送。

### 1.1 与中转站的契约（终端依赖，需先冻结）

| 项 | 值 |
|---|---|
| Base URL | `http://192.168.5.106:8650/v1` |
| 默认模型 | `auto`（auto 模式在服务端实现：脱敏 + 云端模板 + 本地终稿） |
| 鉴权 | 首启弹窗输入 API Key → 写入 dsh credentials |
| 更新清单 | `GET /updates/manifest.json`（由中转站托管） |
| 更新包 | `GET /updates/{full,patch,notes}/…` |
| 上报 | `POST /updates/report` |

---

## 2. 现状审计（逐文件读取，非推测）

**结论：骨架 + 断链，当前双击必然启动即崩。**

| 文件 | 状态 | 问题 |
|---|---|---|
| `app/main/index.js`（248 行） | 存在 | 三视图壳（titleView 44px / opsView 左 / chatView 右）：**布局方向与需求相反**；`require('./dsh-host')`、`require('./first-run')`、`require('./config')` **三个模块磁盘上不存在** |
| `app/preload.js`（18 行） | 存在 | contextBridge 已定义（toggle/expand/history/minimize/maximize/close/opsUrl/apikey） |
| `app/renderer/titlebar.html`（55 行） | 存在 | 可渲染的标题栏（🕘/⏸/─/□/✕） |
| `app/package.json` | 存在 | electron ^33、electron-builder ^25、`main: main/index.js`；**无 electron 依赖副本、无 dist 产物** |
| `browser-mcp/src/index.js`（31 行） | 存在 | `import './browser.js' / './tools.js' / './guard.js'` —— **三个文件全部缺失，MCP 100% 起不来** |
| `browser-mcp/node_modules` | 存在 | MCP SDK / express / ajv 等已装（约 12k 文件） |
| `browser-mcp/blocklist.json` | 存在 | 危险操作规则骨架（1 条 disabled 示例） |
| `dsh-profile/{package.json,cordis.patch.yml,README.md}` | 存在 | bundles = dsh-base + dsh-web-app；patch 覆盖 persona、`agent-default-model = localdeliver/deepseeksam`（**需改 auto**）、insert `mcp-browser`（`%MCP_BROWSER_ENTRY%` 占位） |
| `launcher/{*.bat,start.ps1,update.ps1,ensure-profile.ps1}` | 存在 | bat → start.ps1 → update.ps1 → ensure-profile → **`npm install` 后 start electron**（依赖 npm；UNC 路径直接失败） |
| `updates/manifest.json` | 存在 | 仅 `version 0.1.0` + 1 个空 sha256 条目，**无真实更新通道** |
| `scripts/dev.mjs`（36 行） | 存在 | dev 启动（npm install + ensure-profile + `npx electron .`） |
| `docs/` | 5 行 README | **`开发文档.md`、`BUILD.md` 被索引但不存在** |

### 2.1 dsh 本体的可复用事实

| 事实 | 说明 |
|---|---|
| `apps/cli/lib/bin.js` 已构建存在 | 可打包分发；但仓库无随包发布的独立 exe |
| 已有 Node SEA 打包先例 | `scripts/build-exe-for-python-sdk.ts`（`@yao-pkg/pkg@6.21.0 --sea`、Node 24、整树资源白名单 `ASSET_GLOBS`）→ `dsh.exe` 直接复用 |
| profile / 插件分层 | `cordis.patch.yml`（bundle patch → profile patch → home patch → `--patch` overlay），按 `id` 覆盖、`insert:` 追加 → **插件热更新 = 下发 patch + 重启 host** |
| `dsh plugin --profile <name> add <pkg>` | 插件管理转发给 pnpm（需要 pnpm 在 PATH） |
| web 启动参数 | `--host`（**`0.0.0.0` 被显式拒绝**）、`--port`（`0` = 系统分配）、`--no-open`、`--trusted-host` |
| 客户端插件 HMR | 需 `pnpm run dev:web` 常驻重建才生效 → 正式包必须用 `build:web` 产物，不能依赖 HMR |

---

## 3. 架构设计

### 3.1 窗口布局

```
┌──────────────────────────────────────────────────────────────┐
│ 自绘标题栏 44px：喜粤TV运营助手 | 更新状态 | ─  □  ✕          │
├──────────────────────────────────┬───────────────────────────┤
│ 左：DSH 面板（WebContentsView）   │ 右：浏览器槽               │
│  - dsh 原生 Web UI（= 完整功能）  │  ┌─────────────────────┐  │
│  - 会话 / 设置 / 模型选择 / 技能  │  │工具条：新版|旧版 URL ⭐│  │
│  - 文件拖拽 / 附件 / 计划 / 目标  │  ├─────────────────────┤  │
│  - 工具调用可视化（含浏览器工具） │  │ 页面内容             │  │
│                                  │  │（新槽=WebContentsView│  │
│                                  │  │  旧槽=Chromium77 窗口）│  │
│                                  │  └─────────────────────┘  │
├──────────────────────────────────┴───────────────────────────┤
│ 状态栏 24px：dsh 端口 | auto 模式指示 | 更新提示 | Key 状态     │
└──────────────────────────────────────────────────────────────┘
```

- 宽度比例可拖拽（默认左 42% / 右 58%，记忆到 `data/ui.json`）。
- 原 `opsUrl`（运营后台）→ 降级为**浏览器槽的默认首页 + 书签**，不再是独立视图。

### 3.2 为什么旧槽必须是独立可执行文件

Electron 的 Chromium 版本与 Electron 大版本**硬绑定**（Electron 33 = Chromium 130），同一个 Electron 进程内无法换 Chromium。因此：

| 槽 | 实现 | 版本 |
|---|---|---|
| 新版 | 主 Electron 的 `WebContentsView` | Electron 33（Chromium 130），随 dsh/Electron LTS 节奏升级 |
| 旧版 | 随包分发的独立 Chromium | `77.0.3865.90`（77 末版；如需更早可换 `77.0.3840.0`） |

旧槽启动参数：

```
chrome.exe --app=<url> --user-data-dir=<data\legacy-profile>
           --remote-debugging-port=9222 --window-size=W,H --window-position=X,Y
           --disable-features=...（按需） --no-first-run --no-default-browser-check
```

驱动与摆位：主进程用 CDP（`chrome-remote-interface`）驱动，用 `koffi` 调 `SetWindowPos` 把窗口摆进右侧面板区域（必要时 `SetParent` 挂靠为子窗口）。

两个槽的用户数据目录**分开**：`data\modern-profile`（Electron）与 `data\legacy-profile`（Chromium 77），登录态互不污染。

### 3.3 进程与端口

| 进程 | 说明 |
|---|---|
| Electron 主进程 | 壳、桥服务、窗口管理、更新检查 |
| dsh host | `dsh.exe --profile xiyue-tv --port 0 --no-open`（`--port 0` 由系统分配，避免端口冲突；实际端口从 stdout/健康探测取得） |
| Chromium 77（旧槽，按需） | 仅切到旧版时启动，切回新版后保留后台或退出（可配） |
| 桥 HTTP | `127.0.0.1:8651`，每次启动随机 token，仅本机可访问 |
| MCP server | `node <plugins>\browser-mcp\src\index.js`（stdio，由 dsh 拉起） |

> dsh web 的 `--host 0.0.0.0` 被本体显式拒绝，终端只在本机跑，正好契合。

### 3.4 启动时序

```
双击快捷方式 → launcher\stage0.cmd（永不更新）
   └─ 定位 versions\current（junction 失效则回退 previous）
   └─ 有 state\pending.json ? → 应用更新（关闭后生效的那一版）
   └─ 起 versions\current\app\XiYueTV运营助手.exe
        └─ Electron 主进程
             ├─ 读 data\config.json（首启生成：baseUrl=8650/v1, model=auto）
             ├─ 首启 ? → 弹 API Key 输入 → 写 credentials + settings.yaml
             ├─ ensure-profile（把 profile 模板/插件同步到 data\profile）
             ├─ 起 dsh host（等 /health 通过）
             ├─ 起桥服务 + 注册 MCP
             ├─ 建窗：左 dsh 面板 + 右浏览器槽（默认新版）
             └─ 更新检查（异步，不阻塞进窗）
   └─ Electron 退出后：若 pending 已下载 → bootstrap 应用 → 下次启动即新版 + 弹更新日志
```

---

## 4. 安装与更新架构

### 4.1 安装后目录

```
%LOCALAPPDATA%\XiYueOps\
├─ launcher\
│   ├─ stage0.cmd            # 永不更新（chcp + 找 exe + 转交 bootstrap）
│   ├─ bootstrap.ps1         # 会更新：状态机（应用 pending → 健康检查 → 启动 / 回滚）
│   └─ version.json          # 四组件版本戳
├─ versions\
│   ├─ current\              # 生效版本（junction / 目录符号链接）
│   │   ├─ app\              # Electron 壳 + node_modules（含 electron 运行时）
│   │   ├─ dsh\              # dsh.exe（SEA 单文件）+ 默认 profile 模板
│   │   ├─ browser\          # chromium-77\（含 legacy.manifest.json：版本+sha256）
│   │   └─ plugins\          # browser-mcp / sensitive-audit 等插件
│   └─ previous\             # N-1 版本（回滚）
├─ data\                     # 用户态，更新永不覆盖
│   ├─ profile\              # $DSH_HOME 的 profile 实例（settings/credentials/会话）
│   ├─ modern-profile\       # 新槽浏览器 user-data
│   ├─ legacy-profile\       # 旧槽 Chromium 77 user-data
│   ├─ bookmarks.json / history-export\ / downloads\
│   ├─ ui.json               # 面板比例、槽位、状态栏偏好
│   └─ logs\
└─ state\
    ├─ pending.json          # 已下载待生效的版本
    ├─ changelog.json        # 更新后待展示的日志
    └─ push.json             # 上次检查时间、上报机器码
```

**为什么用 `versions/current` + junction**：Windows 上运行中的 exe 不能被覆盖，所以更新必须写进**新目录**再原子切换指针；出问题切回 `previous` 即可（秒级回滚）。

### 4.2 更新协议（终端侧消费，中转站侧托管）

```json
{
  "channel": "stable",
  "version": "1.1.0",
  "minAppVersion": "1.0.0",
  "releaseNotes": "…",
  "components": { "app": "1.1.0", "dsh": "0.1.0-rc.9", "plugins": "1.1.0",
                  "browserModern": "130.0.6723.58", "browserLegacy": "77.0.3865.90" },
  "files":   [{ "path": "…", "sha256": "…", "size": 123 }],
  "patches": [{ "from": "1.0.3", "to": "1.1.0", "url": "…", "sha256": "…", "deletes": [] }]
}
```

策略：

1. **启动检查**：进窗后异步检查，不阻塞。
2. **运行中检查**：每 30 分钟（可配）+ 状态栏手动按钮。
3. **有更新**：后台静默下载 patch（无 patch 则 full）到 `state\stage\<ver>` → sha256 校验 → 写 `pending.json` → 状态栏提示"**关闭后生效**"。**绝不热替换正在运行的文件**。
4. **关闭后**：`bootstrap.ps1` 应用 pending（解压到 `versions\<ver>` → 健康检查 → 原子切 junction → 起新壳）→ 写入 `changelog.json`。
5. **更新日志**：更新后首启弹窗渲染 `notes/<ver>.md`，带"本版本不再提示"。
6. **强制更新**：本机 `< minAppVersion` 时，壳只显示强制更新页（进度 + 重试 + 备用源），未更新不可用。
7. **失败回滚**：健康检查（壳能启动、dsh `/health` 200、桥可用）任一失败 → 切回 `previous` + 上报。
8. **上报**：每次检查与更新结果 `POST /updates/report`（机器码、版本、状态、错误摘要）。

### 4.3 三件更新对象的落地方式

| 对象 | 更新内容 | 生效方式 |
|---|---|---|
| dsh 本体 | `versions/*/dsh/dsh.exe` + 默认 profile 模板 | 切版本后随壳重启 |
| 插件 | `versions/*/plugins/*` + `cordis.patch.yml` 片段（按 id 覆盖 / `insert:` 追加） | 重启 dsh host（壳内"重启内核"按钮 + 下次启动） |
| 浏览器 | `versions/*/browser/chromium-77/*`（新槽浏览器跟随 Electron） | 切版本后下次启动旧槽生效 |

---

## 5. 双 Chromium 槽设计

### 5.1 统一接口

```js
// app/main/browser/index.js  —— 两个槽对上层暴露同一组方法
{
  navigate(url), back(), forward(), reload(),
  listTabs(), newTab(url), switchTab(id), closeTab(id),
  click(sel), type(sel, text), fill(sel, value), press(key),
  scroll(dx, dy), readPage(), extractTable(sel), screenshot(),
  waitFor(sel, timeout),
  bookmarks.add/list/remove/open, history.search/list,
  cookies.export/import, download(url),
  engine()            // 'modern' | 'legacy'
}
```

- `modern.js` 用 Electron `WebContentsView` 原生 API。
- `legacy.js` 用 CDP 命令实现同一组方法（`Page.navigate`、`Runtime.evaluate`、`Input.dispatchMouseEvent`、`Page.captureScreenshot`…）。

### 5.2 槽切换

- 工具条按钮「新版 | 旧版」；默认新版。
- 切换时：新槽 URL 继承到旧槽（可配），两槽各自保留标签与登录态。
- 旧槽启动失败（二进制缺失/版本校验失败/端口占用）→ **报错并停留在新槽**，不用静默降级（避免用户以为在看旧站真实渲染）。
- 状态栏显示当前槽与 Chromium 实际版本（旧槽版本从 CDP `Browser.getVersion` 取，与清单校验比对）。

### 5.3 二进制获取与校验

- 来源：Chromium snapshot 归档（77.0.3865.90）+ sha256，固化到 `browser\legacy.manifest.json`（版本、URL、hash、预期 `Browser.getVersion` 前缀）。
- 构建期下载并校验；运行期启动前再校验一次（防被替换/损坏）。
- 放不进安装包时（体积）→ 首次运行由 bootstrap 下载并校验；清单里标记 `browserLegacy` 组件的 URL 与 hash。

---

## 6. 浏览器控制桥（dsh ↔ Electron）

### 6.1 链路

```
dsh 模型
   └─ MCP 工具调用（mcp__browser__*）
        └─ plugins/browser-mcp（Node stdio server）
             └─ HTTP 127.0.0.1:8651（Xiyue-Bridge-Token: <随机>）
                  └─ Electron 主进程 bridge-server.js
                       ├─ 工具名白名单校验（第二道）
                       ├─ blocklist.json 规则拦截（第三道）
                       └─ 浏览器槽（modern / legacy）
```

为什么不让 MCP 直接连 CDP：① 桥在主进程内，能拿到窗口几何、能拒绝关闭应用；② token 只在本机、只在本会话有效；③ 工具白名单只有一处真源。

### 6.2 工具清单（模型可见）

`navigate / back / forward / reload / list_tabs / new_tab / switch_tab / close_tab / click / type / fill / press / scroll / read_page / extract_table / screenshot / wait_for / download / bookmarks(add·list·remove·open) / history(search·list) / cookies(export·import) / new_window / switch_engine`

**明令不存在**：`quit_browser`、`close_app`、`close_window(app)`、`kill_process`、`shutdown`、`restart_app`。

### 6.3 "不能关掉自己"的三道防线

1. **工具表**：退出类工具根本不注册（模型看不到）。
2. **桥**：`bridge-server.js` 只接受白名单里的方法名，未知方法直接 400 + 审计。
3. **主进程**：窗口 `close` 事件拦截 —— 若关闭来源是桥/模型 → 拒绝并返回错误；用户点 ✕ → 隐藏窗口并把浏览器槽指向 `data\blank.html`（空白页），**dsh host 进程保活**（`/health` 仍 200）。真正的退出只由用户从状态栏菜单或系统托盘显式确认触发。

### 6.4 危险操作拦截（`blocklist.json`）

- 规则形如：`{ id, enabled, match: { urlPattern, text[], role[] }, action: "block", reason }`。
- 命中的点击/提交直接被拒，返回 `blocked + reason`，并写审计（谁、何时、哪个页面、哪条规则）。
- 产品可按实际后台页面（如"审核上线"）逐步填规则，随更新机制下发。

---

## 7. 文件级改动清单

### 7.1 补断链（现在缺失、必须新建）

| 文件 | 内容 |
|---|---|
| `app/main/config.js` | dev / 打包两态配置读取；默认 `baseUrl=http://192.168.5.106:8650/v1`、`model=auto` |
| `app/main/dsh-host.js` | 拉起 dsh（dev 用 `node apps/cli/src/bin.ts --profile`，正式用 `dsh.exe --profile xiyue-tv --port 0 --no-open`）+ 健康探测 + URL 暴露 + 退出收尾（**只随应用退出**） |
| `app/main/first-run.js` | 首启弹 API Key（预填地址/模型）→ 写 credentials + `settings.yaml`（`llm-pi-ai.providers.localdeliver`） |
| `browser-mcp/src/browser.js` | 真控制器（走桥，不直连浏览器） |
| `browser-mcp/src/tools.js` | 工具注册（清单见 §6.2） |
| `browser-mcp/src/guard.js` | `blocklist.json` 规则拦截 + 审计 |

### 7.2 新增

| 文件 | 内容 |
|---|---|
| `app/main/layout.js` | 左 dsh / 右浏览器 / 状态栏 24px；比例拖拽与记忆 |
| `app/main/windows.js` | 主窗 + 槽窗口生命周期 |
| `app/main/browser/index.js` | 双槽统一接口 |
| `app/main/browser/modern.js` | `WebContentsView` 实现 |
| `app/main/browser/legacy.js` | Chromium 77 启动 + CDP 实现 |
| `app/main/browser/cdp.js` | CDP 客户端（含 `Browser.getVersion` 校验） |
| `app/main/browser/win32.js` | `koffi` 取 HWND + `SetWindowPos` / `SetParent` 摆位 |
| `app/main/bridge-server.js` | 127.0.0.1:8651 + 随机 token + 方法白名单 |
| `app/main/updater-client.js` | 检查/下载/校验/pending/changelog 展示 |
| `app/renderer/panel.html` | 浏览器槽工具条（新版\|旧版、URL、⭐、前进后退、刷新） |
| `app/renderer/blank.html` | 关光窗口后的空白页 |
| `app/renderer/update.html` | 更新进度 / 强制更新页 / 更新日志弹窗 |
| `launcher/stage0.cmd` | 永不更新的握手器 |
| `launcher/bootstrap.ps1` | 状态机：pending → 健康检查 → 启动 / 回滚 |
| `installer/setup.iss` | Inno Setup 单文件离线安装包（装完直接拉起） |
| `scripts/pack-dsh.ps1` | dsh `build` + deploy 闭包 + `@yao-pkg/pkg --sea` → `dsh.exe` |
| `scripts/build.ps1` | 一条命令出安装包（app + dsh + browser + plugins） |
| `scripts/release.ps1` | 出版本：全量包 + 增量 patch + `notes/<ver>.md` + manifest 片段 |
| `scripts/upgrade-dsh.ps1` | 升级 dsh（改 ref）→ 兼容性冒烟 → 失败回滚 |
| `browser/legacy.manifest.json` | 旧槽二进制版本/URL/sha256/预期版本前缀 |
| `docs/{开发文档.md,BUILD.md,更新协议.md,浏览器双引擎.md,兼容性矩阵.md}` | 补齐文档（均被 README 索引但当前不存在） |

### 7.3 修改

| 文件 | 改动 |
|---|---|
| `app/main/index.js` | 重写：布局对调、双槽、桥挂载、关闭保护、更新钩子 |
| `app/preload.js` | 补桥/槽切换/更新相关 IPC（保留原有方法） |
| `app/renderer/titlebar.html` | 状态栏与更新提示接入；保留既有按钮 |
| `app/package.json` | 依赖补 `koffi`、`chrome-remote-interface`；补 builder 配置（nsis + dir，`extraResources` 带 browser/plugins/dsh） |
| `dsh-profile/cordis.patch.yml` | `agent-default-model` 改 `auto`；MCP 行注入桥 token/env |
| `dsh-profile/package.json` | bundles 视需要增补插件 bundle |
| `launcher/ensure-profile.ps1` | 改为同步到 `data\profile`，并做版本化比对（不再依赖项目内相对路径） |
| `launcher/update.ps1` | 重写为 §4.2 协议（patch 优先、pending 语义、回滚） |
| `updates/manifest.json` | 由 `scripts/release.ps1` 生成，不再是手写占位 |
| `scripts/dev.mjs` | UNC 检测与指引；dev 直连 dsh 源码；跳过打包步骤 |

---

## 8. 分阶段实施

| 阶段 | 内容 | 验收 |
|---|---|---|
| M1 | 补断链（config/dsh-host/first-run）+ 布局对调 + 首启弹窗 | `npm run dev` 起得来；左 dsh 右浏览器；输 Key 后能对话 |
| M2 | 双槽浏览器（modern + legacy + 摆位 + 切换） | 新旧槽都能浏览；状态栏显示真实 Chromium 版本；旧槽失败会报错 |
| M3 | 桥 + MCP 全工具 + 关闭保护 | 收藏/历史搜索/开关标签都成功；"退出"类工具不存在；关光窗口进空白页且 host 存活 |
| M4 | 安装包（离线自足） | 干净 Win10/11 双击安装包 → 出完整窗口 → 输 Key → 可用 |
| M5 | 更新通道（manifest/patch/pending/日志/强制更新/回滚） | 后台推 patch → 30 分钟内提示 → 关闭后生效 → 弹日志 |
| M6 | 正式版定型（build/release/upgrade-dsh + 文档 + 兼容性矩阵） | 一条命令出安装包与更新源；文档齐全 |

阶段 M1~M2 约 4~5 天，M3 约 2 天，M4 约 2 天，M5 约 2 天，M6 约 1~2 天 → **合计约 11~13 天**（单人估算）。

---

## 9. 端到端验收用例

**用例 1｜首装即用**：干净 Win10/11 → 双击安装包 → 桌面「喜粤TV运营助手」→ 双击 → 左 dsh 面板（输入框/会话/设置/模型选择，等于完整 dsh）+ 右 Chromium → 弹 API Key → 输入 → 发"你好"得回复。

**用例 2｜auto 模式联调**：输入含真实数据的表格需求 → 服务端双链路返回 → 面板显示【云端链路】/【本地链路】→ 终稿是真实数据（服务端配合抓包证明云端报文 0 真实数字）。

**用例 3｜浏览器操作与自我保护**：让 dsh"把当前页加入收藏、搜一下历史记录里的 XX、关掉这个标签"→ 全成功；再说"关闭浏览器/退出程序"→ 工具不存在或被拒；手动关光所有窗口 → 空白页 + dsh host 存活。

**用例 4｜热更新**：后台推 v1.1 → 终端 30 分钟内提示"关闭后生效" → 关闭再打开已是 v1.1 → 弹更新日志；把 `minAppVersion` 提到 v1.2 → 终端进强制更新页，未更新不可用。

**用例 5｜回滚**：人为把一个坏版本推成 `current`（例如 dsh.exe 损坏）→ bootstrap 健康检查失败 → 自动切回 `previous` → 上报错误 → 程序仍可用。

---

## 10. 风险与对策

| 风险 | 后果 | 对策 |
|---|---|---|
| Electron 无法在 UNC 路径运行 | 你的 W: 是网络共享 | 开发在本地盘；`dev.mjs` 检测 UNC 直接给指引；产物由脚本复制到 W: |
| Chromium 77 官方下载页已下架 | 拿不到二进制 | snapshot 归档 + sha256 + 启动版本校验；首次运行回退下载 |
| 旧槽窗口摆位不稳（DPI/多屏/全屏） | 视觉错位 | 用 `SetWindowPos` 每帧跟随主窗几何；DPI 感知；失败时退化为"独立子窗口"仍可切回 |
| 运行中更新导致文件占用 | 更新失败/损坏 | 只写新目录 + 关闭后切 junction；`pending.json` 显式语义 |
| dsh 升级破坏 profile/patch | 终端起不来 | `upgrade-dsh.ps1` 强制冒烟（profile 加载 + 插件清单 + MCP 工具表 + web 构建）+ 自动回滚 + 升级前快照 `data` |
| 模型误关应用 | 自身被杀 | 三道防线（§6.3）+ 审计 |
| 首启 Key 无 `auto` 池权限 | 首启即 403 | 首启后自检 `/v1/models`；不可用则明确提示管理员（Key 侧由中转站计划处理） |
| dsh SEA 打包缺运行时资源 | 打包成功但启动即崩 | 复用 `ASSET_GLOBS` 白名单策略；打包后强制跑 `dsh.exe --version` + 起 host 冒烟才入库 |
| 安装包体积过大（Electron + Chromium 77 + dsh） | 分发不便 | nsis 压缩；旧槽二进制改为首启下载；`patches` 增量优先 |

---

## 11. 实施前需确认

1. 旧槽 pin `77.0.3865.90`（77 末版）还是 `77.0.3840.0`？
2. 新槽 pin Electron 33（Chromium 130）——是否可接受，还是要更旧的"流行兼容版"？
3. 原 `opsUrl` 作为浏览器默认首页 + 书签——确认？
4. 旧槽失败**报错不回退**——确认？（防静默降级）
5. 旧槽二进制：打进安装包（+~120MB）还是首次运行下载？
6. 强制更新是否允许离线宽限期（例如 3 天内可用旧版）？
7. 更新源是否需要内网备用地址（主源 8650 不可达时切换）？
8. 灰度推送是否需要按机器码名单，还是全量？
9. 关闭窗口 = 隐藏 + 空白页（host 保活），还是真正退出进程？（按你需求 4 应为前者，需确认退出入口放哪里：状态栏菜单 / 托盘）
