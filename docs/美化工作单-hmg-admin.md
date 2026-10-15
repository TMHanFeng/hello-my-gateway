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

## 12. 质感返工实录（2026-10-13 v2.17.1，验收反馈驱动）

**反馈**：v2.17.0 交付后用户判定「太普通——动效呢？精美的卡片呢？还不如 hfadmin」。根因：定调阶段只摘了 DESIGN.md 的**色板 token**，漏掉三样真正决定观感的东西——①Elevation 段的「顶边微高光」（inset 0 1px 0 白 3.5%，品牌"pixel rendered"质感的来源）；②「层级靠表面阶梯抬升、深色拒绝阴影」只做成了静态平铺（没做 hover 抬升）；③动效只挂了几个 180ms keyframe，完全没按 emil-design-eng 的曲线/时长/可中断规范执行。**教训：awesome-design-md 的价值在 components/elevation/do-don't 段，不在 colors: 段；emil 技能必须在写第一行 CSS 前读，不是交付前补。**

**返工清单**（8 文件，全在 hmg-admin 内）：顶边高光+阶梯 hover / 动效 token 三曲线三时长 / 按压 scale(.97) / 弹窗·菜单·toast 改 .on/.off transition 生命周期（出场恒快/防重入/菜单 origin 跟触发源）/ 抽屉 iOS 曲线 / 进度条宽度过渡（轮询数值→平滑动画）/ 入场瀑布 40ms 级差 / 语义 tint 标签四组双主题 token（付费从红降琥珀=信息非错误）/ 删除按钮安静化 / 行操作 hover 显现（触屏恒显）/ 拖拽把手呼吸 / color-scheme / :has(:checked) 选中态 / 空态虚线框。

**验证**：回归 282/282；8660 四象限截图 + DOM 计算样式断言（IAB 捕获管线残影帧三次，均断言证伪后重拍）。实测抓到品牌行换行 bug 一处。

## 13. 高端感返工实录（2026-10-13 v2.17.2，二次验收反馈驱动）

**反馈**：卡片大小不一致/缺鼠标互动/廉价感/**黑太暗损害眼睛**；点名要求多用已装技能。本轮实际加载 `apple-design`（材质层级/排版字距/减透明无障碍）与 `superhuman` 档案（暖黑 ink、sub-default 排版），叠加首轮的 emil/frontend-design。

**核心教训两条**：
1. **awesome-design-md 的暗色 token 必须做「营销站→产品面」亮度适配**——`#010102` 是海报黑，只适合 hero 大字排版；高密度工具界面长时间注视必然眼疲劳。修法：保色相（靛蓝倾向/lavender 同族）提亮度（canvas #010102→#101116，surface 全梯上移）。v2.17.1 的"token 原样摘录"纪律对**画布亮度**不适用，工作单如实改锚。
2. **「不要位移不加投影」的 Linear 营销站戒律在产品面要放开**——用户明确的"缺鼠标互动/廉价感"正来自静态 hover。修法：小卡片浮起三件套（位移+阴影绽放+描边）+ 全卡片辉光发丝线 + 按钮上浮辉光；大容器只描边不位移（大片位移晃眼）。apple-design 的「材质即层级」补上：侧栏/顶栏半透明模糊、内容下滑过、reduced-transparency 退实心。

**其余**：stats 同行等高（stretch + flex 纵排 + bar margin-top:auto 贴底）；emoji 图标全量换线性 SVG（廉价感主因，弹窗标题保留作个性）；12px 小字 +.01em 正字距（apple：字距随字号，never one value for all sizes）。回归 282/282（T40k 画布锚点同步）。

## 14. 四线体检实录（2026-10-13 v2.17.3，对齐/功能/美观/性能主动巡查）

- **教训（等高修复要枚举全部网格容器）**：v2.17.2 修「卡片大小不一致」只改了平铺 `.stat-grid`，漏了分组视图的真实渲染容器 `.stat-group-body`——同一批卡片有**多条渲染路径**（平铺/分组/外层系统组嵌套），修样式问题时必须 `grep grid-template-columns` 枚举全页容器逐个核对，不能只修截图上看到的那条路径。修复后实测 17 行 0 参差 + 进度条贴底对齐；T40g 以整行锚点防回退。
- **鉴别法（IAB 遮挡节流冻结动画时钟）**：带 transition 的属性计算样式会冻结在过渡起始值（76 个 hmgIn 钉在 t=0 为铁证），造成「主题切换不变色」假bug。三步鉴别：①无 transition 的元素（body）计算值即时正确 → 级联没坏；②`document.getAnimations()` 大量 running 且 currentTime=0 → 时钟冻结；③`getAnimations().forEach(a=>a.finish())` 后断言终态全部正确 → 环境问题结案。不要把计算样式冻结当产品 bug 修。
- **其余四线基线（供后续回归参考）**：DCL 80ms；共享资产 gzip 后 10.3K/17.8K；六页零裸 setInterval；跨页按钮 27px 是 `.btn-sm` 刻意紧凑档非漂移；移动端弹窗实测 380/390 宽是布局视口（innerWidth 含 10px 经典滚动条），真机 overlay 滚动条即全宽——测量时对比 `clientWidth` 而非 `innerWidth`。

## 15. 体验丰富化实录（2026-10-13 v2.17.4，三次反馈驱动）

- **「换行打乱布局」的正确诊断**：DOM 审计（scrollWidth/scrollHeight 对比）先证明**没有裁剪与撑破**——病灶是换行改变元素高度、打乱行内节奏（长名折行把同行卡片的数字顶到三个高度）。修法是「单行截断 + title 保全量」而非 overflow-wrap。判据：修后 0 垂直折行（scrollHeight==clientHeight）+ 同行 .stat-nums y 坐标一致。
- **颜色丰富化的克制解**：不引入新装饰色，而是给**实体**发身份色（Linear label 色系 8 相位，名字哈希恒定）——色即是信息（扫读锚点/族类分组），不是壁纸。同供应商族天然同色是哈希的意外红利。
- **「缺少按钮」的真相**：按钮早就在（.btn-ghost），但透明底无边框读作裸文本——可见性 bug 而非缺失。全局 wash 底+发丝边一根 CSS 规则解决。
- **动效全部「状态指示」目的**（emil 框架）：骨架屏（首载 rare）、live-dot（轮询活性）、busy 旋转（异步反馈）、复制文本回声——没有一个「因为好看」；reduced-motion 全局冻结规则自动覆盖新增动画，无需逐个降级。

## 16. 双主题感知评审实录（2026-10-14，四次反馈驱动）

**反馈**：「暗黑版本黑的太深邃了对比度太高不太舒服，感觉缺少了一点动态的感觉，没有科技动态的感觉」+「对白天也做出评价」。要求用前端 UI 技能评审，**只找毛病、给修改意见**（本轮定位为评审轮，不含大范围改造）。

**评审方法**（与前三轮「截图目检」不同，本轮改用可量化证据链）：

1. **色度实测**——阶梯是否可辨不能看 hex，必须看 **CIE L\***（感知均匀标度）；文字是否过刺激必须看 **WCAG 对比度**；
2. **代码审计**——`grep` 核 token 是否真被定义、颜色来自 token 还是硬编码；
3. **网络取证**——`/admin/*` 请求数为 **0**，证明「卡片空白」不是渲染问题而是**请求从未发出**；
4. **双主题实拍**——暗色 + 亮色各一轮，桌面 + 移动两种视口。

### 16.1 教训：评审期间仓库被并发改动，结论落笔前必须重测

本轮 10:22 读到的暗色 token 是 `canvas #101116` / `ink #f4f5f9` / 峰值 **17.31:1**；12:03 复核时已被 **v2.17.6~v2.17.10**（作者 hanfeng2025，另一条线）改成 `canvas #161823` / `ink #e9ebf3` / 峰值 **14.84:1** —— **暗色主问题已被解决**。若不复核就会输出一个已经不成立的结论。

**判据（可复用）**：写实录前 `git log -5` + 重算 token；异常信号是 `git status` 空但 HEAD 版本含本轮改动的特征串（本轮即如此，本轮改动已被并发提交链纳入 HEAD）。

**次级教训**：v2.17.7 加的 44px 细网格是**真实产物**，我一度误判为 JPEG 压缩伪影——**深色大面积上的网格必须先用 DOM 断言（backgroundImage 层数）核实再下结论**，肉眼判断在低码率截图上不可靠。

### 16.2 本轮已改（共享层 2 文件，六页业务代码零改动）

**鉴权链路单点故障（最高优先级修复）**

| 改动 | 作用 |
|---|---|
| `hmgGetKey()` 只读 localStorage，无 key 返回 `null` | 数据加载路径彻底不再调用原生 `prompt()` |
| `hmgApi()` 入口短路 | 无密码**不发请求**、提示每次会话只弹一次 |
| `hmgApi()` fetch 失败文案 | 「网络异常：X」→「**无法连接网关**：X」，归因终于正确 |
| `hmgChangeKey()` 改用 `HMG.prompt` | 走自建模态框，禁用原生 prompt 的环境（Electron / 内嵌 WebView）可用 |
| `hmgChangeKey()` 取消分支 | 断掉「点取消 → 每 5 秒弹一次原生框」的死循环 |
| `hmgToast()` 同文案去重 + 续命 | 接口挂掉时不再被同款提示淹没 |
| 新增 `hmgHasKey()` / `hmgKeyPill()` | 侧栏 pill 实时显示「设置密码 / 管理密码」+ 琥珀警示态 |

**根因**：`hmgHeaders()` 原先在 `fetch` 的 **try 内部求值**，`prompt()` 的异常被「网络异常」catch 接住 → fetch 从未执行 → 六页卡片全空。**一条本地鉴权异常伪装成网络故障，故障域覆盖整个产品。**

**`var(--ok)` → `var(--success)`**：`live-dot` 引用的 `--ok` **全文件从未定义**（只有 `--success` / `--ok-ink` / `--ok-tint`），CSS 声明失效导致背景透明——全系统唯一「不依赖用户操作、持续表达服务在跑」的信号**出生即坏**，只剩一圈绿色残影。修复后已验证可见。

### 16.3 亮色主题：唯一未被触及的塌陷（待修，优先级最高）

**`surface-1` 与 `surface-2` 是同一个颜色**：

```
surface-1 #f5f6f6  L*=96.81
surface-2 #f6f7f7  L*=97.16    ΔL*=+0.35
两者互相比 = 1.009 : 1        （1.00 = 完全同色）
```

而 `.card`=surface-1、`.nav-item.on`=surface-2、`.badge`=surface-2、`.btn-secondary`=surface-2 —— **卡片底、侧栏选中、徽章底、次按钮全是同一个颜色**，选中态在亮色下等于没有。暗色下这三色有 ΔL\*3.8 的区分，亮色下区分完全消失。

其余（v2.17.9 只加了大气层，色板一档未动）：

| 项目 | 实测 | 判定 |
|---|---|---|
| `ink` / `canvas` | 17.91:1 | 高于暗色 |
| `hairline` / `surface-1` | **1.19:1** | 卡片边框几乎不可见 |
| `--shadow-card` | `rgba(22,23,27,.05)/.07` | 白底上等于没有 |
| `ink-tertiary` 全线 | canvas 3.25 / s1 3.00 / s3 2.75 / s4 **2.58** | **AA 全线不达标，比暗色更差** |

**两条深度线索（边框 + 阴影）同时失效**，这是亮色「平」的直接原因，比色板数值本身更致命。

**建议值**：`surface-2` → `#eceef1`（ΔL\* 0.35 → 3.18，单值修复塌陷）· `surface-3/4` → `#e4e7ec`/`#dcdfe6` · `ink-subtle` → `#5a5f6a` · `ink-tertiary` → `#6b707c` · `hairline` → `#d5d9e0` · `hairline-strong` → `#bfc5cf` · 阴影透明度 `.05/.07` → `.06/.10`。

### 16.4 暗色主题（已被 v2.17.6~10 大幅改善，残留项）

| 指标 | 改前 | **现状** | 判定 |
|---|---|---|---|
| 峰值 `ink`/`canvas` | 17.31:1 | **14.84:1** | ✅ 已达标 |
| `canvas`→`surface-1` 落差 | ΔL\* +3.3 | **+3.98** | ⚠️ 仍偏小（卡片宜 ≥ +6） |
| 阶梯均匀度 | 3.3/3.5/4.0/3.4 | **3.98/3.80/3.91/4.31** | ✅ 已均匀 |
| `hairline`/`surface-1` | 1.07:1 | **1.39:1** | ✅ 已可见 |
| `ink-tertiary` | 3.67:1 | **3.64:1**（s4 上 2.34） | ❌ **未改善，全线不达 AA** |
| `ink-subtle` / s4 | — | **3.91:1** | ❌ 不达 AA |

**残留的真问题**：v2.17.6~10 抬了 canvas 和 surface 阶梯，但 **`ink` 那一端没同步收**——`ink-tertiary` 几乎原封不动（3.67 → 3.64）。而恰恰是这一端在制造对比度问题。`ink-tertiary` 用在 `.hint`、`.empty` 空态、`::placeholder`、版本号上——**最该看清的辅助信息，仍是全界面最难看清的**。

**硬约束**：`ink-tertiary` 即使提到 L\*45 左右，在 surface-4 上也只能到 ~3.7:1。**「AA」与「第三级视觉层级」不可兼得**，正确解法是**规定 `ink-tertiary` 只在 canvas / surface-1 上使用**，抬升表面上改用 `ink-subtle`。该约束双主题通用。

### 16.5 卡片级问题（全部未修）

| 问题 | 实测证据 |
|---|---|
| **徽章语义色滥用** | `typeBadge()` 7 种令牌类型中 **5 种**用 `badge-warn`（一次性/5h限额/全天可用/仅闲时/余额返还制），而 `latTag()` 的**真实告警**「限流/失败」也用 `badge-warn` → **中性属性与真告警同色同形**；单行出现灰/琥珀/蓝/绿四色相 |
| **图标语言割裂** | 侧栏是统一线性 SVG，卡片内是 emoji：**58 处**代理对彩色 emoji（model 22 / plugins 13 / pools 10 / hmg.js 8 / stats 3 / keys 2）+ 若干 BMP 符号 emoji（⏱⚡✏🗑 未计入）。§13「emoji 全量换线性 SVG」**未达成**；且 emoji 跨平台字形不一致 = 排版不确定性 |
| **控件排一字长龙** | 池卡一行平铺 7 个异质控件（2 开关 / 1 输入 / 单位 / 提示 / 2 动作），零分隔零分组；**「Switch」全界面无解释** |
| **主按钮权重倒挂** | 实心紫给了「一键单模型」「+添加模型」等**低频批量操作**，而高频的「编辑」「测速本池」是 ghost |
| **信息优先级倒置** | 池名（内部标识）字号最大最亮，核心元数据「策略 · 共 N 项」是 13px 弱对比；`endpoint id` 用 `--ink-tertiary` + 12.5px mono（不达 AA） |
| **身份色撞相** | `auto` / `arkflash` 首字母均为 A，色相接近而形状尺寸全同 → §15「色即是信息」的扫读锚点在同首字母时失效 |
| **URL 换行打乱节奏** | `https://ark.cn-beijing.volces.com/api/v3` 断成两行且断在连字符后；§15 既定解法「单行截断 + title 保全量」未覆盖此处 |
| **徽章被挤换行致参差** | 同一列表内部分行「文本」徽章被挤到首行末，四行徽章起始 y 不一致（§13/§14 修过的参差问题新形态回归） |
| **原生 select** | `select.inp { appearance: auto; }` 保留系统外观，暗色下箭头/背景不跟随 token，跨平台不可控，且与自定义 `.menu` 风格割裂 |
| **危险操作权重过轻** | 移除成员的 ✕ 与拖拽把手同为 `--ink-tertiary`，且置于行最右（视线需横扫全行） |
| **操作说明占黄金位** | 「负载均衡已开启：每次请求按池内模型顺序轮流分配…」整行加粗带底色条，喧宾夺主，应进 tooltip |

### 16.6 主题切换副作用：汉堡图标被覆盖（待修，一行）

`#hmg-burger` 的 class 误带 `theme-btn`，而 `hmgSetTheme()` 执行 `document.querySelectorAll(".theme-btn").forEach(b => { b.innerHTML = hmgThemeIcon(t); b.title = ... })` → **每切一次主题，三横线菜单图标就被替换成太阳/月亮，tooltip 变成「切换主题」**，而点它实际是开菜单。DOM 实测：`id="hmg-burger" aria-label="菜单" title="切换到夜晚模式"`。修法：汉堡去掉 `theme-btn` 类（它要的只是 ghost 样式）。

### 16.7 动效与「科技感」的再判断

现状动效全部是「入场」或「用户触发」，**没有一个表达「系统在运转」**。§15 的原则（动效必须有明确目的、不为好看而动）方向正确，但**执行到极端就成了静态后台系统**——`frontend-design` 点名的 AI 痕迹之一正是「每卡 fade-and-slide-up」。

建议补的三处，**每一条都有明确目的、非装饰**：

1. **轮询数值变化做过渡 + 变化位短暂 tint**——「防止突兀跳变」+ 状态指示。对网关面板而言这是**本体信息**，不是装饰；
2. **`logs` 页吞吐 sparkline 随轮询推进**——这才是这个产品的主题本身，比任何装饰都更有科技感；
3. **画布大气层**——⚠️ **此项 v2.17.7 / v2.17.9 已实现**（漂移层 + 噪点 + 网格 + 极光），不必重做。

**红线不变**：不给每卡加弹跳入场、不给轮询加动画、不加粒子背景。

### 16.8 优先级（本轮建议，未实施）

**第一梯队**：`surface-2`（亮色，单值修复塌陷）· `ink-tertiary` 提色 + 使用范围约束（双主题）· `badge-warn` 只留给限流/失败 · 汉堡去掉 `theme-btn` 类

**第二梯队**：亮色 `hairline` 两档 + 阴影透明度 · `ink-subtle` 双主题各提一档 · emoji 58 处换线性 SVG · 池卡控件排分组 · 主按钮权重下移

**第三梯队**：原生 select 换自定义 · endpoint id 提对比 · 身份色哈希加盐 · URL 单行截断 · 操作说明进 tooltip

**方法论沉淀**：**评审轮的产出必须是可复核的数值，不是观感形容词。**「黑太深」要翻译成「峰值 17.31:1 / 中间调断层 88 个 L\* 单位」；「卡片空白」要翻译成「`/admin/*` 请求数 0，故障域是鉴权而非渲染」。前者可直接定位 token，后者可直接定位 try/catch 边界。

## 17. §16 评审实施实录（2026-10-14，v2.17.12，本线实施）

承接 §16 评审清单逐条核实与实施。**核实时的方法**：每条先在当前树 grep/实测再动手——§16.1 的教训双向适用（评审线要重测，实施线也要重测）。

### 17.1 逐条核实结果（动手前的重测）

| §16 条目 | 重测结论 |
|---|---|
| §16.2 鉴权链路修复 | **已在树且已随早期轮次入库**（`git log -S hmgHasKey`：v2.17.4 引入——他线写在工作区后被本线整文件提交收编）；live-dot 已用 `--success`。本轮零改动 |
| §16.3 亮色塌陷 | 成立（s1/s2 = 1.01:1）；hairline 1.19:1、阴影 .05/.07、ink-tertiary 3.0:1 全部成立 |
| §16.4 暗色残留 | 成立（ink-tertiary/s1 = 3.34:1、ink-subtle/s4 = 3.9:1） |
| §16.5 卡片级问题 | badge-warn 滥用成立（另发现两处评审漏记：池成员「付费」用 badge-err 红色、「子池/专属池」用 badge-warn）；emoji 58 处普查一致；池卡控件一字长龙成立；「endpoint id 用 ink-tertiary」**已过时**（现继承 --ink 高对比，跳过） |
| §16.6 汉堡 | 成立（`hmg-burger` 带 theme-btn，切主题即被换图标） |

### 17.2 本轮已实施（8 文件：hmg.css/hmg.js + model/pools/logs/keys/plugins 五页 + tests/README）

**第一梯队（全做）**
- 亮色 `surface-2` #f6f7f7→**#eceef1**，surface-3/4→#e4e7ec/#dcdfe6（全梯 ΔL*≈2.5-2.8 均匀）；hairline 三档 #d5d9e0/#bfc5cf/#aab1bf（tertiary 反超 strong 的断梯顺修）；阴影 .05/.07→**.06/.10**
- 双主题 ink 提色 + **使用面约束**：暗 ink-subtle→#9ea4bb（s4 上 4.6:1 AA）、ink-tertiary→#7e859c（s1 4.4:1）；亮 ink-subtle→#5a5f6a、ink-tertiary→#6b707c（s1 4.6:1）；约束「tertiary 只许 canvas/surface-1」写入 token 注释，违规点 `.rsn-neutral` 改 ink-subtle
- badge-warn 只留真告警：typeBadge 4 类/freeBadge「付费」/池「子池」「专属池」全部素徽章（说明入 title）；「付费」badge-err→素徽章
- 汉堡去 `theme-btn`（§16.6 一行修），title「菜单」；切主题 N 次图标不换（IAB 实测）

**第二梯队**
- emoji→线性 SVG：**58 处代理对普查后的可见面全清**——按钮（⚡🔍🗑🛠⚙📥🔄🔒🔓）换 HMG_ICONS 新 9 字形（bolt/shield/trash/refresh/download/wrench/lock/unlock/search），徽章（🛡️代理🔀Switch已开启）与状态行（🧠×5）去装饰；**弹窗标题按 §13 先例保留作个性，注释不动**；静态 HTML 宿主走新增 `data-icon` 注入器（hmgInjectIcons，保持 HMG_ICONS 单源）；`.btn svg`/`.badge svg`/宿主级尺寸规则配套
- 池卡控件两行分组：`.pool-head-ctls`（上排开关+阈值同质组 / 下排动作组虚线分隔，右对齐）；<768 转左对齐铺满；「Switch」标签补 title
- note-box 降权：整行底色条→左 2px 线静默注（12px/ink-subtle/无底色），完整解释保留在开关 title
- 主按钮权重：供应商卡内「＋添加模型」与「一键单模型」btn-primary→btn-secondary（页头主 CTA 唯一实心紫保留）

**第三梯队（廉价项顺做）**
- 原生 select 自绘：`appearance:none` + data-URI chevron（中灰双主题通用）+ 右 30px 预留
- 危险操作显险：新增 `.btn-ghost.danger-hover:hover`（悬停显 err tint/边/字），池成员移除 ✕ 接线
- URL 单行截断：`.card-sub.sub-clip`（clip span + title 保全量，代理徽章不被截）
- 模型行 ID/名称单行截断（§15 解法）：文字先收缩、徽章起始 y 恒一致（修「徽章被挤换行参差」）
- 身份色哈希种子掺长度（`h = str.length` 起步）：同前缀/同尾缀名不再系统性落邻相；8 相位鸽笼碰撞数学上不可免，如实注释

### 17.3 验证

- 回归 287→**289** 全绿（T40r 色板定稿 + T40s 徽章语义/布局分组/emoji 换 SVG/汉堡/哈希/注入器）；node --check 全过
- 8660 双主题×双视口实测：亮 s1/s2 阶梯（冻结动画 finish 后 #f5f6f6/#eceef1 实锤）；暗色汉堡免疫；模型行 nowrap+ellipsis+max-340；池卡 column/flex-end/dashed 分隔/note-box 无底色；select appearance:none+svg chevron；badge-warn 页面计数 0（无真告警时不出现）
- 截图四象限：v2_17_12_{pools_light_desktop, pools_dark_desktop, pools_dark_mobile, model_dark_desktop, model_light_mobile}.png
- §14 冻结伪影两次重现（切主题后元素 bg 读中间值），按既定三步鉴别法处置，未当产品 bug

### 17.4 遗留（如实）

- 头像「同首字母同形」问题本质是鸽笼：种子 reshuffle 降低概率，不做每实体特判
- 弹窗标题 emoji 为 §13 既定的个性豁免位，维持
- 测试端口 8678 套件自管；8650 生产照旧零接触，8660 实测实例纯资产刷新即见

## 18. 舷光与「系统在运转」实施实录（2026-10-14，v2.17.13，本线）

**反馈**：自行检查 + 背景仍单一 + 要科技感（黑白双主题、观看舒适/视觉对齐/易操作）。定调技能：frontend-design（**大胆只花在一处**；非用户触发动效只用于引导注意）+ emil-design-eng（动效必须答出目的；UI <300ms；变了才动）。

**自检结论先行**（动手前 8660 实测）：页头基线 46/46 居中、stat 数字行 147/308/469 齐平——**对齐已稳，不乱动**；单调的病灶=整页无色彩锚点（网格太隐性）+ 5s 轮询数字变化零反馈（§16.7 判断属实）+ i-tip 命中区仅 15px。

**实施**（对齐 §16.7 三建议中未做的两项）：
1. **舷光**（唯一大胆笔触）：暗色 body::before 顶部新增 1100×360 accent 光带（rgba(130,143,255,.12) at 50% -12%），与既有两盏顶辉光叠成「仪表舱顶光」，随 ambientDrift 38s 漂移+透明度呼吸；亮色同位粉彩 .07。暗/亮各 10 层，均在 vignette 透明区，文本对比度不受影响。 restraint：live-dot 已有声呐环（livePulse），不再叠加。
2. **轮询数值变化闪现**：hmg.js `hmgLiveTxt`（变了才触发一次 .live-chg tint 闪现 0.8s；不变零动画、首渲不闪）；stats 页 .stat-used/.stat-pct 接线——5s 轮询的数字跳动首次有视觉反馈。
3. **logs 吞吐 sparkline**：页头 120×26 SVG，48 采样（≈4 分钟）环形缓冲，轮询 push 当前 decisions 数（空窗落 0）；accent 折线+渐变面积+头部呼吸点；<768 隐藏。坑：SVG 表现属性（stop-color/stroke 属性值）不解析 CSS 变量，颜色必须走 style 属性。
4. **命中区扩大**（易操作）：i-tip 15→25px、pq-refresh 22×18→30×26、grp-x 22→30px——透明 ::before 外扩，视觉不变。

**验证**：回归 289→**290** 全绿（T40t）；8660 DOM 断言（spark svg/polyline/头部点、hmgLiveTxt 首渲不闪/变值闪、手机档 spark display:none）+ 四象限截图 v2_17_13_*。

**踩坑**：①README 插条目笔误——v2.17.12 把「### 最新」标题重复一次（replace 锚=标题且 entry 自带标题），本轮顺修并改写法（entry 不带标题）；②IAB clip 截图在 reload 后 3s 内合成出双影帧——整页截图稳定，裁剪截图避开 reload 后窗口；③套件 decision_log 留痕（zz* 测试池的「无可用接口」记录）是既有的 append-only 审计行为，非本轮问题。

---

## 19. v2.17.14 背景每页身份色实录（2026-10-15，v2.17.14，本线）

**反馈**：§17/§18 两轮后用户仍觉「背景还是太单一」（IAB 停在 model 页）。

**诊断**：v2.17.13 氛围层 9 层全挤同一靛蓝色系、alpha .05~.12 接近不可感知，且六页共享同一套颜色——切页零差异是「单一」的另一半来源。

**实施**（纯 hmg.css，§10.6）：
1. `body[data-page]` 六页六组 `--aura-a/b/c` 裸三元组（model 靛蓝默认 / pools 紫 / stats 青 / logs 天蓝 / keys 紫红 / plugins 青绿 0.92×V）；渐变侧 `rgb(var(--aura-x) / α)` 消费。
2. 四层吃变量：舷光（1240×400 at 50% -12%）、20%/86% 顶辉光、body::after 极光团（780×560）、新增 115° 斜向光轴；`html::before` 底角不吃变量——**自定义属性不向上继承，html::before 看不到 body 作用域变量**（选择器陷阱 +1，与 §13 的 `:root 即 html` 同族）。
3. alpha 全局统一：暗 .15/.12/.10、亮 .075/.06/.07。两案否决有实录：等 alpha 原亮色 → 青/青绿峰值 ink-subtle 4.18/3.84 破 AA；加深三元组保 AA → 泥色失身份。定稿=色相全亮+统一 alpha+plugins 单页微降 V。
4. 网格暗 .05/.028 亮 .032/.016、暗角 .46——明暗值域拉开是「非平底」的体感来源。

**AA 证据**：脚本算 WCAG（非目测），两主题×六页×两峰值叠点（舷光核心/左上 glowB+极光簇）ink-subtle 全 ≥4.5:1；tertiary 使用面约束（空态/卡内）本就不进 aura 顶带，不另设阈值。

**回归**：291/291（T40u 新增；T40t 舷光锚点移交；T40m/T40o 色值锚点随现状同步）。8660：六页变量逐页断言 + logs 四层解析取证 + 暗色三页/亮色一页截图身份可辨；主题还原 dark 净零。

**Do / Don't 增补**：
- Do：页面级氛围差异用「一组语义变量（--aura-*）+ 全局统一 alpha」表达——身份来自色相差，不来自亮度差。
- Don't：不要给 html::before 引用 body 作用域变量（自定义属性不向上继承，永远解析不到）；不要靠加深色相保 AA（会成泥色），降 alpha 才保色相。

---

## 20. 第 20 轮评审落地实录（2026-10-15，v2.17.15，本线）

**输入**：`docs/美化问题清单-hmg-admin-model-20261015.md`（评审-only，P0×3/P1×3/P2×8 + 量化验收五项）。
逐条核实：P0 全部、P1 全部、P2-3/4/7/8 属实；P2-1 部分成立（氛围层实拍可见，评审 jpg 有损压缩疑洗掉低对比渐变）；P2-2/2-5 延期。

**核心数字**（详表见 `docs/修改记录-20261015-v2.17.15.md`）：
- 暗中性阶降彩度 HSL 37%→11-13%；亮纸面 #ffffff→#fafafc；canvas↔s1 →1.149/1.141（§16.4 ΔL*≥6 首次达标）。
- 计费轴独占彩色：`.badge-one` 紫 / `.badge-idle` 琥珀（hue 对随徽章调 AA），能力/格式/特性轴中性化/tag 化/描边化。
- 删除钮 danger-hover ×2（本页 btn-danger 清零）；供应商卡 `pc-hue-N` 3px 左色条 + 头像 26px；
  `.m-tags` 窄屏独立行；ep-id/把手/a·link 弱化与安全色。
- 验收：暗靛蓝 16.8%（≤88 ✓）/彩度 11-13%（≤14 ✓）/对比 ✓/单行彩徽 ≤1 ✓；**亮色无彩色 99.2% 未达 ≤92% 如实记录**
  ——清单 §6 与 P1-1 互斥（拆靛蓝噪色必然缩彩色面积），按 P1-1 执行交用户裁决。

**Do / Don't 增补**：
- Do：语义色上脸时连同 token 一起调 AA（hue1/hue6 双主题四值全数重算）；测试色值锚点随每个色板轮同步（T40k/r/s→v）。
- Don't ①：不要把页面级装饰类复用全局组件类（`hue-N` 是头像 tint 类，卡片挂上整卡染色——专属前缀 `pc-hue-N`）。
- Don't ②：不要在主题切换后立即截图——壳先换卡后换的混合帧（§14 冻结伪影家族）会污染取证；
  用「先写 localStorage 再 reload」的首 paint 主题法，拍前读回 canvas/cardBg 双值验证。

---

## 21. 亮色主题美术重构实录（2026-10-15，v2.17.16，本线）

**反馈**：「白天的颜色不太好看」（§20 刚按 P0-2/P0-3 做的「冷调纸面+卡片更深」被用户否）。

**教训（本轮核心）**：评审 P0-3 的「canvas↔s1 ≥1.13」深度判据本身没错，**错的可以是步进方向**——
亮色模式把卡片做成比页面深的中灰（#eaebf1 压 #fafafc）读作「脏」；轻色高级感的既定范式是
iOS/macOS 分组样式：**灰底白卡**（卡片比环境亮，边界交描边+投影）。深度数值达标 ≠ 观感达标，
ΔL* 判据必须配「浅色模式卡片宜白」的符号约束一起用。

**定稿**：canvas #eef0f4 冷灰 + s1 #ffffff 纯白卡（1.141/ΔL*5.25 仍达标），s2-s4 填充梯/hairline
随白卡重排；ink-subtle 回退（白卡 6.4 更透亮）、tertiary #666b79（灰画布 4.67）、warn 保持 #8a5009；
语义 tint 与暗色零改动。数值详表见 `docs/修改记录-20261015-v2.17.16.md`。

**另查证**：Linear DESIGN.md 明言官方不出亮色（line 546「Light mode is not documented」）——
亮色本就是自建设计，无违背品牌锚点一说。

**Do / Don't 增补**：
- Do：亮色深度用「白卡+灰底」表达；token 换代时 ink 三枚重新算 AA 而不是沿用（新旧画布亮度差
  会翻转 tertiary 的达标结论：#616674 在 #fafafc 达标、在 #eef0f4 破线）。
- Don't：不要在亮色模式让卡片比画布深超过 ΔL*≈3——那是报纸不是卡片。
**回归**：292/292（T40k/r/v 亮色锚点同步）；8660 三页亮色实拍复验；stats 首拍发虚=5s 轮询入场
中帧非缺陷（稳定态复拍正常，截图取证要先等稳定）。

---

## 22. 亮色身份光提强实录（2026-10-15，v2.17.17，本线）

**反馈**：「白天的颜色太单调了」——§21 灰底白卡干净但素：白卡占视口七成，亮色 aura 还是按白
画布定的 .075/.06/.07，灰画布上几不可感。

**定稿**：亮色专属「混白 40%」粉彩三元组（六页 `:root[data-theme=light] body[data-page]` 覆盖）
+ alpha 提到 .21/.165/.175（约 2.8 倍）+ 斜轴/底角/html 角同步提强 + 亮色网格改靛调
`rgba(94,106,210,.055/.028)` + 白卡投影第二层微靛 `rgba(94,106,210,.16)`。暗色零改动。

**关键取舍（数值否决实录）**：复用暗色原色直接提 alpha 在 .13/.10/.11 就把 P2 峰值 ink-subtle
打到 4.36——粉彩自身亮度高，提强不掉画布 L，才是亮色加色的正路（与 §16.3「亮色高饱和会脏」
互补：脏的是低亮度高饱和，高亮度粉彩加 alpha 是安全的）。

**Do / Don't 增补**：
- Do：亮色提色优先「提亮度换浓度」——混白的粉彩可以开大 alpha，原色不可以。
- Don't：亮色页覆盖必须带 `:root[data-theme]` 前缀（(0,3,1) 压过亮色基座 (0,2,1)），裸
  `body[data-page]` 会被亮色基座反杀、六页塌成一色。
**回归**：292/292；8660 实拍 model（粉彩靛光带/靛网格/微靛投影）与 stats（青身份）分明。
