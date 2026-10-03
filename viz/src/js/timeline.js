/* 能力边界时间轴：回放、各维度指标、同步全景图、超纲检测演示 */
VC.Timeline = {
  pos: null, speed: 3, acc: 0, last: 0, raf: 0, auto: false,
  DIMS: { integer_domain: "整数数域", decimal_places: "小数位数", fraction_types: "分数类型", operation_forms: "运算形态", concepts: "概念", units_of_measure: "计量单位", geometry_vocab: "几何词汇" },
  FRAC: ["几分之一", "几分之几", "真分数", "假分数", "带分数"],

  init() {
    const D = VC.D, T = D.tl, self = this;
    this.N = D.lessons.length; this.pos = this.N - 1;
    // 首次出现表
    const first = { f: new Map(), u: new Map(), g: new Map(), o: {} };
    for (let i = 0; i < this.N; i++) {
      const a = T.add[i]; if (!a) continue;
      (a.f || []).forEach((x) => first.f.has(x) || first.f.set(x, i));
      (a.u || []).forEach((x) => first.u.has(x) || first.u.set(x, i));
      (a.g || []).forEach((x) => first.g.has(x) || first.g.set(x, i));
      Object.entries(a.o || {}).forEach(([op, fs]) => fs.forEach((f) => { (first.o[op] = first.o[op] || new Map()); first.o[op].has(f) || first.o[op].set(f, i); }));
    }
    this.first = first;
    // 刻度
    const box = $("#tl-ticks");
    for (let b = 0; b < 12; b++) {
      const x = (D.bookLessons[b][0] / (this.N - 1)) * 100;
      box.append(el("span", { class: D.books[b].std === 2011 ? "old" : "", style: { left: x + "%", transform: b ? "translateX(-50%)" : "none" }, text: VC.semShort(b) }));
    }
    // 控件
    $("#tl-range").addEventListener("input", (e) => this.setLesson(+e.target.value, true, true));
    $("#tl-play").addEventListener("click", () => this.toggle());
    $("#tl-speed").addEventListener("change", (e) => (this.speed = +e.target.value));
    $("#btn-more-dims").addEventListener("click", () => { const m = $("#more-dims"); m.hidden = !m.hidden; $("#btn-more-dims").textContent = m.hidden ? "更多维度 ▴" : "收起更多维度 ▾"; if (!m.hidden) this.render(false); });
    $("#md-close").addEventListener("click", () => { $("#more-dims").hidden = true; $("#btn-more-dims").textContent = "更多维度 ▴"; });
    $("#tl-legend").innerHTML = VC.DOMS.map((d) => `<span><i style="background:var(--${d})"></i>${VC.DOM[d].name}</span>`).join("") + '<span class="muted">亮 = 已学 · 淡 = 未学</span>';
    // 缩略全景
    this.mini = new VC.Panorama($("#tl-pan"), { compact: true, onSelect: (i) => i != null && VC.goExplore(i) });
    // 指标卡
    this.buildMeters();
    this.buildOos();
    this.render(false);
    // 进入视野时自动播放一次
  },

  cur() { return VC.S.lesson == null ? this.N - 1 : VC.S.lesson; },

  setLesson(li, active = true, user = false) {
    const S = VC.S, prev = S.lesson == null ? null : S.lesson;
    li = clamp(Math.round(li), 0, this.N - 1);
    if (user && S.playing) this.toggle(false);
    S.lesson = active ? li : null; this.pos = li;
    const from = prev == null ? -1 : prev;
    this.render(true, from);
    if (VC.pan && li > from && li - from <= 12) { VC.pan.addFlash(from, li); this.mini.addFlash(from, li); }
    VC.pan && (VC.pan.edgeCache = null, VC.pan.kick()); this.mini.edgeCache = null; this.mini.kick();
    VC.hashSync();
  },
  off() {
    if (VC.S.playing) this.toggle(false);
    VC.S.lesson = null; this.render(true); VC.pan.edgeCache = null; VC.pan.kick(); this.mini.kick(); VC.hashSync();
  },
  toggle(force) {
    const S = VC.S, on = force == null ? !S.playing : force;
    S.playing = on;
    $("#tl-play").classList.toggle("on", on);
    if (on) {
      if (S.lesson == null || S.lesson >= this.N - 1) this.setLesson(0, true);
      this.last = performance.now(); this.acc = 0;
      const tick = (now) => {
        if (!S.playing) return;
        this.acc += ((now - this.last) / 1000) * this.speed * 6; this.last = now;
        if (this.acc >= 1) { const st = Math.floor(this.acc); this.acc -= st; const nx = Math.min(this.N - 1, S.lesson + st); this.setLesson(nx, true); if (nx >= this.N - 1) { this.toggle(false); return; } }
        this.raf = requestAnimationFrame(tick);
      };
      this.raf = requestAnimationFrame(tick);
      VC.pan.kick();
    } else cancelAnimationFrame(this.raf);
  },

  /* ---------- 渲染 ---------- */
  render(animate, prevLesson) {
    const D = VC.D, S = VC.S, T = D.tl, li = this.cur(), L = VC.loc(li), active = S.lesson != null;
    $("#tl-range").value = li;
    const fr = this.FRAC.filter((f) => this.first.f.has(f) && this.first.f.get(f) <= li);
    const im = T.im[li], dp = T.dp[li];
    $("#tl-summary").innerHTML = `学到 <b>${esc(L.sem_name)} · ${esc(L.unit)}</b>「${esc(L.title)}」：整数到 <b>${fmtBig(im)}</b>，` +
      (dp ? `小数 <b>${dp} 位</b>` : "还没学小数") + "，" + (fr.length ? `分数已学 <b>${fr.join("、")}</b>` : "还没学分数") + `。已学 ${D.learnedCum[li]} / ${D.kps.length} 个知识点。`;
    $("#tl-learned").textContent = `已学 ${D.learnedCum[li]} / ${D.kps.length}`;
    const chip = $("#replay-chip");
    chip.hidden = !active;
    if (active) chip.replaceChildren(`回放至 ${L.sem_name} · ${L.title}（已学 ${D.learnedCum[li]}/${D.kps.length}）`, el("button", { text: "退出", onclick: () => this.off() }));
    this.updateMeters(li, prevLesson);
    this.renderOos();
  },

  chip(text, on, key) { return el("span", { class: "lit" + (on ? " on" : ""), "data-k": key, text }); },

  buildMeters() {
    const D = VC.D, T = D.tl, F = this.first;
    const card = (id, title, sub) => { const c = $(id); c.append(el("div", { class: "card-h" }, [el("h3", { text: title }), el("span", { class: "sub", text: sub || "" })])); return c; };
    // 整数
    let c = card("#m-int", "整数数域上限", "能认、能算的最大整数");
    c.append(el("div", { class: "big-num", id: "v-int" }), el("div", { class: "newlist", id: "n-int" }), el("div", { id: "sp-int" }));
    this.spark("#sp-int", T.im.map((v) => Math.log10(Math.max(v, 1)) / 12), "int");
    // 小数
    c = card("#m-dec", "小数位数", "最多几位小数");
    c.append(el("div", { class: "big-num", id: "v-dec" }), el("div", { class: "dots", id: "d-dec" }, [1, 2, 3, 4].map((n) => el("i", { text: n }))), el("div", { class: "newlist", id: "n-dec" }));
    // 分数
    c = card("#m-frac", "分数类型", "按学习顺序点亮");
    c.append(el("div", { class: "sq", id: "s-frac" }, this.FRAC.map((f) => this.chip(f, false, f))));
    // 运算
    c = card("#m-ops", "运算形态", "加减乘除的操作数形态");
    for (const op of ["加法", "减法", "乘法", "除法"]) {
      const forms = [...(F.o[op] || new Map()).entries()].sort((a, b) => a[1] - b[1]);
      c.append(el("div", { class: "oprow" }, [el("b", { text: { 加法: "＋", 减法: "－", 乘法: "×", 除法: "÷" }[op] }), el("div", { class: "sq", "data-op": op }, forms.map(([f]) => this.chip(f, false, op + "|" + f)))]));
    }
    // 单位
    c = card("#m-unit", "计量单位", "已认识的单位");
    c.append(el("div", { class: "sq", id: "s-unit" }, [...F.u.entries()].sort((a, b) => a[1] - b[1]).map(([u]) => this.chip(u, false, u))));
    // 几何
    c = card("#m-geo", "几何词汇", `${F.g.size} 个术语`); c.classList.add("geo");
    c.append(el("div", { class: "sq", id: "s-geo" }, [...F.g.entries()].sort((a, b) => a[1] - b[1]).map(([g]) => this.chip(g, false, g))));
    // 概念
    c = card("#m-concept", "概念与表述", "教材里出现的知识表述");
    c.append(el("div", { class: "big-num", id: "v-cc" }), el("div", { id: "sp-cc" }), el("div", { class: "newlist", id: "n-cc" }));
    const tot = D.cc[this.N - 1]; this.spark("#sp-cc", D.cc.map((v) => v / tot), "cc");
    this.lit = {};
  },

  spark(sel, ys, name) {
    const W = 300, H = 56, n = ys.length, pts = ys.map((y, i) => `${((i / (n - 1)) * W).toFixed(1)},${(H - 4 - y * (H - 10)).toFixed(1)}`);
    const area = `M0,${H} L${pts.join(" L")} L${W},${H} Z`;
    $(sel).innerHTML = `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><path d="${area}" fill="var(--accent)" opacity=".14"/><polyline points="${pts.join(" ")}" fill="none" stroke="var(--accent)" stroke-width="1.6" vector-effect="non-scaling-stroke"/>` +
      `<line id="mk-${name}" y1="0" y2="${H}" stroke="var(--gold)" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
  },

  updateMeters(li, prevLesson) {
    const D = VC.D, T = D.tl, F = this.first, forward = prevLesson != null && li > prevLesson && li - prevLesson < 40;
    const set = (id, txt) => { const e = $(id); if (e && e.textContent !== txt) e.textContent = txt; };
    set("#v-int", fmtBig(T.im[li]) || "0");
    $("#v-int").innerHTML = `${fmtBig(T.im[li])}<small>${T.im[li] >= 1e4 ? "" : "以内"}</small>`;
    $("#v-dec").innerHTML = `${T.dp[li]}<small>位</small>`;
    $$("#d-dec i").forEach((d, i) => d.classList.toggle("on", T.dp[li] >= i + 1));
    const dpNew = [[T.dp[li], T.dp[Math.max(0, li - 1)]]].filter(([a, b]) => a > b);
    const nextJump = (arr, key) => { for (let j = li + 1; j < this.N; j++) if (arr[j] > arr[li]) return j; return null; };
    const nd = nextJump(T.dp), ni = nextJump(T.im);
    $("#n-dec").innerHTML = nd != null ? `下一步：<b>${T.dp[nd]} 位小数</b> · ${esc(VC.loc(nd).sem_name)}` : '<span class="muted">已到上限</span>';
    $("#n-int").innerHTML = ni != null ? `下一步：<b>${fmtBig(T.im[ni])}</b> · ${esc(VC.loc(ni).sem_name)}` : '<span class="muted">已到上限</span>';
    for (const [id, lm] of [["int", T.im], ["cc", D.cc]]) { const mk = $("#mk-" + id); if (mk) { const x = (li / (this.N - 1)) * 300; mk.setAttribute("x1", x); mk.setAttribute("x2", x); } }
    const upd = (container, map, keyFn) => $$(".lit", container).forEach((c) => {
      const f = map(c.dataset.k), on = f != null && f <= li, was = c.classList.contains("on");
      if (on !== was) { c.classList.toggle("on", on); if (on && forward && f > prevLesson) { c.classList.remove("new"); void c.offsetWidth; c.classList.add("new"); } }
    });
    upd($("#s-frac"), (k) => F.f.get(k));
    $$("#m-ops .sq").forEach((box) => upd(box, (k) => F.o[box.dataset.op].get(k.split("|")[1])));
    upd($("#s-unit"), (k) => F.u.get(k)); upd($("#s-geo"), (k) => F.g.get(k));
    $("#v-cc").innerHTML = `${D.cc[li].toLocaleString("en-US")}<small>/ ${D.cc[this.N - 1].toLocaleString("en-US")}</small>`;
    const add = T.add[li];
    $("#n-cc").innerHTML = add && add.c ? `<b>本课新增 ${add.nc} 条：</b>${add.c.slice(0, 3).map(esc).join("；")}${add.nc > 3 ? "…" : ""}` : "";
  },

  /* ---------- 超纲检测 ---------- */
  buildOos() {
    const D = VC.D, list = $("#oos-list");
    this.oosSel = 0;
    D.oos.forEach((q, i) => list.append(el("button", { class: "oos-it" + (i === 0 ? " on" : ""), role: "option", "data-i": i, onclick: () => this.pickOos(i) },
      [el("span", { class: "vd" }), el("div", {}, [el("div", { class: "tg2", text: q.tag }), el("div", { class: "tx", text: q.t })])])));
    const cv = $("#oos-axis");
    const at = (e) => { const r = cv.getBoundingClientRect(); return clamp(Math.round(((e.clientX - r.left) / r.width) * (this.N - 1)), 0, this.N - 1); };
    cv.addEventListener("pointerdown", (e) => { cv.setPointerCapture(e.pointerId); this.setLesson(at(e), true, true); cv.onpointermove = (ev) => this.setLesson(at(ev), true, true); });
    cv.addEventListener("pointerup", () => (cv.onpointermove = null));
    new ResizeObserver(() => this.drawAxis()).observe(cv);
  },
  segAt(q, li) { let s = q.segs[0]; for (const x of q.segs) { if (x[0] <= li) s = x; else break; } return s; },
  firstIn(q) { const s = q.segs.find((x) => x[1] === 0); return s ? s[0] : null; },
  pickOos(i, jump = true) {
    this.oosSel = i;
    $$(".oos-it").forEach((b, j) => b.classList.toggle("on", j === i));
    const q = VC.D.oos[i], fi = this.firstIn(q);
    if (jump && fi != null && fi > 0) {
      // 跳到刚好越界的最后一课，让原因一目了然
      const target = Math.max(0, fi - 1);
      const from = this.cur();
      tween(700, (p) => this.setLesson(from + (target - from) * p, true));
    } else this.renderOos();
  },
  renderOos() {
    const D = VC.D, li = this.cur(), q = D.oos[this.oosSel];
    $$(".oos-it .vd").forEach((d, i) => (d.className = "vd v" + this.segAt(D.oos[i], li)[1]));
    const seg = this.segAt(q, li), v = seg[1], fi = this.firstIn(q), L = VC.loc(li);
    $("#oos-q").textContent = q.t;
    const names = ["在边界内", "边界附近 · 建议复核", "超纲"], ic = ["✓", "!", "✗"];
    const vd = $("#oos-verdict"); vd.className = "oos-verdict v" + v;
    vd.innerHTML = `<span class="ic">${ic[v]}</span><span>${names[v]}</span><span class="lb">${esc(L.sem_name)} · ${esc(L.title)}<br>${fi == null ? "整个教材序列内都超出能力边界" : v === 0 ? `自「${esc(VC.loc(fi).short)}」起在边界内` : `「${esc(VC.loc(fi).short)}」之前超纲`}</span>`;
    const ul = $("#oos-reasons"); ul.replaceChildren();
    if (!seg[2].length) ul.append(el("li", { html: "<b style='color:var(--ok)'>✓</b> 此题用到的整数范围、小数位数、分数与运算形态、单位和几何词汇，都已在该课时之前学过。" }));
    seg[2].forEach(([dim, val, intro, detail]) => ul.append(el("li", { class: v === 1 ? "soft" : "", html: `<b>${this.DIMS[dim] || dim}</b>　${esc(detail)}<small>${intro != null ? `该能力最早在「${esc(VC.loc(intro).short)}」引入` : "教材中未引入这一取值"}</small>` })));
    this.drawAxis();
  },
  drawAxis() {
    const cv = $("#oos-axis"); if (!cv || !VC.D) return;
    const { ctx, w, h } = VC.fitCanvas(cv), C = VC.C, q = VC.D.oos[this.oosSel], N = this.N;
    ctx.clearRect(0, 0, w, h);
    const cols = [C.ok, C.warn, C.bad];
    q.segs.forEach((s, i) => { const x0 = (s[0] / N) * w, x1 = ((q.segs[i + 1] ? q.segs[i + 1][0] : N) / N) * w; ctx.fillStyle = cols[s[1]]; ctx.globalAlpha = 0.85; ctx.fillRect(x0, 8, x1 - x0 + 0.5, h - 24); });
    ctx.globalAlpha = 1;
    ctx.font = "11px " + getComputedStyle(document.body).fontFamily; ctx.textBaseline = "top"; ctx.textAlign = "center";
    for (let b = 0; b < 12; b++) { const x = (VC.D.bookLessons[b][0] / N) * w; ctx.fillStyle = C.ink3; ctx.fillRect(x, h - 16, 1, 5); if (b % 1 === 0) ctx.fillText(VC.semShort(b), Math.min(w - 8, Math.max(8, x + (VC.D.bookLessons[b][1] - VC.D.bookLessons[b][0]) / N * w / 2)), h - 14); }
    const x = ((this.cur() + 0.5) / N) * w;
    ctx.strokeStyle = C.ink; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.moveTo(x, 2); ctx.lineTo(x, h - 16); ctx.stroke();
    ctx.fillStyle = C.ink; ctx.beginPath(); ctx.arc(x, 4, 4, 0, 6.3); ctx.fill();
  },
};
