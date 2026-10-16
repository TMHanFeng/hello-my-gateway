# 动效问题清单 — motion-design skill 评审

- 日期:2026-10-16
- 评审依据:LottieFiles `motion-design` skill(v1.0.0, MIT),重点用 `reference/quality-checklist.md` 的严重度分级和 `reference/troubleshooting.md` 的症状表
- 评审范围:`static/` 下 9 个前端文件,重点 `index.html`、`hfadmin.html`、`hmg-admin/assets/hmg.css`、`hmg-admin/*.html`
- 未改动任何业务代码,本文件仅记录问题与建议

---

## 结论先说

整体水平**高于**该 skill 的平均基线。三项基础设施做得比 skill 建议还好:缓动/时长 token 化、退场时长短于入场、首屏静止 + 增量才播动画。

问题集中在 **`static/index.html` 这一个文件** —— 它是唯一没有 reduced-motion 兜底的文件,也是唯一还在用 `transition: all` 和布局属性过渡的文件。同仓库的 `hfadmin.html` 和 `hmg.css` 已经把这些都修好了,说明是**历史遗留不同步**,不是没意识到。

优先级:**2 个 CRITICAL 都在 index.html**。

---

## CRITICAL

### C1. `index.html` 完全没有 `prefers-reduced-motion` 兜底

**证据**:全仓 `prefers-reduced-motion` 命中 6 处,分布在 `hfadmin.html`(2 处 CSS + 2 处 JS)和 `hmg.css:836`,**`index.html` 零命中**。

而 `index.html` 里有这些动效:

| 位置 | 动画 | 是否无限循环 |
|---|---|---|
| `index.html:61` | `ping 1.8s ease-out infinite`(状态点扩散) | 是 |
| `index.html:336` | `sheen 2.6s ease-in-out infinite`(统计条扫光) | 是 |
| `index.html:334` | `transition: width .8s` + `fillIn .8s` | 否 |
| `index.html:80` `82` `384` `486` `489` | `rise` / `pop` / `slidein` | 否 |

**为什么严重**:该 skill 的 accessibility 检查项把「必须提供 reduced-motion 替代方案」列为 HIGH,而两处 `infinite` 循环动画对前庭功能障碍用户是持续刺激。skill 建议的处理方式是:去空间位移、保留 opacity、去 spring、时长砍半 50%+。

**建议**:照抄 `hfadmin.html:713` 已有的写法。

```css
/* index.html <style> 末尾追加 */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
  .dot::after, .stat-fill::after { animation: none !important; }
  .stat-fill { transition: width 0s !important; }
}
```

> 注:`.dot::after` 和 `.stat-fill::after` 是纯装饰扫光,`animation-duration: 0.01ms` 会让它们闪一帧后停在 `translateX(-100%)` 的初始位,不如直接 `animation: none` 干净。

---

### C2. `.stat-fill` 同时挂了两套进度动画,缓动叠乘 + 每帧触发布局

**证据**:

```css
/* index.html:334 */
.stat-fill { ...; transition: width .8s cubic-bezier(.22,1,.36,1); animation: fillIn .8s cubic-bezier(.22,1,.36,1) backwards; transform-origin: left; }
@keyframes fillIn { from { transform: scaleX(0); } }

/* hfadmin.html:514 —— 同样写法 */
.stat-fill { ...; transition: width 600ms var(--ease-out); animation: fillIn .6s var(--ease-out) backwards; transform-origin: left; }
```

**问题**:两条路径同时生效,可见宽度 = `width(t) × scaleX(t)`。两条都是 ease-out,叠乘后前段快得异常,不是设计出来的曲线。同时 `width` 过渡每帧触发 layout —— 该 skill 的性能表把 `width/height` 列为 **Poor(triggers layout)**,把 `transform` 列为 **Excellent(GPU)**。

**建议**:二选一,只留 `transform` 方案(性能更好,且天然只动合成层):

```css
.stat-fill { transform-origin: left; transform: scaleX(0); transition: transform .6s var(--ease-out); }
.stat-fill[style*="--pct"], .stat-fill.is-set { transform: scaleX(var(--p, 0)); }
```

如果暂时不想改 JS 结构,最小改动是**删掉 `animation: fillIn` 那一行**,只保留 `transition: width`。视觉损失极小,layout 开销减半。

`hmg.css:445` 也是 `transition: width .45s`,但它在 `hmg.css:841` 的 reduced-motion 块里已经做了 `transition: width 0s` 处理,优先级低于上面两处。

---

## HIGH

### H1. 池列表 stagger 没有封顶,会突破 500ms 总预算

**证据**:

```js
// pools.html:254 —— 无上限
` style="animation:hmgIn 280ms ease-out ${i * 35}ms backwards"`

// hfadmin.html:2501 —— 同样无上限
` style="animation:cardIn 280ms var(--ease-out) ${i * 35}ms backwards"`

// model.html:294 —— 有封顶
` style="animation:hmgIn 300ms ease-out ${Math.min(i * 40, 400)}ms backwards"`

// hfadmin.html:1674 —— 同样有封顶
` style="animation:cardIn 300ms var(--ease-out) ${Math.min(i * 40, 400)}ms backwards"`
```

**问题**:该 skill 规定 stagger 总预算必须 < 500ms。`i * 35` 在第 15 个条目时延迟已达 525ms,20 个池就是 700ms —— 尾部要等将近一秒才出现,用户会以为没加载。model 侧已经用 `Math.min(i * 40, 400)` 封顶了,池列表漏了。

**建议**:与 model 侧统一。

```js
${Math.min(i * 35, 400)}ms
```

---

### H2. 用布局属性做过渡(3 处)

该 skill 性能分级:

| 属性 | 评级 |
|---|---|
| `transform` / `opacity` | Excellent(GPU 合成) |
| `color` / `clip-path` | Good |
| `width` / `height` / `margin` / `padding` | Poor(触发布局) |
| `box-shadow` / `filter` / `border-radius` | Avoid |

命中项:

| 位置 | 现状 | 应改为 |
|---|---|---|
| `index.html:156` | `.theme-switch .ts-thumb { transition: left .2s; }` | `transform: translateX()` |
| `hfadmin.html:580` | `.usage-bar { transition: height 450ms var(--ease-out); }` | `transform: scaleY()` |
| `hmg.css:623` | `transition: padding-bottom, margin-bottom var(--t2)` | `transform` 或接受 layout 开销 |

**`index.html:156` 尤其值得改** —— 同一个文件 `index.html:262` 的 `.switch .track::after` 用的就是 `transform: translateX()` + `transform .2s`,写法已经是对的,只是开关没统一:

```css
/* 现状(index.html:156) */
.theme-switch .ts-thumb { position:absolute; left:2px; top:2px; width:16px; height:16px; ...; transition:left .2s; }

/* 建议 */
.theme-switch .ts-thumb { position:absolute; left:2px; top:2px; width:16px; height:16px; ...; transition: transform .2s cubic-bezier(.22,1,.36,1); }
.theme-switch input:checked + .ts-thumb { transform: translateX(16px); }
```

---

### H3. `index.html` 有 4 处 `transition: all`

**证据**:`index.html:192` `.btn`、`:217`、`:233`、`:253` `.remove-x`,均为 `transition: all .14s~.16s`。

**为什么算问题**:`transition: all` 会把**未来新增的任何属性**也纳入过渡 —— 包括 `width` / `height` / `margin` 这类会触发布局的属性,等于给后续维护埋雷。

对比:同仓库的 `hmg.css` 全部是精确枚举,例如 `hmg.css:296`:

```css
transition: border-color var(--t1) ease, box-shadow var(--t1) ease, background-color var(--t1) ease;
```

**建议**:按实际变化的属性展开。`.btn` 那处至少应写成 `background-color, color, border-color, transform`。

---

## MEDIUM

### M1. `keyPulse` 无限动画 `box-shadow`

**证据**:`hfadmin.html:794-795`

```css
.key-dot { animation: keyPulse 2.6s var(--ease-in-out) infinite; }
@keyframes keyPulse { 0%,100% { box-shadow: 0 0 0 0 rgba(52,199,89,.35); } 50% { box-shadow: 0 0 0 5px rgba(52,199,89,0); } }
```

`box-shadow` 属于 skill 性能表的 Avoid 档,且这是 **2.6s 无休止**运行 —— 只要页面上有这个元素就一直在重绘。

已有 `hfadmin.html:713` 的 reduced-motion 兜底,所以不是无障碍问题,是纯性能问题。

**建议**:换成 `::after` 伪元素 + `transform: scale()`,把动画移出合成层:

```css
.key-dot { animation: none; }  /* 去掉 box-shadow 动画 */
```

或保留动效但改为伪元素:

```css
.key-dot::after {
  content:''; position:absolute; inset:0; border-radius:50%;
  background: rgba(52,199,89,.35);
  animation: keyPulse 2.6s var(--ease-in-out) infinite;
  transform: scale(1); opacity: 1;
}
@keyframes keyPulse { 0%,100% { transform: scale(1); opacity: .5; } 50% { transform: scale(2.2); opacity: 0; } }
```

---

### M2. `index.html` 弹窗入场偏短,且完全没有退场动画

**证据**:

```css
/* index.html:378-386 */
.overlay { display: none; ... }
.overlay.show { display: flex; animation: fade .18s ease; }
@keyframes fade { from { opacity: 0; } }
.modal { ... animation: pop .22s cubic-bezier(.22,1,.36,1); }
@keyframes pop { from { opacity: 0; transform: translateY(14px) scale(.98); } }
```

**问题**:
1. 弹窗入场 220ms,skill 的时长表给 Modal/Dialog 是 **300-400ms**。220ms 偏快,弹窗是空间关系变化,需要更长时间让用户看清它从哪来。
2. `.overlay` 靠 `display: none / flex` 切换,**没有退场动画**。skill 明确要求「入场比退场长 30-50%」,这里是退场为 0。

**对比**:`hmg.css` 里的做法是对的 —— `hmg.css:499` `.mask.off { opacity: 0; transition-duration: 140ms; }`,入场用 `--t3`,退场 140ms,比例合规。

**建议**:`index.html` 的遮罩改成 class 驱动而非 `display` 驱动,复用 `hmg.css` 的 `.mask.on/.mask.off` 模式。

---

### M3. hover 入场 160ms,超过 100ms 上限

**证据**:`hmg.css:186`、`:217`、`:332`

```css
transition: background-color var(--t1) ease, color var(--t1) ease, transform 160ms var(--ease-out);
```

skill 的交互反馈表:Hover < 100ms,Release/settle 200-300ms。

**但这里有个 skill 没覆盖的工程约束**:skill 建议「hover 进入快(100ms)、离开慢(150-200ms)」的非对称手感,而 **CSS transition 无法区分进出方向** —— 同一条规则两个方向共用一个时长。要实现非对称必须用 JS 监听 `mouseenter`/`mouseleave` 分别设置。

所以这条**建议不改**,理由是:160ms 已经在「响应」和「精致」的合理区间内,为此引入 JS 状态监听不划算。记在这里是为了说明我们**知情的偏离**。

---

## LOW

| 项 | 位置 | 说明 |
|---|---|---|
| `bubbleRise 24s linear infinite` | `hfadmin.html:82` | skill 建议循环动效用 sine;linear 只留给旋转/进度条。24s 周期下人眼几乎感知不到差异,且已有 reduced-motion 兜底,可不改 |
| `box-shadow` 过渡 | `hmg.css:279-282` `:296` `:373` | skill 性能表列为 Avoid 档。但这些都在 hover 时才触发,单元素同时最多一个,实际代价可控。属于「知情取舍」,不改 |

---

## 做得好的部分(建议保持)

这几项**优于** skill 的默认建议,改代码时别退回去:

1. **缓动与时长 token 化** — `hfadmin.html:26-28` 定义了 `--ease-out: cubic-bezier(.22,1,.36,1)`、`--spring: cubic-bezier(.34,1.42,.5,1)`、`--ease-in-out`;`hmg.css` 定义了 `--t1/--t2/--t3`。改一次全局生效,不会散落成魔法数字。

2. **按压反馈统一挂在 `:active`** — `hmg.css:268` `.btn:active`、`:464` `.seg button:active` 都是 `scale(.97)`,这个值落在 skill 的 Corporate 原型区间(Corporate 按压 0.97 / Playful 0.95),和这套后台管理界面的调性一致;且挂在 `:active` 而非 `click` 上,触发时机正确,不会多等一个 click 周期。

   > 唯一例外是 `hmg.css:221` `.theme-btn:active { transform: scale(.94); }`,比另外两处深了 0.03。skill 的按压区间下限是 0.95(Playful),0.94 低于所有原型的取值。主题切换按钮通常一屏点不到几次、手感不易察觉,影响很小,但如果要统一,改成 `.97` 即可 —— 这属于知情偏离,不算缺陷。

3. **退场短于入场** — `hmg.css:499` `.mask.off { transition-duration: 140ms; }` 对应入场 `--t3`,符合 skill 的「退场 = 入场的 65-75%」。

4. **首屏静止 + 增量才播动画** — `index.html:2999`、`hfadmin.html:3976`、`logs.html:247` 都有 `const anim = firstLoad ? 'none' : 'logIn ... logFlash ...'`。skill 只要求「同一个交互每次动效一致」,这里是更进一步:**第一次访问动、之后不重复动**,直接消解了长时间使用时的视觉疲劳。这是这套代码里最成熟的一个判断。

5. **reduced-motion 下停用进度条过渡** — `hmg.css:841` `.bar > i, .q-row .q-track i { transition: width 0s; }`,精确命中了 skill 的性能与无障碍双重要求。

6. **入场 stagger 已封顶** — `model.html:294`、`hfadmin.html:1674` 用 `Math.min(i * 40, 400)`,符合 500ms 总预算。H1 要修的正是把这个约定补到池列表。

---

## 建议的动手顺序

1. **C1** — 给 `index.html` 加 reduced-motion 块(照抄 `hfadmin.html:713`)
2. **C2** — 删掉 `.stat-fill` 的 `animation: fillIn` 一行
3. **H1** — `pools.html:254` / `hfadmin.html:2501` 加 `Math.min(i * 35, 400)`
4. **H2** — `index.html:156` 改 `translateX`;`hfadmin.html:580` 改 `scaleY`
5. **H3** — `index.html` 4 处 `transition: all` 展开
6. **M1 / M2** — 视时间安排

前 5 项都是局部改动,互不冲突,可以分多个提交做完。
