/* 详情抽屉：知识点信息、前置/后续、题型卡片与“换一题” */
VC.FORM = { fill_blank: "填空", word_problem: "应用题", compute: "计算", draw: "作图", judge: "判断", read_chart: "读图", measure: "测量", choice: "选择", other: "其他" };
VC.GRANT = {
  integer_domain_max: "整数数域上限", decimal_max_places: "小数位数", fraction_types: "分数类型", concepts: "概念", units_of_measure: "计量单位",
  geometry_vocab: "几何词汇", operation_operand_forms: "运算形态",
};

VC.fmtSlot = function (name, s) {
  if (s.type === "choice") { const o = s.options; return `${name} ∈ {${o.slice(0, 4).join("、")}${o.length > 4 ? "…共" + o.length + "项" : ""}}`; }
  if (s.type === "int") return `${name} ∈ [${s.min}, ${s.max}]${s.step ? " 步长" + s.step : ""}`;
  if (s.type === "decimal") return `${name} ∈ [${s.min}, ${s.max}]${s.places != null ? " · " + s.places + "位小数" : ""}`;
  if (s.type === "fraction") return `${name} ∈ [${s.min}, ${s.max}] · 分母≤${s.max_denominator}`;
  return name;
};
const stars = (n) => `<span class="stars" title="难度 ${n}/5">${"★".repeat(n)}<i>${"★".repeat(5 - n)}</i></span>`;

VC.Detail = {
  tab: "info",
  TABS: [["info", "概况"], ["cards", "题型"], ["rel", "前置与后续"], ["cap", "能力"]],
  render(idx) {
    const D = VC.D, body = $("#drawer-body");
    if (idx == null) return;
    const k = D.kps[idx], L = VC.kpLoc(k), dm = VC.DOM[k.d];
    this.idx = idx;
    const head = el("div", { class: "dr-head" }, [
      el("span", { class: `dr-domain ${k.d}` }, [dm.name + (k.th ? " · " + k.th : "")]),
      el("h3", { text: k.n }),
      el("div", { class: "dr-loc", html: `<b>${esc(L.sem_name)}</b> · ${esc(L.unit)} · ${esc(L.title)}` }),
      el("div", { class: "tagrow" }, [
        el("span", { class: "tag", text: VC.MASTERY[k.m] || k.m }), el("span", { class: "tag", text: `${k.na} 个题型` }),
        k.pv ? el("span", { class: "tag gold", text: "◆ 版本缺口补全" }) : null,
      ]),
    ]);
    const tabs = el("div", { class: "dr-tabs", role: "tablist" }, this.TABS.map(([id, name]) => el("button", { role: "tab", "data-t": id, class: id === this.tab ? "on" : "", text: id === "cards" ? `${name} ${k.na}` : name, onclick: () => { this.tab = id; this.pane(); } })));
    body.replaceChildren(head, tabs, el("div", { class: "dr-pane", id: "dr-pane" }));
    this.pane();
  },
  pane() {
    const D = VC.D, idx = this.idx, k = D.kps[idx], box = $("#dr-pane");
    $$(".dr-tabs button").forEach((b) => b.classList.toggle("on", b.dataset.t === this.tab));
    box.replaceChildren(); box.scrollTop = 0; box.style.animation = "none"; void box.offsetWidth; box.style.animation = "";
    if (this.tab === "info") {
      box.append(el("p", { class: "dr-desc", text: k.de }));
      if (k.pn) box.append(el("div", { class: "caveat", text: k.pn }));
      if (k.te.length) box.append(this.sec("典型错误", el("ul", { class: "dr-list" }, k.te.slice(0, 3).map((t) => el("li", { text: t })))));
      box.append(el("div", { class: "dr-actions" }, [
        el("button", { class: "btn tiny", text: "在聚焦图中展开前置链", onclick: () => VC.setView("focus") }),
        el("button", { class: "btn tiny", text: "把能力边界回放到这里", onclick: () => { VC.Timeline.setLesson(k.l, true); VC.go("boundary"); } }),
      ]));
      if (k.al.length) box.append(el("div", { class: "tagrow", style: { marginTop: "14px" } }, k.al.slice(0, 6).map((a) => el("span", { class: "tag", text: "又称 " + a }))));
    } else if (this.tab === "cards") {
      const cards = el("div", {}, [el("div", { class: "muted", text: "加载中…" })]);
      box.append(cards);
      VC.getArch().then((A) => {
        if (this.idx !== idx || this.tab !== "cards") return;
        const list = A[idx] || [];
        cards.replaceChildren(...(list.length ? list.map((a, i) => this.card(a, i === 0)) : [el("div", { class: "muted", text: "该知识点没有题型卡片（不可直接考查）。" })]));
      });
    } else if (this.tab === "rel") {
      const nb = (ei, side) => { const e = D.edges[ei], o = D.kps[side === "in" ? e.f : e.t]; return el("button", { onclick: () => VC.select(o.i, { zoom: true }), title: e.r || "" }, [el("span", { class: "dot", style: { background: `var(--${o.d})` } }), o.n, el("small", { text: VC.semShort(o.b) })]); };
      const pre = D.inn[idx], suc = D.out[idx], wrap = el("div", { class: "nbr" });
      if (pre.length) { wrap.append(el("small", { class: "muted", text: `直接前置 ${pre.length}` })); pre.slice(0, 9).forEach((ei) => wrap.append(nb(ei, "in"))); }
      if (suc.length) { wrap.append(el("small", { class: "muted", text: `直接后继 ${suc.length}`, style: { marginTop: "10px" } })); suc.slice(0, 9).forEach((ei) => wrap.append(nb(ei, "out"))); }
      if (!pre.length && !suc.length) wrap.append(el("div", { class: "muted", text: "该知识点没有直接前置或后继。" }));
      box.append(wrap);
      if (k.rv.length) box.append(this.sec("复现课时（螺旋复习位置）", el("div", { class: "lesson-chips" }, k.rv.slice(0, 14).map((li) => { const l = VC.loc(li); return el("button", { class: "lc", text: l.id, title: l.label, onclick: () => { VC.Timeline.setLesson(li, true); VC.go("boundary"); } }); }))));
    } else {
      const g = k.g || {};
      const gk = Object.keys(VC.GRANT).filter((x) => g[x] != null && (!Array.isArray(g[x]) || g[x].length) && (typeof g[x] !== "object" || Array.isArray(g[x]) || Object.keys(g[x]).length));
      if (!gk.length) box.append(el("div", { class: "muted", text: "学完这个知识点没有新增的边界维度。" }));
      else {
        const dl = el("dl", { class: "grants" });
        gk.forEach((x) => {
          let v = g[x];
          if (x === "integer_domain_max") v = fmtBig(v);
          else if (x === "operation_operand_forms") v = Object.entries(v).map(([op, f]) => `${op}：${f.join("、")}`).join("；");
          else if (Array.isArray(v)) v = v.join("、");
          dl.append(el("dt", { text: VC.GRANT[x] }), el("dd", { text: String(v) }));
        });
        box.append(el("p", { class: "muted", style: { margin: "0 0 10px", fontSize: "13px" }, text: "学完这个知识点，学生新增的能力：" }), dl);
      }
    }
  },
  sec(title, node) { return el("div", { class: "dr-sec" }, [el("h4", { text: title }), node]); },

  card(a, open) {
    const tpl = esc(a.t).replace(/\{(\w+)\}/g, '<span class="slot">$1</span>');
    const vb = ["p", "r", "h"][a.v], card = el("div", { class: "acard" + (open ? " open" : "") });
    const head = el("button", { class: "acard-h", "aria-expanded": !!open, onclick: () => { card.classList.toggle("open"); head.setAttribute("aria-expanded", card.classList.contains("open")); } }, [
      el("div", { class: "acard-t", html: tpl.length > 150 && !open ? tpl.slice(0, 150) + "…" : tpl }),
      el("div", { class: "acard-m" }, [el("span", { class: "vb " + vb, text: VC.VTN[VC.VT[a.v]], title: ["答案由求解程序算出并验证", "按规则校验（如作图、判断）", "开放作答：答案不唯一，需要教师判断"][a.v] }), el("span", { html: stars(a.d) }), el("span", { text: VC.FORM[a.f] || a.f }),
        a.pv ? el("span", { class: "tag gold", text: "补全" }) : null, a.ni ? el("span", { text: `源自 ${a.ni} 道教材习题` }) : null]),
    ]);
    const bodyEl = el("div", { class: "acard-b" });
    const slots = Object.entries(a.sl);
    if (slots.length) bodyEl.append(el("div", { class: "slots", title: "参数约束" }, slots.map(([n, s]) => el("span", { text: VC.fmtSlot(n, s) }))));
    if (a.cs.length) bodyEl.append(el("details", {}, [el("summary", { class: "muted", text: `约束条件 ${a.cs.length} 条` }), el("ul", { class: "dr-list" }, a.cs.slice(0, 6).map((c) => el("li", { class: "mono", html: `<code>${esc(c)}</code>` })))]));
    if (a.fm) bodyEl.append(el("div", { class: "muted", style: { fontSize: "12.5px", margin: "4px 0 8px" }, text: "答案形式：" + a.fm }));
    // 样例池：program 类用运行时实例化结果，其余用卡片里的改写示例
    const useSm = a.sm && a.sm.length, pool = useSm ? a.sm.map((s) => ({ q: s[0], a: s[1], ok: true })) : a.ex.map((e) => ({ q: e[0], a: e[1], s: e[2], ok: false }));
    if (pool.length) {
      let i = 0;
      const box = el("div", { class: "sample" }), cnt = el("span", { class: "muted" });
      const showS = () => {
        const s = pool[i % pool.length];
        const steps = !s.s && a.st.length ? el("ol", {}, a.st.map((t) => el("li", { text: t.replace(/^\d+[.、]\s*/, "") }))) : el("div", { style: { fontSize: "13px", color: "var(--ink-2)", marginTop: "6px" }, text: s.s || "" });
        box.replaceChildren(el("div", { class: "q", text: s.q }),
          el("details", {}, [el("summary", { text: "查看答案与解题步骤" }), el("div", { class: "ans", html: s.a ? `答案：<b>${esc(s.a)}</b>` : '<span class="muted">此类题型无单一程序答案，需按解题步骤核对</span>' }), steps]),
          el("div", { class: s.ok ? "ver" : "ver no", text: s.ok ? "✓ 程序已验证（答案由求解程序算出）" : a.v === 0 ? "改写示例 · 题型可程序验证" : "改写示例" }));
        box.classList.remove("swap"); void box.offsetWidth; box.classList.add("swap");
        cnt.textContent = `${(i % pool.length) + 1} / ${pool.length}`;
      };
      showS();
      bodyEl.append(box, el("div", { class: "shuffle" }, [cnt, el("button", { text: "换一题 ↻", onclick: (e) => { e.stopPropagation(); i++; showS(); } })]));
    }
    if (a.cx.length) bodyEl.append(el("div", { class: "tagrow" }, a.cx.slice(0, 4).map((c) => el("span", { class: "tag", text: "情境：" + c }))));
    if (a.te.length) bodyEl.append(el("details", {}, [el("summary", { class: "muted", text: "易错点" }), el("ul", { class: "dr-list" }, a.te.map((t) => el("li", { text: t })))]));
    card.append(head, bodyEl);
    return card;
  },
};
