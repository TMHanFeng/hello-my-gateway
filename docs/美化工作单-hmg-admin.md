# 美化工作单:hmg-admin 第三面板(多页面 · 双主题 · 全响应式)

> 定稿日期:2026-10-13
> 依据:《美化工作单-设计技能运用流程.md》六阶段流程;本单为该流程①定调阶段的产出物
> 锚点品牌:linear.app(`~/.agents/skills/awesome-design-md/design-md/linear.app/DESIGN.md`,token 原样摘录)
> 任务性质:全新界面(非改版),跳过②原型阶段

---

## 0. 一句话总纲

**多页面真路由 + 共享资产单源 + 单 DOM 双主题:六页各一 URL,壳与 token 全共享,白天/夜晚只换 CSS 变量,手机端抽屉+sheet 全适配。**

## 1. 三大架构决策(本界面与 admin/hfadmin 的本质区别)

1. **多页面真路由**:每菜单独立 URL(`/hmg-admin/model|pools|stats|logs|keys|plugins`),可深链/收藏/刷新;`/hmg-admin` → 307 → `/hmg-admin/model`。每页只带本页业务 JS(内联),告别单文件 5000 行。
2. **共享资产单源**:`assets/hmg.css`(token+组件+响应式)与 `assets/hmg.js`(基础设施)六页共用;侧栏壳由 hmg.js `renderShell()` 按 `body[data-page]` 单源注入——终结第三份复制粘贴漂移。
3. **单 DOM 双主题**:`<html data-theme="light|dark">` + CSS 变量,同一界面同一布局仅换色(区别于 admin/hfadmin 两套文件两种界面)。默认跟随 `prefers-color-scheme`,手动切换记忆 `localStorage.hmg_theme`;每页 `<head>` 内联 3 行主题引导脚本防 FOUC。

## 2. 色板 token 定稿

### 2.1 暗色(品牌 token 原样,DESIGN.md `colors:` 段逐值照抄)

| CSS 变量 | 值 | 来源 |
|---|---|---|
| `--canvas` | `#010102` | colors.canvas |
| `--surface-1/2/3/4` | `#0f1011` / `#141516` / `#18191a` / `#191a1b` | colors.surface-1..4 |
| `--hairline` / `--hairline-strong` / `--hairline-tertiary` | `#23252a` / `#34343a` / `#3e3e44` | 同名 |
| `--ink` / `--ink-muted` / `--ink-subtle` / `--ink-tertiary` | `#f7f8f8` / `#d0d6e0` / `#8a8f98` / `#62666d` | 同名 |
| `--accent`(hover/focus) | `#5e6ad2`(`#828fff`/`#5e69d1`) | primary / primary-hover / primary-focus |
| `--success` | `#27a644` | semantic-success |
| `--overlay` | `rgba(0,0,0,.6)` | semantic-overlay(#000000,弹窗遮罩加透明度) |
| `--on-accent` | `#ffffff` | on-primary |

### 2.2 亮色(inverse 系列映射 + 派生,来源逐条标注)

DESIGN.md **未发布产品浅色主题**(Known Gaps 明示;inverse-* 是品牌唯一公开浅色值)。映射:

| CSS 变量 | 值 | 来源 |
|---|---|---|
| `--canvas` | `#ffffff` | inverse-canvas 原样 |
| `--surface-1` / `--surface-2` | `#f5f6f6` / `#f6f7f7` | inverse-surface-1/2 原样 |
| `--surface-3` / `--surface-4` | `#ebecee` / `#e4e5e8` | **派生**:阶梯续两级(下拉/悬停用) |
| `--hairline` / `--hairline-strong` / `--hairline-tertiary` | `#e2e3e5` / `#d0d2d5` / `#c3c5c9` | **派生**(暗色 hairline 的亮度倒数阶) |
| `--ink` | `#000000` | inverse-ink 原样 |
| `--ink-muted` / `--ink-subtle` / `--ink-tertiary` | `#3c3f44` / `#62666d` / `#8a8f98` | **派生**(后两值直接复用暗色 tertiary/subtle,灰阶对称) |
| `--accent` 系 | 与暗色同值 | 品牌未给亮色变体,单点缀色不随主题变 |
| `--success` | `#27a644` | 同左 |
| `--overlay` | `rgba(0,0,0,.32)` | 派生(浅色遮罩减淡) |

### 2.3 语义扩展色(最小必要,DESIGN.md 未覆盖)

管理面板刚需错误/警告/余量三级分级。DESIGN.md 只给 success;Known Gaps 第 4 条承认产品内存在 red/orange/yellow 标签色但 hex 未公开。**扩展项(非品牌 token,工作单如实记录)**:

| CSS 变量 | 值(双主题同值) | 用途 |
|---|---|---|
| `--danger` | `#eb5757` | 错误/熔断/删除确认 |
| `--warning` | `#f2994a` | 警告/余量黄色档/接近限额 |
| `--info` | `#4cb782`? 否→ 用 accent | 处理中提示一律走 accent,不另设蓝 |

余量三级分级:`--success`(充裕) / `--warning`(吃紧) / `--danger`(耗尽),与现有面板语义一致。

## 3. 字体/字号(硬约束:中文系统栈,忽略品牌字体段)

- 字体:`-apple-system, "Segoe UI", "Microsoft YaHei", system-ui, sans-serif`;mono(模型 ID/密钥/耗时):`ui-monospace, "Cascadia Mono", Consolas, monospace`。
- 字号沿用品牌 body 段的**尺寸/字重/行高**:主体 14px/400、次要 caption 12px、按钮 14px/500、页面标题 20px/600(取 subhead 档,标题负字距 -0.2px)、卡片标题 16px/500。中文不施加品牌激进负字距(仅标题 -0.2px)。
- 数字一律 `font-variant-numeric: tabular-nums`(统计/余额/耗时列必需)。

## 4. 间距/圆角/层级(品牌原样)

- 间距:4px 基数,4/8/12/16/24/32;卡片内边距 24px(紧凑列表 16px);按钮 8px×14px;输入框 8px×12px。
- 圆角:卡片 12px(lg)、按钮/输入 8px(md)、徽章 4px(xs)、状态 pill 9999px。**CTA 永不 pill**。
- 层级:表面阶梯+hairline 边框承载,**拒绝阴影**(暗色尤甚);卡片顶缘 1px 微高光(inset 0 1px 0 rgba(255,255,255,.02))可选;焦点环 2px `--accent` 50% 透明度。

## 5. 组件映射(现面板 → Linear 组件 token)

| hmg-admin 组件 | token 规格 |
|---|---|
| 侧栏 | canvas 底,右缘 1px hairline,菜单项 8px×12px,选中=surface-2 提升圆角 md(选中即 surface lift,pricing-tab-selected 语义) |
| 卡片(供应商/模型/池/插件) | feature-card:surface-1 + 1px hairline + 12px 圆角 + 24px 内距;悬停 surface-2 |
| 主按钮 | button-primary:accent 底白字 8px×14px md;hover #828fff;active #5e69d1 |
| 次按钮 | button-secondary:surface-1 底 ink 字 + hairline 边 |
| 危险按钮 | 同 secondary 形制,字/边 `--danger`(语义扩展) |
| 输入框/下拉 | text-input:surface-1 底 md 圆角 8px×12px;焦点环 2px accent 50% |
| 状态徽章 | status-badge:surface-2 底 ink-muted 字 pill 2px×8px 12px 字号 |
| 弹窗 | 遮罩 `--overlay`;面板 surface-1 + hairline + 16px(xl)圆角;移动端 <768 全屏 sheet 底部滑入 |
| 下拉菜单 | surface-3 提升层级 3 档,无阴影 |
| 表格 | changelog-row 语义:canvas 行底 + hairline 底部规则线;宽表横向滚动首列吸附 |

## 6. 响应式(手机版硬需求)

| 断点 | 布局 |
|---|---|
| ≥1200 | 侧栏 220px 常驻,内容区 max-width 1440 居中,卡片网格 auto-fill≥280px |
| 768–1199 | 侧栏折叠为 56px 图标条(悬停/点击展开),网格 ≥240px |
| <768 | 侧栏→顶部 52px 紧凑条(汉堡+页名+主题切换),点汉堡滑出抽屉(遮罩+280px 面板);网格单列;弹窗全屏 sheet、编辑弹窗五分区手风琴;宽表横向滚动;触控目标 ≥40px(按钮最小高 40px) |

## 7. 动效清单(CSS-only,尊重 prefers-reduced-motion)

1. 首屏入场:内容卡片 staggered fadeInUp 8px,24ms 间隔,180ms ease-out;
2. 悬停/按下:卡片 surface-1→surface-2 背景 120ms;按钮 hover 120ms;无 transform 缩放;
3. 状态切换:主题切换对 background/color/border 150ms 线性过渡(仅首次渲染后启用,防 FOUC 闪烁);弹窗遮罩 150ms 淡入,面板 180ms translateY(12px)→0;移动端抽屉 200ms 滑入;
4. 页签内容重渲不做入场动画(5s 轮询增量刷新会闪)。

## 8. Do / Don't(面板特化)

**Do**:lavender 只用于 选中态/焦点环/主按钮/链接强调,绝不装饰;表面阶梯逐级使用不跳级;状态一律 status-badge;数字 tabular-nums。
**Don't**:不引第三方库/字体/图标文件(图标用 Unicode/内联 SVG);不用纯黑 #000000 当画布(暗)与大面积灰底(亮);CTA 不 pill-round;不搞渐变/聚光卡;不为轮询刷新的内容加入场动画。

## 9. 红线(写死)

1. `static/index.html`、`static/hfadmin.html` **零改动**;后端只新增 hmg_routes.py + paths 常量 + main 一行 include,零行为改动;
2. 旧面板 JS 一概不引用不依赖;`localStorage.gw_key` 只读复用;
3. 轮询统一走 hmg.js pollMgr(visibilitychange 感知+离页清理);
4. 不碰 8650 生产;开发 8651 / 实测 8660 / 套件 8678;
5. API 全部复用 `/admin/*` 现有端点,发现缺能力时先核对 hfadmin 是否同用该端点,绝不新增后端逻辑。

## 10. 验收口径(本任务四道闸)

静态闸(hmg.js+六页内联 JS node 语法 0 失败+套件 270→新增 T-HMG 全绿)/黑盒闸(web-gui-tester 六页全链路+双主题+手机视口)/渲染闸(双主题×桌面/手机四象限截图 visual-judge)/残留闸(git diff 存量文件为空)。上限 3 轮。

---

## 11. 落地实录(2026-10-13 v2.17.0 交付后回填)

- 四道闸实测:**静态闸** node --check 全过+回归 282/282(T40a-l 十二项);**黑盒闸** 六页渲染/添加供应商弹窗/白天主题/手机视口/抽屉全过,keys 用量行窄列断行一处→修复(`.usage-line` 独占整行+数字 nowrap)→复验过;**渲染闸** 10 张四象限截图(`gui-test-screenshots/`,visual-judge 仅适用 pptx/pdf 类,网页按 fallback 逐张目检);**残留闸** git diff:存量仅 paths.py +1/main.py +4(含日志行)/tests +63,admin/hfadmin 零改动。
- 共享层最终归属(比计划多收了三件):分组收纳全套(grp*,跨 model/pools 两页)、成本预估弹窗(cost*,跨 model/stats 两页)、节省统计(hr*,跨 stats/plugins 两页)——凡两页以上要用的都上收 hmg.js,页面只留业务;`HMG.grpReload` 钩子解决共享套件调页面重载的循环依赖。
- IAB 输入管线会话中段失效(三路点击不派发而截图/evaluate 正常):黑盒受阻用例如实记录,机制覆盖=同类已过证据(弹窗组件 T2 已证/主题 localStorage 路径已证);渲染闸布防态(预置 localStorage/类名)已在报告标注。
- 命名冲突教训:hmg.js 初版把千分位命名 `hmgJsq`,与 hfadmin `jsq`(引号转义)撞名——移植代码大量 `jsq()` 直呼,共享层必须保持旧名同义(千分位改名 `hmgNum`);移植前先盘点旧面板全局函数名,共享层同名同义、异名让路。
- api 契约:旧面板 `api()` 失败返回 null(不抛),移植代码满篇 `if (!ok) return;`——共享层照抄该契约(401/网络异常/非 2xx 一律 null),否则 throw 会从每个移植函数里穿出去。
