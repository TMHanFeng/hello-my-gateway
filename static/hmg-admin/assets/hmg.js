/* ============================================================
   hmg-admin 共享基础设施（六页单源；架构决策见 docs/美化工作单-hmg-admin.md §1）
   - 鉴权：localStorage.gw_key（与 admin/hfadmin 同键互通），API 全走 /admin/* Bearer
   - 壳：renderShell() 按 body[data-page] 单源注入侧栏/移动端抽屉，杜绝六页复制
   - 主题：data-theme 切换只换 CSS token（单 DOM 双主题）
   - 轮询：pollMgr 统一管理（visibilitychange 感知，离页清理）
   ============================================================ */
"use strict";

const HMG_VERSION = "__GATEWAY_VERSION__";

/* ── 图标（内联 SVG，stroke=currentColor；不引图标字体/文件） ── */
const HMG_ICONS = {
  model: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2 3 7v10l9 5 9-5V7l-9-5z"/><path d="M3 7l9 5 9-5"/><path d="M12 12v10"/></svg>',
  pools: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 2 8.5 12 14l10-5.5L12 3z"/><path d="m2 14.5 10 5.5 10-5.5"/></svg>',
  stats: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20v-7"/><path d="M10 20V5"/><path d="M16 20v-9"/><path d="M22 20H2"/></svg>',
  logs: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3.5 6h.01"/><path d="M3.5 12h.01"/><path d="M3.5 18h.01"/></svg>',
  keys: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="7.5" cy="15.5" r="4.5"/><path d="m11 12 9.5-9.5"/><path d="M15 5l3.5 3.5"/><path d="m18 8 2.5-2.5"/></svg>',
  plugins: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 7V3.5"/><path d="M15 7V3.5"/><path d="M7 7h10v3.5a5 5 0 0 1-5 5 5 5 0 0 1-5-5V7z"/><path d="M12 15.5V21"/></svg>',
  sun: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>',
  moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>',
  menu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 7h16M4 12h16M4 17h16"/></svg>',
  folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7V5a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/></svg>',
  cog: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1"/></svg>',
};
/* 主题图标：暗色显太阳（点击切亮），亮色显月亮（点击切暗）——统一线性 SVG，不用 emoji（廉价感来源） */
function hmgThemeIcon(t) { return (t === "light") ? HMG_ICONS.moon : HMG_ICONS.sun; }
const HMG_NAV = [
  { id: "model", label: "模型管理", icon: "model", href: "/hmg-admin/model" },
  { id: "pools", label: "模型池", icon: "pools", href: "/hmg-admin/pools" },
  { id: "stats", label: "用量统计", icon: "stats", href: "/hmg-admin/stats" },
  { id: "logs", label: "调用记录", icon: "logs", href: "/hmg-admin/logs" },
  { id: "keys", label: "API 密钥", icon: "keys", href: "/hmg-admin/keys" },
  { id: "plugins", label: "插件中心", icon: "plugins", href: "/hmg-admin/plugins" },
];

/* ── 鉴权 / API ───────────────────────────────────────────── */
function hmgGetKey() {
  let k = localStorage.getItem("gw_key");
  if (!k) {
    k = prompt("请输入管理密码：");
    if (!k) throw new Error("未输入管理密码");
    localStorage.setItem("gw_key", k);
  }
  return k;
}
function hmgHeaders(extra) {
  return Object.assign({ "Authorization": "Bearer " + hmgGetKey(), "Content-Type": "application/json" }, extra || {});
}
/** 面板统一 API 入口：任何失败（网络异常/401/非 2xx）toast 后返回 null（与原面板 api 同契约，移植代码零改动）；成功返回解析后的 JSON（可为 null）。 */
async function hmgApi(path, opts = {}) {
  let r;
  try {
    r = await fetch(path, Object.assign({}, opts, { headers: hmgHeaders(opts.headers) }));
  } catch (e) {
    hmgToast("网络异常：" + e.message, "err");
    return null;
  }
  if (r.status === 401) {
    localStorage.removeItem("gw_key");
    hmgToast("管理密码无效，请重新输入", "err");
    return null;
  }
  let data = null;
  try { data = await r.json(); } catch (e) { /* 空响应 */ }
  if (!r.ok) {
    const msg = (data && (data.detail || data.error || data.message)) || ("HTTP " + r.status);
    hmgToast(String(msg), "err");
    return null;
  }
  return data;
}
function hmgChangeKey() {
  const k = prompt("设置新的管理密码（与 config.json server.api_key 一致）：", localStorage.getItem("gw_key") || "");
  if (k === null) return;
  if (!k) { hmgToast("密码不能为空", "err"); return; }
  localStorage.setItem("gw_key", k);
  hmgToast("管理密码已更新（仅本浏览器生效）", "ok");
  const pill = document.querySelector(".pill-key .pk-t");
  if (pill) pill.textContent = "密码已设置";
}

/* ── 工具 ─────────────────────────────────────────────────── */
function hmgEsc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function hmgPathEnc(s) { return encodeURIComponent(String(s == null ? "" : s)); }
/** 千分位（小数位保留原样）。 */
function hmgNum(n) {
  if (n == null || isNaN(Number(n))) return String(n == null ? "-" : n);
  const neg = Number(n) < 0;
  const parts = Math.abs(Number(n)).toString().split(".");
  parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return (neg ? "-" : "") + parts.join("");
}
/** token 量简写（与原面板 fmtTok 同语义）：12345678 → 1234.57万 / 1.23亿。 */
function hmgFmtTok(n) {
  n = n || 0;
  return n >= 1e8 ? (n / 1e8).toFixed(2) + "亿" : n >= 1e4 ? (n / 1e4).toFixed(1) + "万" : hmgNum(n);
}
/** onclick 参数安全化（与原面板 jsq 同名同语义）：HTML 转义 + 引号转义。 */
function hmgJsq(s) { return hmgEsc(String(s)).replace(/'/g, "\\'"); }
/** Unix 秒 → MM-DD HH:mm:ss。 */
function hmgFmtTs(sec) {
  if (!sec) return "-";
  const d = new Date(sec * 1000);
  const p = x => String(x).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
/** 秒 → 人类时长：0.8s / 1.2s / 2m03s。 */
function hmgFmtDur(sec) {
  sec = Number(sec) || 0;
  if (sec < 60) return sec.toFixed(sec < 10 ? 2 : 1) + "s";
  const m = Math.floor(sec / 60), s = Math.round(sec % 60);
  return `${m}m${String(s).padStart(2, "0")}s`;
}
/** 内容未变零写入（防 5s 轮询重渲闪烁）。 */
function hmgSetTxt(el, html) {
  if (el && el.innerHTML !== html) el.innerHTML = html;
}
function hmgEl(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

/* ── toast（transition 生命周期：.on 入场 / .out 出场，可中断；出场恒快于入场） ── */
let _hmgToastBox = null;
function hmgToast(msg, type = "info", ms = 2600) {
  if (!_hmgToastBox) { _hmgToastBox = document.createElement("div"); _hmgToastBox.id = "hmg-toast"; document.body.appendChild(_hmgToastBox); }
  const t = hmgEl(`<div class="toast"><span class="dot ${type === "ok" ? "ok" : type === "err" ? "err" : type === "warn" ? "warn" : "acc"}"></span><span></span></div>`);
  t.lastElementChild.textContent = String(msg);
  _hmgToastBox.appendChild(t);
  void t.offsetWidth;            // 先以初始态入布局，再加 .on 触发过渡
  t.classList.add("on");
  setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 170); }, ms);
}

/* ── 弹窗（桌面居中 / 移动端全屏 sheet，见 hmg.css §8；transition 生命周期：mask.on 入场 / mask.off 出场） ── */
function hmgOpenModal(opts) {
  const mask = hmgEl(`<div class="mask"><div class="modal${opts.wide ? " wide" : ""}" role="dialog" aria-modal="true">
    <div class="modal-head"><div class="t"></div><button class="modal-x" aria-label="关闭">✕</button></div>
    <div class="modal-body"></div>
    <div class="modal-foot"><div class="hint grow" style="margin:0"></div><div class="right" style="display:flex;gap:8px"></div></div>
  </div></div>`);
  mask.querySelector(".t").textContent = opts.title || "";
  const body = mask.querySelector(".modal-body");
  if (typeof opts.body === "string") body.innerHTML = opts.body;
  else if (opts.body) body.appendChild(opts.body);
  const footBtns = mask.querySelector(".modal-foot .right");
  if (!opts.footer || !opts.footer.length) mask.querySelector(".modal-foot").style.display = "none";
  const close = () => {
    if (mask.__closing) return;    // 出场中防重入（Esc/遮罩/✕ 连按）
    mask.__closing = true;
    document.removeEventListener("keydown", onKey);
    mask.classList.add("off");     // 出场 140ms（恒快于入场）后再摘除
    setTimeout(() => mask.remove(), 160);
    if (opts.onClose) opts.onClose();
  };
  const onKey = e => { if (e.key === "Escape") close(); };
  if (opts.maskClose !== false) mask.addEventListener("mousedown", e => { if (e.target === mask) close(); });
  mask.querySelector(".modal-x").onclick = close;
  document.addEventListener("keydown", onKey);
  for (const b of (opts.footer || [])) {
    const btn = document.createElement("button");
    btn.className = b.cls || "btn btn-secondary";
    btn.textContent = b.label;
    btn.onclick = () => b.onClick && b.onClick({ close, body, mask, btn });
    footBtns.appendChild(btn);
  }
  document.body.appendChild(mask);
  void mask.offsetWidth;           // 初始态先入布局，再加 .on 触发缩放+淡入过渡
  mask.classList.add("on");
  return { mask, body, close };
}
/** Promise 版确认框。 */
function hmgConfirm(msg, { okLabel = "确认", danger = false, title = "请确认" } = {}) {
  return new Promise(resolve => {
    let done = false;
    const m = hmgOpenModal({
      title,
      body: `<div style="font-size:14px;line-height:1.6">${msg}</div>`,
      footer: [
        { label: "取消", cls: "btn btn-secondary", onClick: ({ close }) => { done = true; close(); resolve(false); } },
        { label: okLabel, cls: "btn " + (danger ? "btn-danger" : "btn-primary"), onClick: ({ close }) => { done = true; close(); resolve(true); } },
      ],
      onClose: () => { if (!done) resolve(false); },
    });
    setTimeout(() => { const ok = m.mask.querySelector(".modal-foot .btn-primary, .modal-foot .btn-danger"); if (ok) ok.focus(); }, 0);
  });
}
/** Promise 版输入框（替代原生 prompt，样式统一）。 */
function hmgPrompt(title, { value = "", placeholder = "", okLabel = "确定", multiline = false } = {}) {
  return new Promise(resolve => {
    let done = false;
    const id = "hmg-pi-" + Date.now();
    const fld = multiline
      ? `<textarea id="${id}" class="inp" rows="4" placeholder="${hmgEsc(placeholder)}">${hmgEsc(value)}</textarea>`
      : `<input id="${id}" class="inp" value="${hmgEsc(value)}" placeholder="${hmgEsc(placeholder)}">`;
    const m = hmgOpenModal({
      title,
      body: `<div class="frm"><div>${fld}</div></div>`,
      footer: [
        { label: "取消", cls: "btn btn-secondary", onClick: ({ close }) => { done = true; close(); resolve(null); } },
        { label: okLabel, cls: "btn btn-primary", onClick: ({ close }) => { done = true; close(); resolve(document.getElementById(id).value); } },
      ],
      onClose: () => { if (!done) resolve(null); },
    });
    setTimeout(() => { const i = document.getElementById(id); if (i) { i.focus(); if (!multiline) i.select(); } }, 0);
  });
}

/* ── 下拉菜单（fixed 定位，自动翻转防溢出；缩放入场以触发源为 transform-origin） ── */
let _hmgMenu = null;
function hmgCloseMenu() {
  if (!_hmgMenu) return;
  const m = _hmgMenu;
  _hmgMenu = null;   // 先摘引用：动画期间再开新菜单互不干扰
  m.classList.add("off");
  setTimeout(() => m.remove(), 110);
  document.removeEventListener("mousedown", _hmgMenuOutside, true);
  window.removeEventListener("resize", hmgCloseMenu);
  window.removeEventListener("scroll", hmgCloseMenu, true);
  document.removeEventListener("keydown", _hmgMenuEsc);
}
function _hmgMenuOutside(e) { if (_hmgMenu && !_hmgMenu.contains(e.target)) hmgCloseMenu(); }
function _hmgMenuEsc(e) { if (e.key === "Escape") hmgCloseMenu(); }
/** items: [{label, onClick, danger}] 或 "-" 分隔线。 */
function hmgMenu(anchor, items) {
  hmgCloseMenu();
  const m = document.createElement("div");
  m.className = "menu";
  m.style.position = "fixed";
  for (const it of items) {
    if (it === "-") { m.appendChild(hmgEl('<div class="sep"></div>')); continue; }
    const b = document.createElement("button");
    if (it.danger) b.className = "danger";
    b.textContent = it.label;
    b.onclick = () => { hmgCloseMenu(); if (it.onClick) it.onClick(); };
    m.appendChild(b);
  }
  document.body.appendChild(m);
  _hmgMenu = m;
  const r = anchor.getBoundingClientRect();
  const mw = m.offsetWidth, mh = m.offsetHeight;
  let x = Math.max(8, Math.min(r.left, window.innerWidth - mw - 8));
  let y = r.bottom + 4;
  const openedUp = y + mh > window.innerHeight - 8;
  if (openedUp) y = Math.max(8, r.top - mh - 4);
  m.style.left = x + "px"; m.style.top = y + "px";
  // 触发源感知 origin：菜单从锚点方向缩放展开（弹窗豁免——弹窗恒居中）
  m.style.transformOrigin = (x + mw / 2 < window.innerWidth / 2 ? "left" : "right") + " " + (openedUp ? "bottom" : "top");
  void m.offsetWidth;
  m.classList.add("on");
  setTimeout(() => {
    document.addEventListener("mousedown", _hmgMenuOutside, true);
    window.addEventListener("resize", hmgCloseMenu);
    window.addEventListener("scroll", hmgCloseMenu, true);
    document.addEventListener("keydown", _hmgMenuEsc);
  }, 0);
}

/* ── 轮询管理（页签不可见暂停、回前台恢复，离页 stopAll） ──── */
const hmgPoll = {
  _t: {}, _reg: {},
  start(name, fn, ms) { this.stop(name); this._reg[name] = { fn, ms }; this._t[name] = setInterval(fn, ms); },
  stop(name) { if (this._t[name]) { clearInterval(this._t[name]); delete this._t[name]; } delete this._reg[name]; },
  stopAll() { for (const n of Object.keys(this._reg)) this.stop(n); },
  _pause() { for (const t of Object.values(this._t)) clearInterval(t); this._t = {}; },
  _resume() { for (const [n, r] of Object.entries(this._reg)) if (!this._t[n]) this._t[n] = setInterval(r.fn, r.ms); },
};
document.addEventListener("visibilitychange", () => { if (document.hidden) hmgPoll._pause(); else hmgPoll._resume(); });
window.addEventListener("pagehide", () => hmgPoll.stopAll());

/* ── 主题（单 DOM 双主题：只换 token 颜色） ────────────────── */
function hmgTheme() { return document.documentElement.getAttribute("data-theme") || "dark"; }
function hmgSetTheme(t) {
  if (t !== "light" && t !== "dark") t = "dark";
  localStorage.setItem("hmg_theme", t);
  document.documentElement.classList.add("theming");
  document.documentElement.setAttribute("data-theme", t);
  setTimeout(() => document.documentElement.classList.remove("theming"), 200);
  document.querySelectorAll(".theme-btn").forEach(b => { b.innerHTML = hmgThemeIcon(t); b.title = t === "dark" ? "切换到白天模式" : "切换到夜晚模式"; });
}
function hmgToggleTheme() { hmgSetTheme(hmgTheme() === "dark" ? "light" : "dark"); }

/* ── 壳注入（侧栏 + 移动端顶栏/抽屉，六页单源） ─────────────── */
function hmgRenderShell() {
  const page = document.body.dataset.page || "";
  const sb = document.getElementById("hmg-sidebar");
  if (sb) {
    sb.innerHTML = `
      <div class="hmg-brand"><div class="logo">H</div><span class="name">HMG 网关</span><span class="ver">${hmgEsc(HMG_VERSION)}</span></div>
      <nav class="hmg-nav">${HMG_NAV.map(n => `
        <a class="nav-item${n.id === page ? " on" : ""}" href="${n.href}"${n.id === page ? ' aria-current="page"' : ""}>${HMG_ICONS[n.icon]}<span class="lb">${hmgEsc(n.label)}</span></a>`).join("")}
      </nav>
      <div class="hmg-side-foot">
        <div class="hmg-side-row"><span class="side-ic">${HMG_ICONS.keys}</span>
          <button class="pill-key" onclick="hmgChangeKey()" title="点击修改管理密码（仅本浏览器）"><span class="pk-t">管理密码</span></button>
        </div>
        <div class="hmg-side-row"><button class="theme-btn" onclick="hmgToggleTheme()">${hmgThemeIcon(hmgTheme())}</button>
          <span style="font-size:12px;color:var(--ink-tertiary);letter-spacing:.01em">主题</span></div>
      </div>`;
    const tb = sb.querySelector(".theme-btn");
    tb.innerHTML = hmgThemeIcon(hmgTheme());
    tb.title = hmgTheme() === "dark" ? "切换到白天模式" : "切换到夜晚模式";
  }
  const bar = document.getElementById("hmg-topbar");
  if (bar) {
    const cur = HMG_NAV.find(n => n.id === page);
    bar.innerHTML = `
      <button class="btn btn-ghost theme-btn" id="hmg-burger" aria-label="菜单">${HMG_ICONS.menu}</button>
      <span class="pg">${hmgEsc(cur ? cur.label : "HMG 网关")}</span>
      <span class="sp"></span>
      <button class="theme-btn" onclick="hmgToggleTheme()">${hmgThemeIcon(hmgTheme())}</button>`;
    bar.querySelector(".theme-btn[onclick]").title = hmgTheme() === "dark" ? "切换到白天模式" : "切换到夜晚模式";
    bar.querySelector("#hmg-burger").onclick = () => document.body.classList.toggle("drawer-open");
  }
  const mk = document.getElementById("hmg-drawermask");
  if (mk) mk.onclick = () => document.body.classList.remove("drawer-open");
}

/* 全局暴露（页面业务 JS 直接用这些名字） */
window.HMG = {
  V: HMG_VERSION, ICONS: HMG_ICONS, NAV: HMG_NAV,
  key: hmgGetKey, headers: hmgHeaders, api: hmgApi, changeKey: hmgChangeKey,
  esc: hmgEsc, pathEnc: hmgPathEnc, jsq: hmgJsq, num: hmgNum, fmtTok: hmgFmtTok, fmtTs: hmgFmtTs, fmtDur: hmgFmtDur,
  setTxt: hmgSetTxt, el: hmgEl, toast: hmgToast,
  modal: hmgOpenModal, confirm: hmgConfirm, prompt: hmgPrompt,
  menu: hmgMenu, closeMenu: hmgCloseMenu,
  poll: hmgPoll, theme: hmgTheme, setTheme: hmgSetTheme, toggleTheme: hmgToggleTheme,
  renderShell: hmgRenderShell,
  isTouch: !!(window.matchMedia && window.matchMedia("(hover: none), (pointer: coarse)").matches),
};

/* ── ⓘ 信息气泡（data-tip 多行浮层，跟随鼠标/触屏点按；原面板 i-tip 同款交互） ── */
(function () {
  const pop = document.createElement("div");
  pop.style.cssText = "display:none;position:fixed;z-index:150;max-width:340px;white-space:pre-wrap;background:var(--surface-3);color:var(--ink);border:1px solid var(--hairline-strong);border-radius:8px;padding:8px 10px;font-size:12px;line-height:1.6;pointer-events:none;box-shadow:0 16px 48px rgba(0,0,0,.45);opacity:0;transition:opacity 120ms ease";
  document.body.appendChild(pop);
  let owner = null;
  const fine = window.matchMedia && window.matchMedia("(pointer: fine)").matches;
  function place(x, y) {
    const pr = pop.getBoundingClientRect();
    let left = x + 14, top = y + 16;
    if (left + pr.width > window.innerWidth - 8) left = x - pr.width - 14;
    if (top + pr.height > window.innerHeight - 8) top = y - pr.height - 12;
    pop.style.left = Math.max(8, left) + "px";
    pop.style.top = Math.max(8, top) + "px";
  }
  function show(t, x, y) {
    const txt = t.dataset.tip || "";
    if (!txt) return;
    if (pop.textContent !== txt) pop.textContent = txt;
    pop.style.display = "block"; place(x, y); owner = t;
    requestAnimationFrame(() => { pop.style.opacity = "1"; });
  }
  function hide() {
    pop.style.opacity = "0"; owner = null;
    setTimeout(() => { if (!owner) pop.style.display = "none"; }, 130);   // 淡出播完再摘
  }
  document.addEventListener("mouseover", e => { const t = e.target.closest && e.target.closest(".i-tip"); if (t) show(t, e.clientX, e.clientY); });
  document.addEventListener("mousemove", e => { const t = e.target.closest && e.target.closest(".i-tip"); if (t && t === owner) place(e.clientX, e.clientY); });
  document.addEventListener("mouseout", e => { if (e.target.closest && e.target.closest(".i-tip")) hide(); });
  document.addEventListener("click", e => {
    const t = e.target.closest ? e.target.closest(".i-tip") : null;
    if (t) { (!fine || t !== owner) ? show(t, e.clientX, e.clientY) : hide(); } else if (owner) hide();
  });
})();

/* ── 拖拽自动滚动：上下 1/4 视口检测区，dragover 驱动按深入程度滚动（非拖拽零开销） ── */
(function () {
  let dragging = false;
  document.addEventListener("dragstart", () => { dragging = true; }, true);
  document.addEventListener("dragend", () => { dragging = false; }, true);
  document.addEventListener("dragover", e => {
    if (!dragging) return;
    const zone = innerHeight / 4;
    let d = 0;
    if (e.clientY < zone) d = -Math.ceil((zone - e.clientY) / 6);
    else if (e.clientY > innerHeight - zone) d = Math.ceil((e.clientY - (innerHeight - zone)) / 6);
    if (d) scrollBy(0, d);
  });
})();

/* ── 分组收纳套件（模型管理/模型池两页共用，单源；数据 gw_groups + 服务端镜像 ui_groups）──
   重挂载钩子：HMG.grpReload 由页面设置（分组变化后重渲本页列表） */
const GRP_KEY = "gw_groups";
function grpData() { try { return JSON.parse(localStorage.getItem(GRP_KEY)) || {}; } catch (e) { return {}; } }
function grpSave(d) { localStorage.setItem(GRP_KEY, JSON.stringify(d)); HMG.api("/admin/ui_groups", { method: "PUT", body: JSON.stringify(d) }); }
async function gwGroupsBoot() {
  try {
    const d = await HMG.api("/admin/ui_groups");
    if (!d) return;
    const sv = d.ui_groups || {};
    const has = o => Object.keys(o).some(k => k !== "settings" && Array.isArray(o[k]) && o[k].length)
      || (o.settings && (o.settings.models || o.settings.pools));
    if (has(sv)) localStorage.setItem(GRP_KEY, JSON.stringify(sv));
    else if (has(grpData())) HMG.api("/admin/ui_groups", { method: "PUT", body: JSON.stringify(grpData()) });
  } catch (e) { /* 分组同步失败不阻塞 */ }
}
function grpSettings() { return Object.assign({ models: false, pools: false }, grpData().settings || {}); }
function grpList(kind) {
  const d = grpData();
  if (!Array.isArray(d[kind])) { d[kind] = []; grpSave(d); }
  return d[kind];
}
function grpReload() { if (typeof HMG.grpReload === "function") HMG.grpReload(); }
/* 分组渲染：items=[{id,html}] 按全局顺序；开启时输出「分组区段+未分组区段」，关闭时调用方自行平铺 */
function grpWrapHtml(kind, items) {
  const d = grpData();
  if (!Array.isArray(d[kind])) d[kind] = [];
  const groups = d[kind];
  const ids = new Set(items.map(i => i.id));
  let dirty = false;
  groups.forEach(g => {
    if (!Array.isArray(g.members)) { g.members = []; dirty = true; }
    const kept = g.members.filter(m => ids.has(m));
    if (kept.length !== g.members.length) { g.members = kept; dirty = true; }
  });
  if (dirty) grpSave(d);
  const inGroup = new Set();
  groups.forEach(g => (g.members || []).forEach(m => inGroup.add(m)));
  let out = "";
  groups.forEach(g => {
    const members = items.filter(i => (g.members || []).includes(i.id)).map(i => i.html).join("");
    out += `
    <div class="grp-section${g.collapsed ? " collapsed" : ""}" data-grp="${HMG.esc(g.id)}">
      <div class="grp-head" onclick="grpToggle('${kind}','${HMG.esc(g.id)}')" title="点击折叠 / 展开">
        <span class="grp-caret">▼</span>
        <span class="side-ic" style="margin-right:-2px">${HMG_ICONS.folder}</span>
        <span class="grp-name" ondblclick="event.stopPropagation();grpRename('${kind}','${HMG.esc(g.id)}')" title="双击重命名">${HMG.esc(g.name)}</span>
        <span class="grp-count">${(g.members || []).length}</span>
        <button class="grp-x" title="删除分组（成员回到未分组）" onclick="event.stopPropagation();grpDelGroup('${kind}','${HMG.esc(g.id)}')">✕</button>
      </div>
      <div class="grp-body">${members || '<div class="grp-empty">把卡片拖进来，或点卡片上的 📂 归组</div>'}</div>
    </div>`;
  });
  const restItems = items.filter(i => !inGroup.has(i.id));
  out += `
    <div class="grp-section grp-ungrouped" data-grp="__ungrouped__">
      <div class="grp-head" onclick="this.closest('.grp-section').classList.toggle('collapsed')" title="点击折叠 / 展开">
        <span class="grp-caret">▼</span>
        <span class="grp-name">未分组</span>
        <span class="grp-count">${restItems.length}</span>
      </div>
      <div class="grp-body">${restItems.map(i => i.html).join("") || '<div class="grp-empty">全部已归组</div>'}</div>
    </div>`;
  return out;
}
/* 拖拽收尾：以拖放后的 DOM 实际位置回写分组归属 */
function grpSyncFromDOM(kind, listEl, cardSel, attr) {
  const d = grpData();
  if (!Array.isArray(d[kind])) return;
  const byGroup = {};
  listEl.querySelectorAll(".grp-section[data-grp]").forEach(sec => {
    if (sec.dataset.grp === "__ungrouped__") return;
    byGroup[sec.dataset.grp] = [...sec.querySelectorAll(cardSel)].map(c => c.dataset[attr]);
  });
  d[kind].forEach(g => { g.members = byGroup[g.id] || []; });
  grpSave(d);
}
function grpToggle(kind, gid) {
  const d = grpData(); const g = (d[kind] || []).find(x => x.id === gid);
  if (!g) return;
  g.collapsed = !g.collapsed; grpSave(d);
  const el = document.querySelector(`.grp-section[data-grp="${CSS.escape(gid)}"]`);
  if (el) el.classList.toggle("collapsed", g.collapsed);
}
async function grpDelGroup(kind, gid) {
  const d = grpData(); const g = (d[kind] || []).find(x => x.id === gid);
  if (!g) return;
  if (!await HMG.confirm(`删除分组「${HMG.esc(g.name)}」？其成员将回到未分组（供应商/池本身不受影响）。`, { danger: true, okLabel: "删除" })) return;
  d[kind] = (d[kind] || []).filter(x => x.id !== gid); grpSave(d); grpReload();
}
async function grpRename(kind, gid) {
  const d = grpData(); const g = (d[kind] || []).find(x => x.id === gid);
  if (!g) return;
  const nn = await HMG.prompt("新的分组名称：", { value: g.name });
  if (!nn || nn.trim() === g.name || !nn.trim()) return;
  g.name = nn.trim(); grpSave(d); grpReload();
}
function grpToggleMember(kind, id, gid, checked) {
  const d = grpData(); if (!Array.isArray(d[kind])) d[kind] = [];
  const g = d[kind].find(x => x.id === gid);
  if (!g) return;
  g.members = g.members || [];
  g.members = checked ? [...new Set([...g.members, id])] : g.members.filter(m => m !== id);
  grpSave(d); grpReload();
}
function grpCreateAssign(kind, id) {
  const inp = document.getElementById("grp-new-name");
  const name = inp && inp.value.trim();
  if (!name) { HMG.toast("请输入分组名称", "err"); return; }
  const d = grpData(); if (!Array.isArray(d[kind])) d[kind] = [];
  if (d[kind].some(g => g.name === name)) { HMG.toast("同名分组已存在", "err"); return; }
  d[kind].push({ id: "g" + Date.now().toString(36), name, collapsed: false, members: [id] });
  grpSave(d); grpReload();
}
function grpAssignMenu(ev, kind, id) {
  ev.stopPropagation(); ev.preventDefault();
  HMG.menu(ev.currentTarget || ev.target, grpList(kind).map(g => ({
    label: ((g.members || []).includes(id) ? "✓ " : "　") + g.name + `（${(g.members || []).length}）`,
    onClick: () => grpToggleMember(kind, id, g.id, !(g.members || []).includes(id)),
  })).concat([{
    label: "＋ 新建分组并加入…",
    onClick: async () => {
      const nn = await HMG.prompt("新的分组名称：", { placeholder: "如：主力供应商" });
      if (!nn || !nn.trim()) return;
      const d = grpData(); if (!Array.isArray(d[kind])) d[kind] = [];
      if (d[kind].some(g => g.name === nn.trim())) { HMG.toast("同名分组已存在", "err"); return; }
      d[kind].push({ id: "g" + Date.now().toString(36), name: nn.trim(), collapsed: false, members: [id] });
      grpSave(d); grpReload();
    },
  }]));
}
/* v2.16.7/8 分组收纳设置弹窗：两维度开关 + 每行 ⚙️ 管理（排序/重命名/删除） */
const GRP_MGMT_KINDS = [["providerGroups", "模型管理"], ["poolGroups", "模型池"]];
let _grpMgmtOpen = "";
function grpMgmtToggle(kind) { _grpMgmtOpen = _grpMgmtOpen === kind ? "" : kind; grpMgmtRender(); }
function grpMgmtRender() {
  document.querySelectorAll(".grp-row-cog").forEach(b => b.classList.toggle("on", b.dataset.kind === _grpMgmtOpen));
  GRP_MGMT_KINDS.forEach(([kind, label]) => {
    const box = document.getElementById("grp-mgmt-" + kind);
    if (!box) return;
    if (_grpMgmtOpen !== kind) { box.hidden = true; box.innerHTML = ""; return; }
    const gs = grpData()[kind] || [];
    box.hidden = false;
    box.innerHTML = `<div class="grp-mgmt-title">「${HMG.esc(label)}」分组（${gs.length}）</div>` + (gs.length ? gs.map((g, i) => `
      <div class="grp-mgmt-row">
        <span class="grp-mgmt-ops">
          <button title="上移（展示顺序前移）"${i === 0 ? " disabled" : ""} onclick="grpMgmtMove('${kind}','${HMG.esc(g.id)}',-1)">↑</button>
          <button title="下移（展示顺序后移）"${i === gs.length - 1 ? " disabled" : ""} onclick="grpMgmtMove('${kind}','${HMG.esc(g.id)}',1)">↓</button>
          <button class="grp-mgmt-del" title="删除分组（成员卡片回到未分组，不影响模型/池本身）" onclick="grpMgmtDelete('${kind}','${HMG.esc(g.id)}')">🗑</button>
        </span>
        <span class="grp-mgmt-name" title="点击重命名" onclick="grpMgmtRename('${kind}','${HMG.esc(g.id)}')">${HMG.esc(g.name)}</span>
        <span class="grp-mgmt-count">${(g.members || []).length} 成员</span>
      </div>`).join("") : '<div class="grp-mgmt-empty">暂无分组——启用收纳后点卡片 📂 创建</div>');
  });
}
function grpMgmtMove(kind, gid, dir) {
  const d = grpData(); const gs = d[kind];
  if (!Array.isArray(gs)) return;
  const i = gs.findIndex(x => x.id === gid); const j = i + dir;
  if (i < 0 || j < 0 || j >= gs.length) return;
  [gs[i], gs[j]] = [gs[j], gs[i]];
  grpSave(d); grpMgmtRender(); grpReload();
}
function grpMgmtRename(kind, gid) { grpRename(kind, gid); grpMgmtRender(); }
async function grpMgmtDelete(kind, gid) {
  const d = grpData(); const gs = d[kind];
  const g = Array.isArray(gs) ? gs.find(x => x.id === gid) : null;
  if (!g) return;
  if (!await HMG.confirm(`删除分组「${HMG.esc(g.name)}」？组内 ${(g.members || []).length} 个成员卡片将回到未分组（模型/池本身不受影响）。`, { danger: true, okLabel: "删除" })) return;
  d[kind] = gs.filter(x => x.id !== gid);
  grpSave(d); grpMgmtRender(); grpReload();
}
function grpOpenSettings() {
  const m = HMG.modal({
    title: "⚙️ 分组收纳设置",
    body: `
      <label class="chk" style="justify-content:space-between"><span style="display:flex;align-items:center;gap:6px"><input type="checkbox" id="grp-set-models"> 启用「模型管理」分组收纳</span>
        <button type="button" class="btn btn-ghost btn-sm grp-row-cog" data-kind="providerGroups" onclick="grpMgmtToggle('providerGroups')">⚙️ 管理</button></label>
      <div class="grp-mgmt-box" id="grp-mgmt-providerGroups" hidden></div>
      <label class="chk" style="justify-content:space-between"><span style="display:flex;align-items:center;gap:6px"><input type="checkbox" id="grp-set-pools"> 启用「模型池」分组收纳</span>
        <button type="button" class="btn btn-ghost btn-sm grp-row-cog" data-kind="poolGroups" onclick="grpMgmtToggle('poolGroups')">⚙️ 管理</button></label>
      <div class="grp-mgmt-box" id="grp-mgmt-poolGroups" hidden></div>
      <div class="hint">分组数据服务端镜像（全端共享），不影响调用、配额与后端排序持久化。「📊 分组查看」在用量统计页随时可用。</div>`,
    footer: [{ label: "关闭", cls: "btn btn-secondary", onClick: ({ close }) => close() }],
  });
  const s = grpSettings();
  m.body.querySelector("#grp-set-models").checked = s.models;
  m.body.querySelector("#grp-set-pools").checked = s.pools;
  m.body.querySelector("#grp-set-models").addEventListener("change", e => grpSetToggle("models", e.target.checked));
  m.body.querySelector("#grp-set-pools").addEventListener("change", e => grpSetToggle("pools", e.target.checked));
  grpMgmtRender();
}
function grpSetToggle(key, val) {
  const d = grpData();
  d.settings = Object.assign({ models: false, pools: false }, d.settings || {});
  d.settings[key] = val; grpSave(d); grpReload();
}
/* 分组区段折叠/管理盒样式（hmg.css 中 grp-* 段配套） */
window.addEventListener("DOMContentLoaded", () => { /* 保留挂载点：grp 样式在 hmg.css */ });

/* ── 缓存计费预估弹窗（模型页「⚙ 齿轮」与统计页「💰 预估费用」共用，单源）──
   依赖：页面提供 window.cachedModels（保存价格后同步编辑表单回填缓存，可缺省）与 window.editingId（齿轮入口守卫） */
let costEstId = null, costHours = [], costRange = 1, _costModal = null;
function _bjDateStr(offsetDays = 0) {
  const d = new Date(Date.now() + offsetDays * 86400000);
  return new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Shanghai" }).format(d);
}
function _costNum(id) { const v = parseFloat(document.getElementById(id).value); return Number.isFinite(v) ? Math.max(0, v) : 0; }
function _hhmm(v, fallback) { const s = (v || "").trim(); return /^\d{1,2}:\d{2}$/.test(s) ? s.padStart(5, "0") : fallback; }
/* 高峰时段自适应解析：段间 ; ； , ， 换行；区间符 - — － ~ ～ → 至 到；时间 9:00/09:00/9:30。返回 [[起分钟,止分钟],...]（起≥止=跨零点段） */
function _parsePeakWindows(str) {
  const out = [];
  String(str || "").split(/[;；,，\n]+/).forEach(seg => {
    const m = seg.trim().match(/^(\d{1,2})(?::(\d{1,2}))?\s*(?:-|—|－|~|～|→|至|到)\s*(\d{1,2})(?::(\d{1,2}))?$/);
    if (!m) return;
    const h1 = +m[1], mi1 = +(m[2] || 0), h2 = +m[3], mi2 = +(m[4] || 0);
    if (h1 > 23 || h2 > 23 || mi1 > 59 || mi2 > 59) return;
    const a = h1 * 60 + mi1, b = h2 * 60 + mi2;
    if (a === b) return;
    out.push([a, b]);
  });
  return out;
}
function _fmtMin(v) { return String(Math.floor(v / 60)).padStart(2, "0") + ":" + String(v % 60).padStart(2, "0"); }
function _peakLabel(wins) { return wins.map(w => _fmtMin(w[0]) + "-" + _fmtMin(w[1])).join(";"); }
function _normPeakWeekdays(str) {
  const seen = new Set();
  String(str || "").split("").forEach(c => { const n = +c; if (c !== " " && n >= 1 && n <= 7) seen.add(n); });
  return Array.from(seen).sort((a, b) => a - b).join("");
}
function _validMD(s4) { const mm = +s4.slice(0, 2), dd = +s4.slice(2); return mm >= 1 && mm <= 12 && dd >= 1 && dd <= 31; }
function _normPeakExdates(str) {
  const seen = new Set(), items = [];
  String(str || "").split(/[;；,，\s]+/).forEach(raw => {
    const seg = raw.trim();
    if (!seg) return;
    const m = seg.match(/^(\d{4})\s*(?:-|—|－|~|～|至|到)\s*(\d{4})$/);
    if (m && _validMD(m[1]) && _validMD(m[2])) {
      const key = "r" + m[1] + m[2];
      if (!seen.has(key)) { seen.add(key); items.push({ key, text: m[1] + "-" + m[2], start: +m[1] }); }
      return;
    }
    if (/^\d{4}$/.test(seg) && _validMD(seg) && !seen.has(seg)) { seen.add(seg); items.push({ key: seg, text: seg, start: +seg }); }
  });
  items.sort((a, b) => a.start - b.start);
  return items.map(x => x.text).join(";");
}
/* 规范化谷峰串 → {MMDD 整型} 集合（区间含首尾展开；起>止=跨年；与后端同规则） */
function _expandExdates(normStr) {
  const out = new Set();
  String(normStr || "").split(";").forEach(tok => {
    if (!tok) return;
    const m = tok.match(/^(\d{4})-(\d{4})$/);
    if (m) {
      let cur = new Date(2000, +m[1].slice(0, 2) - 1, +m[1].slice(2));
      const end = new Date(2000, +m[2].slice(0, 2) - 1, +m[2].slice(2));
      const add = () => { out.add((cur.getMonth() + 1) * 100 + cur.getDate()); };
      if (cur <= end) { while (cur <= end) { add(); cur.setDate(cur.getDate() + 1); } }
      else {
        const eoy = new Date(2000, 11, 31);
        while (cur <= eoy) { add(); cur.setDate(cur.getDate() + 1); }
        cur = new Date(2000, 0, 1);
        while (cur <= end) { add(); cur.setDate(cur.getDate() + 1); }
      }
      return;
    }
    out.add(+tok);
  });
  return out;
}
function _inPeakWins(minute, wins) {
  return wins.some(([a, b]) => a < b ? (minute >= a && minute < b) : (minute >= a || minute < b));
}
function setCostRange(r) {
  costRange = r;
  document.querySelectorAll("#cost-range-seg button").forEach(b => b.classList.toggle("on", +b.dataset.r === r));
  recalcCost();
}
function togglePeakFields() {
  const f = document.getElementById("cost-peak-fields");
  if (f) f.classList.toggle("hidden", !document.getElementById("cost-peak-on").checked);
}
async function showCostEst(modelId) {
  const d = await HMG.api("/admin/model/" + HMG.pathEnc(modelId) + "/cost?hours=" + 24 * 30);
  if (!d) return;
  costEstId = modelId;
  costHours = d.hours || [];
  if (_costModal) _costModal.close();
  _costModal = HMG.modal({
    title: "💰 缓存计费预估",
    wide: true,
    body: `
      <div class="badge" id="cost-model-badge" style="max-width:340px;overflow:hidden;text-overflow:ellipsis;margin-bottom:8px"></div>
      <div class="hint" style="margin-bottom:10px">该模型按小时桶统计的<b>缓存命中 / 未命中 / 输出</b> token（在模型编辑勾选「缓存 Token 计费统计」后开始记录）。价格单位 ¥/百万 token，改动即时对所选时段重算；上游未回报缓存命中时按命中 0 计。</div>
      <div class="seg" id="cost-range-seg">
        <button class="on" data-r="1" onclick="setCostRange(1)">今日</button>
        <button data-r="7" onclick="setCostRange(7)">近 7 天</button>
        <button data-r="30" onclick="setCostRange(30)">近 30 天</button>
      </div>
      <div id="cost-summary" style="margin:12px 0 4px"></div>
      <div class="form-sec">价格（¥ / 百万 token）</div>
      <div class="frm-row3">
        <div class="fg"><label>缓存命中 单价</label><input type="number" id="cost-p-hit" class="inp" step="0.01" min="0" oninput="recalcCost()"></div>
        <div class="fg"><label>缓存未命中 单价</label><input type="number" id="cost-p-miss" class="inp" step="0.01" min="0" oninput="recalcCost()"></div>
        <div class="fg"><label>输出 单价</label><input type="number" id="cost-p-out" class="inp" step="0.01" min="0" oninput="recalcCost()"></div>
      </div>
      <label class="chk" style="margin:4px 0 10px"><input type="checkbox" id="cost-peak-on" onchange="togglePeakFields();recalcCost()"><span><b>高峰期分段计价</b>勾选后高峰时段内的用量按高峰单价计算（按小时桶归属；支持多段与跨零点）</span></label>
      <div id="cost-peak-fields" class="hidden">
        <div class="fg"><label>每日高峰时段（多段用分号分隔，北京时间）</label><input id="cost-peak-windows" class="inp" placeholder="如 9:00-12:00;14:00-16:00（段内起≥止视为跨零点，如 21:00-6:00；留空=当天全天）" oninput="recalcCost()"><div class="hint">自适应解析：段间 <b>; ； , ，</b> 均可；区间符 <b>- — ~ ～ 至 到</b> 均可；时间 <b>9:00 / 09:00 / 9:30</b> 均可；无法解析的段自动忽略</div></div>
        <div class="frm-row">
          <div class="fg"><label>每周高峰（周几，1=周一 … 7=周日）</label><input id="cost-peak-weekdays" class="inp" placeholder="如 12345（周一至周五；留空=每天）" oninput="recalcCost()"></div>
          <div class="fg"><label>特定谷峰日（月日四位，每年循环）</label><input id="cost-peak-exdates" class="inp" placeholder="如 1001-1007;0930（区间含首尾，起>止=跨年经元旦）" oninput="recalcCost()"></div>
        </div>
        <div class="hint" style="margin:2px 0 8px">与模型编辑（付费模型）的高峰期为同一份数据（存储一处，两处修改互通）。高峰 = 每周高峰日 ∩ 每日高峰时段 − 特定谷峰日；仅配置谷峰日不构成高峰。</div>
        <div class="frm-row3">
          <div class="fg"><label>高峰·缓存命中 单价</label><input type="number" id="cost-peak-hit" class="inp" step="0.01" min="0" oninput="recalcCost()"></div>
          <div class="fg"><label>高峰·缓存未命中 单价</label><input type="number" id="cost-peak-miss" class="inp" step="0.01" min="0" oninput="recalcCost()"></div>
          <div class="fg"><label>高峰·输出 单价</label><input type="number" id="cost-peak-out" class="inp" step="0.01" min="0" oninput="recalcCost()"></div>
        </div>
      </div>
      <div id="cost-breakdown"></div>`,
    footer: [
      { label: "关闭", cls: "btn btn-secondary", onClick: ({ close }) => close() },
      { label: "保存价格", cls: "btn btn-primary", onClick: () => saveCostPrices() },
    ],
  });
  _costModal.body.querySelector("#cost-model-badge").textContent = modelId;
  const p = d.prices || {}, pk = d.peak || {};
  document.getElementById("cost-p-hit").value = p.hit ?? 0;
  document.getElementById("cost-p-miss").value = p.miss ?? 0;
  document.getElementById("cost-p-out").value = p.out ?? 0;
  document.getElementById("cost-peak-on").checked = !!pk.enabled;
  document.getElementById("cost-peak-windows").value =
    pk.windows || (pk.start && pk.end ? _hhmm(pk.start, "09:00") + "-" + _hhmm(pk.end, "21:00") : "09:00-21:00");
  document.getElementById("cost-peak-weekdays").value = pk.weekdays || "";
  document.getElementById("cost-peak-exdates").value = pk.exdates || "";
  document.getElementById("cost-peak-hit").value = pk.hit ?? p.hit ?? 0;
  document.getElementById("cost-peak-miss").value = pk.miss ?? p.miss ?? 0;
  document.getElementById("cost-peak-out").value = pk.out ?? p.out ?? 0;
  togglePeakFields();
  recalcCost();
}
function _costPeakCfg() {
  const wins = _parsePeakWindows(document.getElementById("cost-peak-windows").value);
  const wds = _normPeakWeekdays(document.getElementById("cost-peak-weekdays").value);
  const exLabel = _normPeakExdates(document.getElementById("cost-peak-exdates").value);
  const exs = _expandExdates(exLabel);
  return { wins, wds, exs, exLabel };
}
function _inCostPeak(hourKey, cfg) {
  if (!cfg.wins.length && !cfg.wds.length) return false;
  const ds = String(hourKey).slice(0, 10);
  if (cfg.exs.has((+ds.slice(5, 7)) * 100 + (+ds.slice(8, 10)))) return false;
  if (cfg.wds.length) {
    const d = new Date(ds + "T00:00:00");
    if (!cfg.wds.includes(String(d.getDay() === 0 ? 7 : d.getDay()))) return false;
  }
  return !cfg.wins.length || _inPeakWins(parseInt(String(hourKey).slice(11, 13), 10) * 60, cfg.wins);
}
function recalcCost() {
  const el = document.getElementById("cost-summary"), bd = document.getElementById("cost-breakdown");
  if (!el || !costEstId) return;
  const days = new Set(Array.from({ length: costRange }, (_, i) => _bjDateStr(-i)));
  const rows = costHours.filter(h => days.has(String(h.hour_key).slice(0, 10)));
  const t = { calls: 0, hit: 0, miss: 0, out: 0 }, pkT = { calls: 0, hit: 0, miss: 0, out: 0 };
  const peakOn = document.getElementById("cost-peak-on").checked;
  const pkCfg = _costPeakCfg();
  for (const h of rows) {
    const isPeak = peakOn && _inCostPeak(h.hour_key, pkCfg);
    const dst = isPeak ? pkT : null;
    t.calls += h.calls || 0; t.hit += h.hit_tokens || 0; t.miss += h.miss_tokens || 0; t.out += h.out_tokens || 0;
    if (dst) { dst.calls += h.calls || 0; dst.hit += h.hit_tokens || 0; dst.miss += h.miss_tokens || 0; dst.out += h.out_tokens || 0; }
  }
  const p = { hit: _costNum("cost-p-hit"), miss: _costNum("cost-p-miss"), out: _costNum("cost-p-out") };
  const pk = peakOn ? { hit: _costNum("cost-peak-hit"), miss: _costNum("cost-peak-miss"), out: _costNum("cost-peak-out") } : p;
  const offT = { calls: t.calls - pkT.calls, hit: t.hit - pkT.hit, miss: t.miss - pkT.miss, out: t.out - pkT.out };
  const _c = (tok, price) => tok / 1e6 * price;
  const _row = (name, tok, price, color) =>
    '<tr><td style="padding:5px 8px">' + name + '</td>' +
    '<td style="padding:5px 8px;text-align:right" class="mono">' + HMG.num(tok) + ' tok</td>' +
    '<td style="padding:5px 8px;text-align:right" class="mono">¥' + price.toFixed(2) + ' /M</td>' +
    '<td style="padding:5px 8px;text-align:right;color:' + color + '" class="mono">¥' + _c(tok, price).toFixed(4) + "</td></tr>";
  const _sumRow = (name, calls, cost, color) =>
    '<tr><td style="padding:5px 8px">' + name + "</td>" +
    '<td style="padding:5px 8px;text-align:right" class="mono">' + HMG.num(calls) + " 次</td>" +
    '<td style="padding:5px 8px;text-align:right" class="mono">—</td>' +
    '<td style="padding:5px 8px;text-align:right;color:' + color + '" class="mono">¥' + cost.toFixed(4) + "</td></tr>";
  const fmt = HMG.num;
  el.innerHTML = rows.length || costRange === 1
    ? '<div style="font-size:13px;line-height:2">所选时段：<b>' + fmt(t.calls) + '</b> 次调用 · 缓存命中 <b style="color:var(--success)">' + fmt(t.hit) + '</b> tok · 未命中 <b style="color:var(--warning)">' + fmt(t.miss) + '</b> tok · 输出 <b style="color:var(--accent-hover)">' + fmt(t.out) + "</b> tok</div>"
    : '<div class="hint">近 ' + costRange + " 天暂无统计（需在模型编辑勾选「缓存 Token 计费统计」并产生调用）</div>";
  let total = _c(t.hit, p.hit) + _c(t.miss, p.miss) + _c(t.out, p.out);
  let html = '<div style="font-size:12.5px;color:var(--ink-subtle);margin:10px 0 6px">费用明细（当前输入框价格实时计算）</div>' +
    '<div class="tbl-wrap" style="border:1px solid var(--hairline);border-radius:8px"><table class="tbl">' +
    "<thead><tr><th>项目</th><th class=\"r\">token</th><th class=\"r\">单价</th><th class=\"r\">费用</th></tr></thead><tbody>" +
    _row("缓存命中", t.hit, p.hit, "var(--success)") + _row("缓存未命中", t.miss, p.miss, "var(--warning)") + _row("输出", t.out, p.out, "var(--accent-hover)");
  if (peakOn) {
    const pkCost = _c(pkT.hit, pk.hit) + _c(pkT.miss, pk.miss) + _c(pkT.out, pk.out);
    const offCost = _c(offT.hit, p.hit) + _c(offT.miss, p.miss) + _c(offT.out, p.out);
    total = pkCost + offCost;
    let pkDesc = pkCfg.wins.length ? _peakLabel(pkCfg.wins) : "";
    if (pkCfg.wds) pkDesc += (pkDesc ? " · " : "") + "周" + pkCfg.wds;
    if (pkCfg.exs.size) pkDesc += (pkDesc ? " · " : "") + "谷峰" + pkCfg.exLabel;
    html += '<tr><td colspan="4" style="padding:2px 8px"></td></tr>' +
      (pkCfg.wins.length || pkCfg.wds.length
        ? _sumRow("高峰小计（" + pkDesc + "）", pkT.calls, pkCost, "var(--danger)") +
          _sumRow("平峰小计", offT.calls, offCost, "var(--ink-subtle)")
        : '<tr><td colspan="4" style="padding:5px 8px;color:var(--danger)">⚠ 高峰时段无法解析出有效配置（已按全平峰计算）——请按「9:00-12:00;14:00-16:00」格式填写每日时段，或填每周高峰（如 12345）</td></tr>' +
          _sumRow("全平峰小计（时段未生效）", t.calls, total, "var(--danger)"));
  }
  html += "</tbody></table></div>" +
    '<div style="font-size:14px;margin-top:10px">预估总费用：<b style="color:var(--danger);font-size:17px" class="num">¥' + total.toFixed(4) + "</b>" + (peakOn ? "（高峰分段计价）" : "") + "</div>";
  bd.innerHTML = html;
}
async function saveCostPrices() {
  if (!costEstId) return;
  const peakOn = document.getElementById("cost-peak-on").checked;
  const winsRaw = (document.getElementById("cost-peak-windows").value || "").trim();
  const wins = _parsePeakWindows(winsRaw);
  const wdNorm = _normPeakWeekdays(document.getElementById("cost-peak-weekdays").value);
  const exNorm = _normPeakExdates(document.getElementById("cost-peak-exdates").value);
  if (peakOn && !wins.length && !wdNorm.length) {
    HMG.toast("高峰期无法解析出有效配置：请填每日时段（如 9:00-12:00;14:00-16:00）或每周高峰（如 12345）", "err");
    return;
  }
  const body = {
    cost_prices: { hit: _costNum("cost-p-hit"), miss: _costNum("cost-p-miss"), out: _costNum("cost-p-out") },
    cost_peak: {
      enabled: peakOn,
      windows: peakOn ? _peakLabel(wins) : winsRaw,
      weekdays: peakOn ? wdNorm : (document.getElementById("cost-peak-weekdays").value || "").trim(),
      exdates: peakOn ? exNorm : (document.getElementById("cost-peak-exdates").value || "").trim(),
      hit: _costNum("cost-peak-hit"), miss: _costNum("cost-peak-miss"), out: _costNum("cost-peak-out"),
    },
  };
  const ok = await HMG.api("/admin/models/" + HMG.pathEnc(costEstId), { method: "PUT", body: JSON.stringify(body) });
  if (!ok) return;
  const _cm = (window.cachedModels || []).find(x => x.id === costEstId);
  if (_cm) _cm.cost_peak = body.cost_peak;
  HMG.toast("价格已保存（模型：" + costEstId + "）");
}
/* 模型编辑表单里「缓存 Token 计费统计」的小齿轮入口（按钮在 label 内，拦截默认行为防误触勾选框） */
function openCostSettings(ev) {
  ev.preventDefault(); ev.stopPropagation();
  if (!window.editingId) { HMG.toast("模型尚未保存：请先保存模型，再配置单价与高峰期", "err"); return; }
  showCostEst(window.editingId);
}

/* ── 🪴 Headroom 节省统计（统计页「节省统计」入口 + 插件中心 Headroom 设置弹窗共用，单源）──
   入口按钮显隐：#btn-hr-stats 存在的页面才生效（统计页）；__hrStatsOn=false 时隐藏 */
let __hrStatsOn = null;
function applyHrStatsBtn() {
  const b = document.getElementById("btn-hr-stats");
  if (b) b.style.display = __hrStatsOn === false ? "none" : "";
}
async function refreshHrStatsBtn(force) {
  if (__hrStatsOn !== null && !force) { applyHrStatsBtn(); return; }
  const d = await HMG.api("/admin/headroom");
  if (!d) return;
  __hrStatsOn = !!d.enabled;
  applyHrStatsBtn();
}
async function showHeadroomStats() {
  const d = await HMG.api("/admin/headroom/stats?days=7");
  if (!d) return;
  const st = d.enabled
    ? (d.mode === "dry_run" ? "✅ 已启用 · dry_run（只统计不改写，灰度观察）" : "✅ 已启用 · live（出站消息实际压缩）")
    : "⛔ 未启用：config.json 里 headroom.enabled 改 true 并 /admin/reload，且目标模型在编辑里勾选「Headroom 上下文压缩」";
  const t = d.total || {}, td = d.today || {};
  const fmt = n => (n || 0).toLocaleString();
  const bm = d.by_model || [];
  const rc = d.recent || [];
  const bmHtml = bm.length ?
    '<div style="font-size:12.5px;color:var(--ink-subtle);margin:10px 0 6px">按模型（近 ' + d.window_days + ' 天，成功压缩）</div>' +
    '<div class="tbl-wrap" style="border:1px solid var(--hairline);border-radius:8px;max-height:200px;overflow:auto"><table class="tbl">' +
    '<thead><tr><th>模型</th><th class="r">次数</th><th class="r">节省 tok</th><th class="r">平均压缩率</th></tr></thead><tbody>' +
    bm.map(m => `<tr><td class="mono">${HMG.esc(m.model)}</td><td class="r mono">${fmt(m.requests)}</td><td class="r mono" style="color:var(--success)">${fmt(m.saved)}</td><td class="r mono">${((m.avg_ratio || 0) * 100).toFixed(1)}%</td></tr>`).join("") +
    "</tbody></table></div>" : "";
  const rcHtml = rc.length ?
    '<div style="font-size:12.5px;color:var(--ink-subtle);margin:10px 0 6px">最近记录（≤50 条，含 dry_run 与异常旁路）</div>' +
    '<div class="tbl-wrap" style="border:1px solid var(--hairline);border-radius:8px;max-height:260px;overflow:auto"><table class="tbl">' +
    '<thead><tr><th>时间</th><th>池 / 模型</th><th>模式</th><th class="r">前 → 后</th><th class="r">节省</th><th class="r">耗时</th></tr></thead><tbody>' +
    rc.map(r => {
      const tm = new Date(r.ts * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
      const mode = r.error ? '<span style="color:var(--danger)">✗ ' + HMG.esc(r.error.slice(0, 30)) + "</span>" : HMG.esc(r.mode);
      return `<tr><td class="mono">${tm}</td><td class="mono">${HMG.esc(r.pool)} / ${HMG.esc(r.model)}</td><td>${mode}</td><td class="r mono">${fmt(r.tokens_before)} → ${fmt(r.tokens_after)}</td><td class="r mono" style="color:var(--success)">${fmt(r.tokens_saved)}</td><td class="r mono">${(r.latency_ms || 0).toFixed(0)}ms</td></tr>`;
    }).join("") +
    "</tbody></table></div>" : "";
  HMG.modal({
    title: "🪴 Headroom 上下文压缩 · 节省统计",
    wide: true,
    body: `
      <div style="font-size:12.5px;line-height:1.9">
        <div>插件状态：<b style="color:${d.enabled ? "var(--success)" : "var(--danger)"}">${st}</b></div>
        <div>今日节省 <b style="color:var(--success)" class="num">${fmt(td.saved)}</b> tok（${fmt(td.requests)} 次）　|　近 ${d.window_days} 天节省 <b style="color:var(--success)" class="num">${fmt(t.saved)}</b> tok（压缩前 ${fmt(t.tokens_before)} → 压缩后 ${fmt(t.tokens_after)}）</div>
        <div>压缩 ${fmt(t.requests)} 次 · 旁路/异常 ${fmt(t.errors)} 次 · 耗时均值 ${(t.avg_latency_ms || 0).toFixed(0)}ms / 峰值 ${(t.max_latency_ms || 0).toFixed(0)}ms</div>
      </div>
      ${bmHtml}${rcHtml}`,
    footer: [{ label: "关闭", cls: "btn btn-secondary", onClick: ({ close }) => close() }],
  });
}
