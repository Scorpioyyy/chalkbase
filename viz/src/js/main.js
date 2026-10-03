/* 接线：多页路由、选择与视图、主题、键盘、引导 */
VC.pan = null;
VC.PAGES = ["overview", "graph", "boundary", "quality"];
VC.page = null;
VC.tab = { boundary: "replay", quality: "core" };
VC.DRAWER_W = 420;

/* ---------- 选择与视图 ---------- */
VC.select = function (idx, o = {}) {
  const S = VC.S;
  S.sel = idx;
  VC.pan.setSelection(idx);
  const dr = $("#drawer");
  if (idx == null) { dr.hidden = true; VC.setDrawerOpen(false); if (S.view === "focus") VC.Focus.render(true); VC.hashSync(); return; }
  const wasOpen = !dr.hidden;
  dr.hidden = false;
  VC.Detail.render(idx);
  if (!wasOpen) VC.setDrawerOpen(true);
  if (S.view === "focus") { if (o.refocus || o.zoom) VC.Focus.render(true); }
  else if (o.zoom) VC.pan.zoomToNode(idx, Math.max(VC.pan.tr.k, 2.6));
  else VC.pan.ensureVisible(idx);
  VC.hashSync();
};

VC.setDrawerOpen = function (open) {
  $("#stage").classList.toggle("has-drawer", open);
  const pan = VC.pan, w = open ? VC.DRAWER_W : 0;
  if (pan && pan.zoom) { pan.drawerW = w; pan.zoom.translateExtent([[0, 0], [pan.vw + w, pan.vh]]); }
};

VC.setView = function (v) {
  const S = VC.S; S.view = v;
  $$("#seg-view button").forEach((b) => b.classList.toggle("on", b.dataset.view === v));
  $("#focus-wrap").hidden = v !== "focus";
  $("#minimap").hidden = v === "focus"; $("#legend").hidden = v === "focus"; $("#stage-hint").hidden = v === "focus";
  $("#empty-hint").hidden = true;
  if (v === "focus") { VC.Focus.syncBar(); VC.Focus.render(false); } else { VC.Focus.closeEdge(); VC.pan.kick(); VC.refreshFilters(); }
  VC.hashSync();
};

/** 跳到图谱页并定位某个知识点 */
VC.goExplore = function (idx) {
  if (VC.S.view === "focus" && idx != null) VC.setView("pan");
  VC.go("graph");
  if (idx != null) setTimeout(() => VC.select(idx, { zoom: true }), VC.reduced ? 0 : 80);
};

/* ---------- 路由：#/页面?参数 ---------- */
VC.hashFor = function (page) {
  const S = VC.S, p = [];
  if (page === "graph") {
    if (S.view !== "pan") p.push("v=" + S.view);
    if (S.sel != null) p.push("kp=" + encodeURIComponent(VC.D.kps[S.sel].id));
    if (S.view === "focus") { p.push("d=" + S.depth); const sh = Object.keys(S.show).filter((k) => S.show[k]); if (sh.length) p.push("e=" + sh.join(",")); }
    if (S.grade.size) p.push("g=" + [...S.grade].join(","));
    if (S.domain.size) p.push("dm=" + [...S.domain].join(","));
  } else if (S.sel != null) p.push("kp=" + encodeURIComponent(VC.D.kps[S.sel].id));
  if (S.lesson != null) p.push("l=" + S.lesson);
  if (VC.tab[page] && VC.tab[page] !== { boundary: "replay", quality: "core" }[page]) p.push("t=" + VC.tab[page]);
  return "#/" + page + (p.length ? "?" + p.join("&") : "");
};

VC.hashSync = debounce(function () {
  if (!VC.page) return;
  try { history.replaceState(null, "", VC.hashFor(VC.page)); } catch (e) { /* file:// 下个别浏览器禁止 */ }
}, 200);

VC.go = function (page, tab) {
  if (tab) VC.tab[page] = tab;
  const h = VC.hashFor(page);
  if (location.hash === h) VC.show(page); else location.hash = h;
};

/** 解析 hash 并应用页面与状态 */
VC.applyHash = function () {
  const m = location.hash.match(/^#\/(\w+)(?:\?(.*))?$/);
  const page = m && VC.PAGES.includes(m[1]) ? m[1] : "overview";
  const q = {};
  if (m && m[2]) m[2].split("&").forEach((x) => { const i = x.indexOf("="); q[x.slice(0, i)] = decodeURIComponent(x.slice(i + 1)); });
  const S = VC.S;
  if (page === "graph" && m && m[2]) {
    S.grade = new Set((q.g || "").split(",").filter(Boolean).map(Number)); S.domain = new Set((q.dm || "").split(",").filter(Boolean));
    if (q.d) S.depth = clamp(+q.d || 2, 1, 7);
    Object.keys(S.show).forEach((k) => (S.show[k] = false)); (q.e || "").split(",").forEach((k) => k in S.show && (S.show[k] = true));
    VC.syncChips(); VC.refreshFilters();
  }
  if (q.l != null && q.l !== "" && +q.l !== S.lesson) VC.Timeline.setLesson(+q.l, true);
  const k = q.kp && VC.D.kpById.get(q.kp);
  if (k && k.i !== S.sel) VC.select(k.i);
  if (q.t && VC.tab[page] !== undefined) VC.tab[page] = q.t;
  VC.show(page);
  if (page === "graph") { const v = q.v === "focus" ? "focus" : "pan"; if (v !== S.view) VC.setView(v); }
};

VC.show = function (page) {
  const changed = page !== VC.page;
  if (changed) {
    $$(".page").forEach((p) => p.classList.toggle("on", p.dataset.page === page));
    $$(".nav-links a").forEach((a) => a.classList.toggle("on", a.dataset.page === page));
    $("#nav").classList.toggle("dark", page === "overview");
    VC.page = page;
    VC.onPageShow(page);
  }
  if (VC.tab[page] !== undefined) VC.showTab(page, VC.tab[page]);
  VC.hashSync();
};

VC.onPageShow = function (page) {
  VC.tip.hide();
  if (page === "overview") VC.heroCount && VC.heroCount();
  if (page === "graph") { VC.pan.relayout(); if (VC.S.view === "focus") VC.Focus.render(false); }
  if (page === "boundary") { VC.Timeline.mini.relayout(); VC.Timeline.drawAxis(); VC.Timeline.render(false); }
};

VC.showTab = function (page, tab) {
  const root = $("#p-" + page);
  VC.tab[page] = tab;
  $$(".tabs button", root).forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  const pane = $("#tp-" + tab);
  $$(".tabpane", root).forEach((p) => p.classList.toggle("on", p === pane));
  if (pane && !pane.classList.contains("in")) { pane.classList.add("in"); (pane._cbs || []).forEach((f) => f()); pane._cbs = []; }
  if (page === "boundary") { if (tab === "replay") VC.Timeline.mini.relayout(); else { VC.Timeline.drawAxis(); if (!VC.Timeline.oosShown) { VC.Timeline.oosShown = true; VC.Timeline.pickOos(VC.Timeline.oosSel, true); } } }
};

/* ---------- 主题与图例 ---------- */
VC.THEME_ICON = {
  light: '<path d="M16.5 11.5A7 7 0 018.5 3.5a7 7 0 108 8z" fill="currentColor"/>',
  dark: '<circle cx="10" cy="10" r="3.6" fill="currentColor"/><g stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M10 2.2v2M10 15.8v2M2.2 10h2M15.8 10h2M4.5 4.5l1.4 1.4M14.1 14.1l1.4 1.4M4.5 15.5l1.4-1.4M14.1 5.9l1.4-1.4"/></g>',
};
/* 图标随主题变化：浅色时显示月亮（点击进入深色），深色时显示太阳（点击回到浅色） */
VC.syncThemeIcon = function (animate) {
  const btn = $("#btn-theme"); if (!btn) return;
  const t = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  const svg = btn.querySelector("svg");
  svg.innerHTML = VC.THEME_ICON[t];
  btn.title = t === "dark" ? "切换到浅色" : "切换到深色";
  if (animate && svg.animate && !matchMedia("(prefers-reduced-motion: reduce)").matches)
    svg.animate([{ transform: "rotate(-80deg) scale(.55)", opacity: 0 }, { transform: "none", opacity: 1 }], { duration: 380, easing: "cubic-bezier(.2,.8,.2,1)" });
};

VC.setTheme = function (t, save) {
  document.documentElement.dataset.theme = t;
  VC.syncThemeIcon(true);
  if (save) try { localStorage.setItem("chalkbase-theme", t); } catch (e) { /* 忽略 */ }
  VC.readColors();
  VC.pan && VC.pan.kick(); VC.Timeline.mini && VC.Timeline.mini.kick(); VC.Timeline.drawAxis && VC.Timeline.drawAxis();
};

VC.buildLegend = function () {
  const D = VC.D;
  $("#legend").innerHTML = VC.DOMS.map((d) => `<span><i style="background:var(--${d})"></i>${VC.DOM[d].name}</span>`).join("") +
    `<span><i class="gap"></i>版本缺口补全 ${D.kps.filter((k) => k.pv).length}</span><span><i class="old"></i>旧版教材</span>`;
};

/* ---------- 引导 ---------- */
VC.Tour = {
  steps: [
    { page: "overview", sel: "#p-overview .hero-copy", t: "这是什么", d: "ChalkBase 把 12 册北师大版小学数学教材整理成可被程序直接调用的课程知识库：知识图谱、题型卡片、逐课时的能力边界。" },
    { page: "graph", sel: "#stage", t: "知识图谱", d: "横轴是 12 个学期，纵向是 4 个领域。悬停看前置流向，点击看详情，双击展开前置链；右上角“筛选”按年级、领域等收窄。" },
    { page: "graph", sel: "#search", t: "用教师的话检索", d: "试试“三年级两位数乘一位数的竖式”。按 / 随时聚焦搜索框，命中的知识点会在图上标出涟漪。" },
    { page: "boundary", sel: "#scrubber", t: "能力边界回放", d: "拖动或播放这条时间轴：已学的知识点亮起，三个核心指标随课时增长。试试“超纲检测演示”标签。" },
    { page: "quality", sel: "#tabs-quality", t: "质量证据", d: "先看“核心结论”的 6 个指标，再用标签切换到全部指标、分布、版本修复、课标覆盖和标注数据质量。" },
  ],
  i: 0,
  start() { this.i = 0; $("#tour").hidden = false; this.show(); },
  end() { $("#tour").hidden = true; try { localStorage.setItem("chalkbase-tour", "1"); } catch (e) { /* 忽略 */ } },
  show() {
    const s = this.steps[this.i], t = $("#tour");
    VC.go(s.page);
    const place = () => {
      const target = $(s.sel), r = target.getBoundingClientRect(), pad = 8;
      const m = t.querySelector(".mask") || t.appendChild(el("div", { class: "mask" }));
      Object.assign(m.style, { left: r.left - pad + "px", top: r.top - pad + "px", width: r.width + pad * 2 + "px", height: r.height + pad * 2 + "px" });
      let pop = t.querySelector(".pop");
      if (!pop) pop = t.appendChild(el("div", { class: "pop", role: "dialog" }));
      pop.innerHTML = `<h4>${s.t}</h4><p>${s.d}</p><div class="row"><span>${this.i + 1} / ${this.steps.length}</span><div><button class="btn tiny" data-a="skip">跳过</button>${this.i ? '<button class="btn tiny" data-a="prev">上一步</button>' : ""}<button class="btn tiny primary" data-a="next">${this.i === this.steps.length - 1 ? "开始探索" : "下一步"}</button></div></div>`;
      const pw = 340, ph = pop.offsetHeight;
      let px = r.right + 18, py = r.top + 10;
      if (px + pw > innerWidth - 12) px = r.left - pw - 18;
      if (px < 12) { px = clamp(r.left + 20, 12, innerWidth - pw - 12); py = r.bottom + 16; if (py + ph > innerHeight - 12) py = Math.max(12, r.top + 20); }
      Object.assign(pop.style, { left: px + "px", top: clamp(py, 12, innerHeight - ph - 12) + "px" });
      pop.querySelector('[data-a="skip"]').onclick = () => this.end();
      pop.querySelector('[data-a="next"]').onclick = () => (this.i === this.steps.length - 1 ? this.end() : (this.i++, this.show()));
      const pv = pop.querySelector('[data-a="prev"]'); if (pv) pv.onclick = () => (this.i--, this.show());
    };
    requestAnimationFrame(() => requestAnimationFrame(place));
  },
};

/* ---------- 启动 ---------- */
(async function boot() {
  if (!window.DecompressionStream) { document.body.innerHTML = "<p style='padding:40px'>此浏览器不支持 DecompressionStream，请使用新版 Chrome / Edge / Safari / Firefox 打开。</p>"; return; }
  let theme = null; try { theme = localStorage.getItem("chalkbase-theme"); } catch (e) { /* 忽略 */ }
  document.documentElement.dataset.theme = theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  VC.syncThemeIcon(false);
  // 先让所有页面都有尺寸再初始化画布，之后恢复到路由指定的页面
  VC.readColors();
  await VC.load();
  VC.initHero();
  VC.buildLegend(); VC.buildChips();
  $("#p-graph").classList.add("on");
  VC.pan = new VC.Panorama($("#pan"), { onSelect: (i) => VC.select(i), onFocus: () => VC.setView("focus") });
  VC.pan.ensureVisible = function (i) {
    if (!this.drawerW) return; const n = this.nodes[i], t = this.tr, sx = t.x + t.k * n.x;
    if (sx > this.vw - this.drawerW - 50) { const nt = d3.zoomIdentity.translate(t.x - (sx - (this.vw - this.drawerW) / 2), t.y).scale(t.k); d3.select(this.cv).transition().duration(500).call(this.zoom.transform, nt); }
  };
  VC.initMinimapEvents(VC.pan);
  VC.Focus.init();
  VC.Search.init();
  $("#p-graph").classList.remove("on"); $("#p-boundary").classList.add("on");
  VC.Timeline.init();
  $("#p-boundary").classList.remove("on");
  VC.Quality.init();

  // 视图与工具
  $$("#seg-view button").forEach((b) => b.addEventListener("click", () => VC.setView(b.dataset.view)));
  // 聚焦视图里只收起详情，保留当前聚焦的图；全景视图里关闭详情即取消选中
  $("#drawer-close").addEventListener("click", () => {
    if (VC.S.view !== "focus") return VC.select(null);
    $("#drawer").hidden = true; VC.setDrawerOpen(false); VC.Focus.render(true);
  });
  $("#btn-reset").addEventListener("click", () => {
    const S = VC.S; S.grade.clear(); S.domain.clear(); S.vt.clear(); S.gap = false; S.std = null;
    $$(".chips .chip").forEach((c) => c.classList.remove("on")); VC.refreshFilters(); VC.select(null); VC.pan.resetZoom(); VC.setView("pan");
  });
  $("#sel-size").addEventListener("change", (e) => { VC.S.size = e.target.value; VC.pan.layout(); VC.pan.kick(); });
  $("#btn-filter").addEventListener("click", (e) => { e.stopPropagation(); const p = $("#filter-pop"); p.hidden = !p.hidden; $("#btn-filter").setAttribute("aria-expanded", !p.hidden); });
  document.addEventListener("click", (e) => { const fp = $("#ft-pop"); if (fp && !fp.hidden && !fp.contains(e.target)) fp.hidden = true; const p = $("#filter-pop"); if (!p.hidden && !p.contains(e.target) && e.target !== $("#btn-filter")) p.hidden = true; });
  $("#btn-theme").addEventListener("click", () => VC.setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark", true));
  $("#btn-help").addEventListener("click", () => VC.Tour.start());
  $$(".nav-links a, .brand").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); VC.go(a.dataset.page || "overview"); }));
  $$("[data-go]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); VC.go(a.dataset.go); }));
  $$(".tabs").forEach((box) => box.addEventListener("click", (e) => { const b = e.target.closest("button[data-tab]"); if (b) { VC.showTab(box.closest(".page").dataset.page, b.dataset.tab); VC.hashSync(); } }));

  // 尺寸变化
  const rel = debounce(() => { if (VC.page === "graph") VC.pan.relayout(); if (VC.page === "boundary") { VC.Timeline.mini.relayout(); VC.Timeline.drawAxis(); } }, 150);
  new ResizeObserver(rel).observe($("#stage")); new ResizeObserver(rel).observe($("#tl-pan-card"));
  addEventListener("resize", rel);

  // 键盘
  addEventListener("keydown", (e) => {
    const tag = (e.target.tagName || "").toLowerCase(), typing = tag === "input" && e.target.type !== "range" || tag === "select" || tag === "textarea";
    if (e.key === "/" && !typing) { e.preventDefault(); $("#q").focus(); $("#q").select(); return; }
    if (typing) return;
    if (e.key === "Escape") {
      if (!$("#tour").hidden) return VC.Tour.end();
      if (!$("#filter-pop").hidden) { $("#filter-pop").hidden = true; return; }
      if (!$("#more-dims").hidden && VC.page === "boundary") { $("#md-close").click(); return; }
      if (!$("#edge-card").hidden) return VC.Focus.closeEdge();
      if (VC.S.hits.size) { VC.S.hits = new Map(); $("#q").value = ""; VC.pan.kick(); return; }
      if (VC.S.view === "focus" && VC.page === "graph") return VC.setView("pan");
      if (VC.S.sel != null && VC.page === "graph") return VC.select(null);
      if (VC.S.lesson != null) return VC.Timeline.off();
    }
    if ((e.key === "ArrowLeft" || e.key === "ArrowRight") && VC.S.lesson != null && tag !== "range") { e.preventDefault(); VC.Timeline.setLesson(VC.S.lesson + (e.key === "ArrowRight" ? 1 : -1), true, true); }
    if (e.key === " " && tag !== "button" && tag !== "range" && VC.page === "boundary") { e.preventDefault(); VC.Timeline.toggle(); }
  });
  addEventListener("hashchange", () => VC.applyHash());

  VC.applyHash();
  VC.initLive().catch((e) => console.warn("live card", e));
  VC.getArch();
  let seen = false; try { seen = localStorage.getItem("chalkbase-tour"); } catch (e) { /* 忽略 */ }
  if (!seen && !location.hash.includes("?") && !navigator.webdriver) setTimeout(() => VC.Tour.start(), 1800);
  document.documentElement.dataset.ready = "1";
})().catch((e) => { console.error(e); document.documentElement.dataset.error = String(e); });
