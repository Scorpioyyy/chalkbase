"use strict";
/* ChalkBase 可视化：公共工具。全局命名空间 VC。 */
const VC = { S: {}, handlers: {} };

const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
VC.reduced = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;

/** 创建元素：el("div", {class:"x", onclick:fn}, [子节点或字符串]) */
function el(tag, attrs, kids) {
  const e = document.createElement(tag);
  for (const k in attrs || {}) {
    const v = attrs[k];
    if (v == null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "html") e.innerHTML = v;
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of [].concat(kids == null ? [] : kids)) e.append(c.nodeType ? c : document.createTextNode(c));
  return e;
}
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
const lerp = (a, b, t) => a + (b - a) * t;
const easeOut = (t) => 1 - Math.pow(1 - t, 3);
const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
function raf(fn) { return requestAnimationFrame(fn); }

/** 动画：duration 毫秒内回调 fn(进度 0..1)；减弱动效时直接到终点。 */
function tween(ms, fn, ease) {
  if (VC.reduced || ms <= 0) { fn(1); return { cancel() {} }; }
  const t0 = performance.now(); let dead = false;
  (function step(now) {
    if (dead) return;
    const p = clamp((now - t0) / ms, 0, 1);
    fn((ease || easeOut)(p));
    if (p < 1) raf(step);
  })(t0);
  return { cancel() { dead = true; } };
}

/** 整数的紧凑中文写法：123456 → 12.3万 */
function fmtBig(n) {
  if (n == null) return "0";
  if (n >= 1e12) return (n / 1e12).toFixed(n % 1e12 ? 1 : 0).replace(/\.0$/, "") + "万亿";
  if (n >= 1e8) return (n / 1e8).toFixed(1).replace(/\.0$/, "") + "亿";
  if (n >= 1e4) return (n / 1e4).toFixed(1).replace(/\.0$/, "") + "万";
  return String(n);
}
const fmtPct = (x, d = 1) => (x * 100).toFixed(d).replace(/\.0+$/, "") + "%";
const CN = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"];

VC.DOM = {
  na: { name: "数与代数", short: "数代", i: 0 },
  gg: { name: "图形与几何", short: "图几", i: 1 },
  sp: { name: "统计与概率", short: "统概", i: 2 },
  ip: { name: "综合与实践", short: "综实", i: 3 },
};
VC.DOMS = ["na", "gg", "sp", "ip"];
VC.VT = ["program", "rule", "human"];
VC.VTN = { program: "程序可验证", rule: "规则可验证", human: "需人工核对" };
VC.MASTERY = { know: "了解", understand: "理解", master: "掌握", apply: "运用" };
VC.EDGE = [
  { key: "prerequisite", name: "前置", cls: "pre" },
  { key: "builds_on", name: "递进", cls: "bld" },
  { key: "extends", name: "螺旋扩展", cls: "ext" },
  { key: "related", name: "相关", cls: "rel" },
  { key: "confusable", name: "易混淆", cls: "con" },
];

/** 读取 CSS 变量到 VC.C，供 canvas 使用；主题切换后重新读取。 */
VC.readColors = function () {
  const cs = getComputedStyle(document.documentElement);
  const g = (n) => cs.getPropertyValue(n).trim();
  VC.C = {
    bg: g("--surface"), ink: g("--ink"), ink2: g("--ink-2"), ink3: g("--ink-3"), line: g("--line"), line2: g("--line-2"),
    gold: g("--gold"), up: g("--up"), down: g("--down"), ok: g("--ok"), warn: g("--warn"), bad: g("--bad"),
    bandNew: g("--band-new"), bandOld: g("--band-old"), edge: g("--edge"), accent: g("--accent"),
    dom: { na: g("--na"), gg: g("--gg"), sp: g("--sp"), ip: g("--ip") },
  };
};

/** 简易事件总线 */
VC.on = (ev, fn) => { (VC.handlers[ev] = VC.handlers[ev] || []).push(fn); };
VC.emit = (ev, ...a) => { for (const f of VC.handlers[ev] || []) f(...a); };

/** 把 canvas 调成设备像素比，返回 2d 上下文与 CSS 尺寸 */
VC.fitCanvas = function (canvas) {
  const r = canvas.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = Math.max(1, Math.round(r.width)), h = Math.max(1, Math.round(r.height));
  if (canvas.width !== w * dpr || canvas.height !== h * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h, dpr };
};

VC.toast = function (msg, ms = 2200) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(VC._tt); VC._tt = setTimeout(() => (t.hidden = true), ms);
};

/** 悬浮提示 */
VC.tip = {
  show(html, x, y) {
    const t = $("#tip"); t.innerHTML = html; t.hidden = false;
    const w = t.offsetWidth, h = t.offsetHeight;
    let px = x + 16, py = y + 16;
    if (px + w > innerWidth - 8) px = x - w - 16;
    if (py + h > innerHeight - 8) py = y - h - 16;
    t.style.left = Math.max(8, px) + "px"; t.style.top = Math.max(8, py) + "px";
  },
  hide() { $("#tip").hidden = true; },
};
