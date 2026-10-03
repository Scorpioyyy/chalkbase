/* 检索：浏览器内的小型 BM25（轻量近似），支持年级/学期/领域解析与高亮；筛选芯片 */
VC.Search = {
  K1: 1.4, B: 0.72,
  STRIP: /(帮我|请帮我|我想要?|想要|给我|出[一两几]?道|出几题|出题|出一份|关于|有关|相关的?|的题目|练习题|习题|知识点|需要|设计|编一?道?|一些|一点)/g,
  CNUM: { 一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6 },
  DOMKEY: [["gg", /几何|图形|角|面积|体积|周长/], ["sp", /统计|概率|图表|统计图|平均数/], ["ip", /综合与实践|实践活动|项目学习/], ["na", /计算|运算|应用题|数的/]],

  toks(str) {
    const out = [], s = String(str).toLowerCase();
    const re = /[一-鿿]+|[a-z0-9.%/]+/g; let m;
    while ((m = re.exec(s))) {
      const w = m[0];
      if (/^[a-z0-9]/.test(w)) { out.push(w); continue; }
      for (let i = 0; i < w.length; i++) { out.push(w[i]); if (i + 1 < w.length) out.push(w[i] + w[i + 1]); }
    }
    return out;
  },

  init() {
    const D = VC.D, F = { n: 3.2, al: 2.6, th: 1.4, de: 0.9, tp: 0.8 };
    this.docs = D.kps.map((k) => {
      const tf = new Map(); let len = 0;
      const add = (txt, w) => this.toks(txt).forEach((t) => { tf.set(t, (tf.get(t) || 0) + w); len += w; });
      add(k.n, F.n); add(k.al.join(" "), F.al); add(k.th, F.th); add(k.de, F.de); add(k.tp, F.tp);
      return { tf, len };
    });
    this.avg = this.docs.reduce((a, d) => a + d.len, 0) / this.docs.length;
    const df = new Map();
    this.docs.forEach((d) => d.tf.forEach((_, t) => df.set(t, (df.get(t) || 0) + 1)));
    this.idf = (t) => Math.log(1 + (this.docs.length - (df.get(t) || 0) + 0.5) / ((df.get(t) || 0) + 0.5));
    this.bind();
  },

  parse(q) {
    const r = { grade: null, sem: null, dom: null, text: q, tags: [] };
    let m = q.match(/([一二三四五六1-6])\s*年级/);
    if (m) { r.grade = this.CNUM[m[1]] || +m[1]; r.text = r.text.replace(m[0], " "); r.tags.push(m[0].replace(/\s/g, "")); }
    m = q.match(/(上|下)\s*(册|学期)/);
    if (m) { r.sem = m[1] === "上" ? "a" : "b"; r.text = r.text.replace(m[0], " "); r.tags.push(m[1] + "册"); }
    for (const [d, re] of this.DOMKEY) if (re.test(q) && d !== "na") { r.dom = d; r.tags.push(VC.DOM[d].name); break; }
    r.text = r.text.replace(this.STRIP, " ").replace(/[的了吗呢吧，。,.?？]/g, " ").trim();
    return r;
  },

  run(q, limit = 8) {
    const D = VC.D, P = this.parse(q), tk = [...new Set(this.toks(P.text))];
    const res = [];
    D.kps.forEach((k, i) => {
      let s = 0;
      const d = this.docs[i];
      for (const t of tk) { const f = d.tf.get(t); if (f) s += this.idf(t) * ((f * (this.K1 + 1)) / (f + this.K1 * (1 - this.B + (this.B * d.len) / this.avg))) * (t.length === 2 ? 1.25 : 1); }
      if (P.text && k.n.includes(P.text.replace(/\s/g, ""))) s *= 1.5;
      if (!tk.length && (P.grade || P.sem)) s = 1;
      if (s <= 0) return;
      if (P.grade) s *= k.grade === P.grade ? 1.9 : 0.5;
      if (P.sem) s *= k.sem === P.sem ? 1.35 : 0.7;
      if (P.dom) s *= k.d === P.dom ? 1.4 : 0.8;
      if (!tk.length) s -= k.l * 1e-4;
      res.push({ i, s });
    });
    res.sort((a, b) => b.s - a.s);
    return { P, tk, hits: res.slice(0, limit), total: res.length };
  },

  hl(text, tk) {
    const chars = new Set(); tk.filter((t) => t.length >= 2 && /[一-鿿]/.test(t)).forEach((t) => { for (const c of t) chars.add(c); });
    const bi = new Set(tk.filter((t) => t.length === 2));
    const mark = new Array(text.length).fill(false);
    for (let i = 0; i < text.length - 1; i++) if (bi.has(text.slice(i, i + 2))) mark[i] = mark[i + 1] = true;
    let o = "", on = false;
    for (let i = 0; i < text.length; i++) { if (mark[i] && !on) { o += "<mark>"; on = true; } if (!mark[i] && on) { o += "</mark>"; on = false; } o += esc(text[i]); }
    return o + (on ? "</mark>" : "");
  },

  bind() {
    const inp = $("#q"), pop = $("#search-pop"), self = this;
    let sel = 0, cur = null;
    const render = () => {
      const q = inp.value.trim();
      if (!q) { pop.hidden = true; VC.S.hits = new Map(); VC.pan && VC.pan.kick(); return; }
      cur = this.run(q);
      VC.S.hits = new Map(cur.hits.map((h) => [h.i, h.s])); VC.pan && VC.pan.kick();
      pop.hidden = false; pop.replaceChildren();
      const meta = el("div", { class: "sp-meta" }, [`解析：`, ...cur.P.tags.map((t) => el("span", { class: "pill", style: { background: "var(--surface-3)", color: "var(--ink-2)", border: "0" }, text: t })), cur.tk.length ? `关键词 ${cur.P.text.replace(/\s+/g, " ")}` : "", ` · 共 ${cur.total} 个相关知识点`]);
      pop.append(meta);
      if (!cur.hits.length) pop.append(el("div", { class: "sp-empty", text: "没有匹配的知识点，换个说法试试（如“两位数乘一位数”）" }));
      const top = cur.hits[0] ? cur.hits[0].s : 1;
      cur.hits.forEach((h, j) => {
        const k = VC.D.kps[h.i], L = VC.kpLoc(k);
        const it = el("div", { class: "sp-item" + (j === sel ? " on" : ""), "data-j": j }, [
          el("span", { class: "dot", style: { background: `var(--${k.d})` } }),
          el("div", {}, [el("div", { class: "nm", html: this.hl(k.n, cur.tk) }), el("div", { class: "sub", text: `${L.sem_name} · ${L.unit} · ${k.na} 个题型` })]),
          el("div", { class: "sc" }, [el("i", { style: { width: Math.max(8, (h.s / top) * 100) + "%" } })])]);
        it.addEventListener("mousedown", (e) => { e.preventDefault(); this.pick(h.i); });
        it.addEventListener("mouseenter", () => { sel = j; $$(".sp-item", pop).forEach((x, y) => x.classList.toggle("on", y === j)); });
        pop.append(it);
      });
    };
    inp.addEventListener("input", () => { sel = 0; render(); });
    inp.addEventListener("focus", () => inp.value && render());
    inp.addEventListener("blur", () => setTimeout(() => (pop.hidden = true), 120));
    inp.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { if (!cur) return; e.preventDefault(); sel = (sel + (e.key === "ArrowDown" ? 1 : -1) + cur.hits.length) % Math.max(1, cur.hits.length); $$(".sp-item", pop).forEach((x, y) => x.classList.toggle("on", y === sel)); }
      else if (e.key === "Enter" && cur && cur.hits[sel]) { e.preventDefault(); this.pick(cur.hits[sel].i); }
      else if (e.key === "Escape") { inp.value = ""; inp.blur(); VC.S.hits = new Map(); VC.pan.kick(); pop.hidden = true; e.stopPropagation(); }
    });
    this.fire = (q) => { inp.value = q; sel = 0; inp.focus(); render(); VC.go("graph"); };
  },
  pick(i) { $("#q").blur(); $("#search-pop").hidden = true; VC.goExplore(i); },
};

/* ---------- 筛选芯片 ---------- */
VC.buildChips = function () {
  const S = VC.S, mk = (box, items, set, cls) => {
    items.forEach(([val, label, extra]) => {
      const b = el("button", { class: "chip", "data-v": val, ...(extra || {}), onclick: () => { const on = set(val); b.classList.toggle("on", on); VC.refreshFilters(); } }, [label]);
      box.append(b);
    });
  };
  const tog = (s) => (v) => (s.has(v) ? (s.delete(v), false) : (s.add(v), true));
  mk($("#chips-grade"), [1, 2, 3, 4, 5, 6].map((g) => [g, CN[g] + "年级"]), tog(S.grade));
  $("#chips-domain").append(...VC.DOMS.map((d) => { const b = el("button", { class: "chip", "data-d": d, onclick: () => { const on = tog(S.domain)(d); b.classList.toggle("on", on); VC.refreshFilters(); } }, [el("i", { class: "sw", style: { background: `var(--${d})` } }), VC.DOM[d].name]); return b; }));
  const more = $("#chips-vt"), src = $("#chips-src");
  const VTT = { program: "答案由求解程序算出并验证", rule: "按规则校验（如作图、判断）", human: "开放作答：答案不唯一，需要教师判断" };
  VC.VT.forEach((t) => { const b = el("button", { class: "chip", title: "筛选含该类题型的知识点 · " + VTT[t], onclick: () => { b.classList.toggle("on", tog(S.vt)(t)); VC.refreshFilters(); } }, [VC.VTN[t]]); more.append(b); });
  const gap = el("button", { class: "chip", title: "教材未引入、由版本对齐阶段补全的知识点", onclick: () => { S.gap = !S.gap; gap.classList.toggle("on", S.gap); VC.refreshFilters(); } }, ["◆ 补全"]);
  const nw = el("button", { class: "chip", onclick: () => { S.std = S.std === 2022 ? null : 2022; nw.classList.toggle("on", S.std === 2022); old.classList.remove("on"); VC.refreshFilters(); } }, ["新版"]);
  const old = el("button", { class: "chip", onclick: () => { S.std = S.std === 2011 ? null : 2011; old.classList.toggle("on", S.std === 2011); nw.classList.remove("on"); VC.refreshFilters(); } }, ["旧版"]);
  src.append(gap, nw, old);
};
VC.syncChips = function () {
  const S = VC.S;
  $$("#chips-grade .chip").forEach((b) => b.classList.toggle("on", S.grade.has(+b.dataset.v)));
  $$("#chips-domain .chip").forEach((b) => b.classList.toggle("on", S.domain.has(b.dataset.d)));
};
VC.refreshFilters = function () {
  const n = VC.D.kps.filter((k) => VC.passFilter(k)).length;
  $("#filter-count").textContent = VC.filterActive() ? `${n} / ${VC.D.kps.length} 个知识点` : "";
  const S = VC.S, groups = (S.grade.size ? 1 : 0) + (S.domain.size ? 1 : 0) + (S.vt.size ? 1 : 0) + (S.gap || S.std ? 1 : 0), bd = $("#filter-badge"); bd.hidden = !groups; bd.textContent = groups;
  VC.pan.edgeCache = null; VC.pan.kick(); if (VC.Timeline.mini) { VC.Timeline.mini.edgeCache = null; VC.Timeline.mini.kick(); }
  const eh = $("#empty-hint");
  if (VC.S.view === "pan") { eh.hidden = n > 0; eh.textContent = "没有满足所有筛选条件的知识点，试着放宽一个条件"; }
  VC.hashSync();
};
