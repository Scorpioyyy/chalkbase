/* 质量与统计概览：纯 SVG/HTML 图表，颜色全部取自 CSS 变量，随主题切换 */
VC.Quality = {
  init() {
    this.core(); this.metrics(); this.dist(); this.vt(); this.diff(); this.edges(); this.story(); this.std(); this.kappa(); this.limits();
  },
  /* ---- 核心结论：6 个最有说服力的指标 ---- */
  core() {
    const M = Object.fromEntries(VC.D.eval.metrics.map((m) => [m.name, m])), box = $("#q-core");
    const specs = [
      ["课标内容要求覆盖", "课标覆盖", (m) => "133/133", "133 条课标内容要求全部有对应知识点（其中 9 条判为不适用）", 0],
      ["前置闭包 F1", "前置关系准确度", (m) => m.v.toFixed(2), "前置关系传递闭包对照标注数据的 F1（验收线 0.80）", 2],
      ["题型粒度合适率", "题型归纳粒度", (m) => m.v.toFixed(2), "题型卡片被评为“粒度合适”的比例，基线是按签名直接分组", 2],
      ["生成探针边界通过率", "生成题不超纲", (m) => m.v.toFixed(3), "1313 个 program 题型各采样 20 次，答案正确且参数都在对应课时的能力边界内", 0],
      ["成对判同 F1", "知识点跨册合并", (m) => m.v.toFixed(2), "同一知识点在不同册里被正确合并的 F1（验收线 0.85）", 2],
      ["检索 recall@5", "教师口吻检索", (m) => m.v.toFixed(2), "教师需求 → 知识点，前 5 条召回（封顶口径），MRR 0.91", 2],
    ];
    const allOk = specs.every(([n]) => M[n] && M[n].ok);
    box.append(el("p", { class: "core-concl", html: allOk ? "<b>6 个核心指标全部达到验收线</b>，并且都高于最朴素的基线做法。每一环都是先写评测、跑基线，再实现。" : "核心指标见下方。" }));
    const grid = el("div", { class: "core-grid" });
    specs.forEach(([n, label, fmt, expl, dd]) => {
      const m = M[n]; if (!m) return;
      const delta = m.base != null && m.base < m.v ? `↑ +${(m.v - m.base).toFixed(dd || 2)} <i>基线 ${m.base.toFixed(2)}</i>` : m.n ? `<i>n = ${m.n.toLocaleString("en-US")}</i>` : "";
      grid.append(el("div", { class: "tile" }, [m.ok ? el("span", { class: "ok-badge", text: "✓ 达标" }) : null, el("div", { class: "k", text: label }), el("div", { class: "v", text: fmt(m) }), el("div", { class: "d", html: delta }),
        el("div", { class: "bar" }, [el("i", { style: { width: m.v * 100 + "%" } }), m.thr != null ? el("u", { style: { left: m.thr * 100 + "%" }, title: "验收线 " + m.thr }) : null, m.base != null ? el("u", { style: { left: m.base * 100 + "%", background: "var(--warn)" }, title: "基线 " + m.base }) : null]),
        el("div", { class: "e", text: expl })]));
    });
    box.append(grid);
    box.append(el("p", { class: "muted", style: { margin: "12px 2px 0", fontSize: "14px" }, text: "进度条：绿色为当前值，灰竖线为验收线，橙竖线为基线。完整的 14 项指标、分布、版本修复、课标覆盖与标注数据质量见上方各标签。" }));
  },
  head(box, title, sub) { box.append(el("div", { class: "card-h" }, [el("h3", { text: title }), el("span", { class: "sub", text: sub || "" })])); },

  /* ---- 评测指标：当前 / 基线 / 阈值 / 置信区间 ---- */
  metrics() {
    const M = VC.D.eval.metrics, box = $("#q-metrics");
    this.head(box, "评测指标 · test 划分", `最近一次运行 ${VC.D.meta.eval_ts.slice(0, 10)}；全部比例指标报告 Wilson 或 bootstrap 95% 区间`);
    const rowH = 33, top = 58, X0 = 330, X1 = 730, H = top + M.length * rowH + 14, W = 1000;
    const x = (v) => X0 + v * (X1 - X0);
    let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="评测指标对比图">`;
    for (let t = 0; t <= 4; t++) { const v = t / 4; s += `<line class="gl" x1="${x(v)}" x2="${x(v)}" y1="${top - 8}" y2="${H - 10}"/><text x="${x(v)}" y="${top - 14}" text-anchor="middle">${v.toFixed(2)}</text>`; }
    s += `<g transform="translate(${X0},8)"><circle cx="6" cy="0" r="5" fill="var(--ok)"/><text x="16" y="4">当前值</text><circle cx="82" cy="0" r="4.5" fill="var(--surface)" stroke="var(--ink-3)" stroke-width="2"/><text x="92" y="4">朴素基线</text><line x1="170" x2="170" y1="-6" y2="6" stroke="var(--ink)" stroke-width="2.5"/><text x="178" y="4">验收阈值</text><line x1="240" x2="262" y1="0" y2="0" stroke="var(--ink-2)" stroke-width="2"/><text x="268" y="4">95% 区间</text></g>`;
    let last = "";
    M.forEach((m, i) => {
      const y = top + i * rowH + rowH / 2, col = m.ok === false ? "var(--bad)" : m.ok ? "var(--ok)" : "var(--accent)";
      if (m.stage !== last) { s += `<text x="0" y="${y + 4}" style="font-weight:700;fill:var(--ink)">${m.stage}</text>`; last = m.stage; }
      s += `<g class="mrow" data-i="${i}"><rect class="bgrow" x="-6" y="${y - rowH / 2 + 2}" width="${W + 12}" height="${rowH - 4}" rx="8"/>`;
      s += `<text x="86" y="${y + 4}">${esc(m.name)}</text><line class="ax" x1="${X0}" x2="${X1}" y1="${y}" y2="${y}"/>`;
      if (m.thr != null) s += `<line x1="${x(m.thr)}" x2="${x(m.thr)}" y1="${y - 9}" y2="${y + 9}" stroke="var(--ink)" stroke-width="2.5"/>`;
      if (m.ci) s += `<line class="ci" x1="${x(m.ci[0])}" x2="${x(m.ci[1])}" y1="${y}" y2="${y}" stroke="var(--ink-2)" stroke-width="2.2" stroke-linecap="round" opacity="0"/>`;
      if (m.base != null) s += `<circle cx="${x(m.base)}" cy="${y}" r="5" fill="var(--surface)" stroke="var(--ink-3)" stroke-width="2"/>`;
      s += `<circle class="cur" cx="${x(m.base != null ? m.base : 0)}" data-cx="${x(m.v)}" cy="${y}" r="6.5" fill="${col}" stroke="var(--surface)" stroke-width="2"/>`;
      s += `<text x="${X1 + 24}" y="${y + 4}" style="font-weight:700;fill:var(--ink);font-variant-numeric:tabular-nums">${m.v.toFixed(m.v >= 0.9995 ? 2 : 3).replace(/0+$/, "").replace(/\.$/, ".0")}</text>`;
      s += `<text x="${X1 + 82}" y="${y + 4}" style="font-size:11px;fill:var(--ink-3);font-variant-numeric:tabular-nums">${m.ci ? `[${m.ci[0].toFixed(2)}, ${m.ci[1].toFixed(2)}]` : ""}${m.n ? ` n=${m.n.toLocaleString("en-US")}` : ""}</text>`;
      s += `<text x="${W}" y="${y + 4}" text-anchor="end" style="font-size:11.5px;fill:${m.ok === false ? "var(--bad)" : m.ok ? "var(--ok)" : "var(--ink-3)"}">${m.thr != null ? (m.ok ? "✓ " : "✗ ") + "≥" + m.thr : "无阈值"}</text></g>`;
    });
    s += "</svg>";
    box.append(el("div", { class: "chart", html: s }), el("div", { class: "muted", style: { fontSize: "14px", marginTop: "6px" }, text: "越界探针查准的基线为 1.0，是“几乎什么都不判越界”的退化结果（其查全仅 0.13）。圆点向右越过竖线即达标；标注数据为多模型交叉标注，绝对值请结合 κ 与已知局限阅读。" }));
    const play = () => $$(".cur", box).forEach((c, i) => setTimeout(() => { c.style.cx = c.dataset.cx + "px"; }, 80 * i)), ci = () => $$(".ci", box).forEach((c) => (c.style.opacity = 1));
    VC.onReveal(box, () => { play(); setTimeout(ci, 600); });
    $$(".mrow", box).forEach((g) => {
      const m = M[+g.dataset.i];
      g.addEventListener("mousemove", (e) => VC.tip.show(`<b>${esc(m.stage)} · ${esc(m.name)}</b><div>当前 ${m.v}${m.ci ? "　区间 [" + m.ci.join(", ") + "]" : ""}${m.n ? "　n=" + m.n : ""}</div><div class="tm">基线 ${m.base == null ? "—" : m.base}　阈值 ${m.thr == null ? "—" : m.thr}</div>`, e.clientX, e.clientY));
      g.addEventListener("mouseleave", () => VC.tip.hide());
    });
  },

  /* ---- 各学期 × 领域知识点分布 ---- */
  dist() {
    const D = VC.D, box = $("#q-dist");
    this.head(box, "知识点按学期与领域的分布", "按首次引入位置统计");
    const W = 640, H = 330, L = 34, B = 40, T = 26, bw = (W - L) / 12;
    const cnt = D.books.map((_, b) => VC.DOMS.map((d) => D.kps.filter((k) => k.b === b && k.d === d).length));
    const mx = Math.max(...cnt.map((c) => c.reduce((a, b) => a + b, 0)));
    const y = (v) => T + (H - T - B) * (1 - v / (mx * 1.08));
    let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="各学期知识点分布">`;
    for (let t = 0; t <= 4; t++) { const v = Math.round((mx * 1.08 * t) / 4 / 10) * 10; s += `<line class="gl" x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${v}</text>`; }
    cnt.forEach((c, b) => {
      let acc = 0; const x0 = L + b * bw + bw * 0.16, w = bw * 0.68;
      s += `<g class="bar" data-b="${b}" style="--d:${b * 55}ms">`;
      c.forEach((v, di) => { if (!v) return; const y1 = y(acc + v), y0 = y(acc); s += `<rect x="${x0}" y="${y1}" width="${w}" height="${y0 - y1 - 0.8}" rx="2" fill="var(--${VC.DOMS[di]})"/>`; acc += v; });
      s += `<text x="${x0 + w / 2}" y="${y(acc) - 5}" text-anchor="middle" style="font-weight:700;fill:var(--ink);font-size:11.5px">${acc}</text>`;
      s += `<text x="${x0 + w / 2}" y="${H - B + 17}" text-anchor="middle" style="fill:var(--ink)">${VC.semShort(b)}</text>`;
      if (D.books[b].std === 2011) s += `<rect x="${x0}" y="${H - B + 22}" width="${w}" height="3" rx="1.5" fill="var(--gold)"/><text x="${x0 + w / 2}" y="${H - 6}" text-anchor="middle" style="font-size:10.5px;fill:var(--warn)">旧版</text>`;
      s += `</g>`;
    });
    s += "</svg>";
    const leg = el("div", { class: "legend-inline", html: VC.DOMS.map((d) => `<span><i style="background:var(--${d})"></i>${VC.DOM[d].name} ${D.kps.filter((k) => k.d === d).length}</span>`).join("") });
    box.append(el("div", { class: "chart", html: s }), leg);
    $$(".bar", box).forEach((g) => {
      const b = +g.dataset.b;
      g.addEventListener("mousemove", (e) => VC.tip.show(`<b>${VC.semName(b)}</b>` + VC.DOMS.map((d, i) => `<div>${VC.DOM[d].name}：${cnt[b][i]}</div>`).join(""), e.clientX, e.clientY));
      g.addEventListener("mouseleave", () => VC.tip.hide());
    });
  },

  /* ---- 可验证类型环形图 ---- */
  vt() {
    const st = VC.D.stats, box = $("#q-vt"), tot = st.vt_diff.flat().reduce((a, b) => a + b, 0);
    this.head(box, "题型的可验证类型", `${tot} 个题型卡片`);
    const vals = st.vt_diff.map((r) => r.reduce((a, b) => a + b, 0)), cols = ["var(--ok)", "var(--warn)", "var(--ink-3)"];
    const R = 80, r = 52, cx = 110, cy = 100; let a0 = -Math.PI / 2, s = `<svg viewBox="0 0 220 200">`;
    vals.forEach((v, i) => { const a1 = a0 + (v / tot) * 2 * Math.PI, lg = a1 - a0 > Math.PI ? 1 : 0;
      const p = (rad, a) => `${cx + rad * Math.cos(a)},${cy + rad * Math.sin(a)}`;
      s += `<path class="arc" style="--d:${i * 150}ms" d="M${p(R, a0)} A${R},${R} 0 ${lg} 1 ${p(R, a1 - 0.012)} L${p(r, a1 - 0.012)} A${r},${r} 0 ${lg} 0 ${p(r, a0)} Z" fill="${cols[i]}"/>`; a0 = a1; });
    s += `<text x="${cx}" y="${cy - 2}" text-anchor="middle" style="font-size:30px;font-weight:800;fill:var(--ink)">${(vals[0] / tot * 100).toFixed(0)}%</text><text x="${cx}" y="${cy + 18}" text-anchor="middle">可由程序求解</text></svg>`;
    box.append(el("div", { class: "chart donut", html: s }), el("div", { class: "legend-inline col", html: vals.map((v, i) => `<span><i style="background:${cols[i]}"></i>${VC.VTN[VC.VT[i]]} <b>${v}</b></span>`).join("") }),
      el("div", { class: "muted", style: { fontSize: "14px", marginTop: "6px" }, text: "program：程序可求解并验证；rule：可按规则校验（如作图、判断）；human（开放作答）：答案不唯一，需要教师判断。" }));
  },

  diff() {
    const st = VC.D.stats, box = $("#q-diff");
    this.head(box, "题型难度分布", "1 级最易，5 级最难");
    const W = 340, H = 220, L = 30, B = 30, T = 10, bw = (W - L) / 5, cols = ["var(--ok)", "var(--warn)", "var(--ink-3)"];
    const tot = [0, 1, 2, 3, 4].map((d) => st.vt_diff.reduce((a, r) => a + r[d], 0)), mx = Math.max(...tot);
    const y = (v) => T + (H - T - B) * (1 - v / (mx * 1.1));
    let s = `<svg viewBox="0 0 ${W} ${H}">`;
    for (let t = 0; t <= 3; t++) { const v = Math.round((mx * 1.1 * t) / 3 / 50) * 50; s += `<line class="gl" x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 5}" y="${y(v) + 4}" text-anchor="end">${v}</text>`; }
    for (let d = 0; d < 5; d++) {
      let acc = 0; const x0 = L + d * bw + bw * 0.18, w = bw * 0.64;
      s += `<g class="bar" style="--d:${d * 70}ms">`;
      for (let t = 0; t < 3; t++) { const v = st.vt_diff[t][d]; if (!v) continue; s += `<rect x="${x0}" y="${y(acc + v)}" width="${w}" height="${y(acc) - y(acc + v) - 0.6}" rx="2" fill="${cols[t]}"/>`; acc += v; }
      s += `<text x="${x0 + w / 2}" y="${y(acc) - 5}" text-anchor="middle" style="font-weight:700;fill:var(--ink)">${acc}</text><text x="${x0 + w / 2}" y="${H - 10}" text-anchor="middle" style="fill:var(--ink)">${d + 1} 级</text></g>`;
    }
    box.append(el("div", { class: "chart", html: s + "</svg>" }));
  },

  edges() {
    const st = VC.D.stats, box = $("#q-edges");
    this.head(box, "关系边的构成", `${VC.D.edges.length} 条`);
    const rows = [["前置 · 直接", 0, 1, "var(--ink-2)"], ["前置 · 隐含（传递约简）", 0, 0, "var(--ink-3)"], ["递进", 1, 1, "var(--na)"], ["螺旋扩展", 2, 1, "var(--up)"], ["相关", 3, 1, "var(--sp)"], ["易混淆", 4, 1, "var(--bad)"]];
    const cnt = (k, d) => (st.edge_counts.find((e) => e.k === VC.EDGE[k].key && e.d === d) || { n: 0 }).n, mx = Math.max(...rows.map((r) => cnt(r[1], r[2])));
    rows.forEach(([name, k, d, col]) => { const n = cnt(k, d); box.append(el("div", { class: "kbar" }, [el("span", { text: name }), el("div", { class: "tr" }, [el("i", { class: "grow", style: { background: col, width: Math.max(1.5, (n / mx) * 100) + "%" } })]), el("b", { text: n })])); });
    box.append(el("div", { class: "muted", style: { fontSize: "14px", marginTop: "8px" }, text: "前置边经传递约简后只保留 852 条直接依赖，图谱可读；被约简的隐含边在聚焦图里可一键显示。" }));
  },

  /* ---- 版本冲突修复故事 ---- */
  story() {
    const D = VC.D, S5 = D.eval.story, box = $("#q-story");
    this.head(box, "版本对齐：把新旧教材缝成一张图", "Stage 5 · 新版 2022 课标 9 册 + 旧版 2011 课标 3 册混编");
    const stat = (a, b, t, cls) => el("div", { class: "stat" }, [el("div", { class: "a", html: `<s>${a}</s><span style="color:var(--ink-3)">→</span><b class="cnt" data-to="${b.n}" data-sfx="${b.sfx || ""}">${b.n}${b.sfx || ""}</b>` }), el("div", { class: "t", html: t })]);
    const st = el("div", { class: "stats3" }, [
      stat(S5.order_before, { n: S5.order_after }, "<b>逆序前置边</b>：前置知识点晚于后继才引入。11 条经分诊判为关系推断的误判，改记为“相关”，没有改动教材顺序"),
      stat(`${S5.cov_before}/${S5.cov_n}`, { n: S5.cov_n, sfx: `/${S5.cov_n}` }, "<b>课标内容要求覆盖</b>：133 条中 124 条有对应知识点，9 条判为不适用（逐条理由已记录）"),
      stat("0", { n: S5.gaps.length }, "<b>补全的版本缺口知识点</b>：后续知识依赖它，但没有任何一册教材引入，由对齐阶段补上并挂到相应课时"),
    ]);
    box.append(st);
    const gc = el("div", { class: "gapchips" });
    S5.gaps.forEach((g) => gc.append(el("button", { text: "◆ " + D.kps[g.k].n, title: g.how, onclick: () => VC.goExplore(g.k) })));
    box.append(el("div", { class: "muted", style: { fontSize: "14px" }, text: "14 个补全知识点（点击在图谱中定位；在全景图中以金色虚线圈标出）" }), gc);
    const det = el("details", { style: { marginTop: "14px" } }, [el("summary", { class: "muted", style: { cursor: "pointer", fontSize: "14.5px" }, text: `展开：被改判的 ${S5.drops.length} 条逆序前置边及理由` })]);
    const ul = el("ul", { class: "dr-list", style: { marginTop: "8px" } });
    S5.drops.forEach((d) => ul.append(el("li", { html: `<b>${esc(D.kps[d.f].n)} → ${esc(D.kps[d.t].n)}</b>：${esc(d.r)}` })));
    det.append(ul); box.append(det);
    VC.onReveal(box, () => $$(".cnt", box).forEach((c) => { const to = +c.dataset.to; tween(1100, (p) => (c.textContent = Math.round(to * p) + c.dataset.sfx)); }));
  },

  /* ---- 课标覆盖矩阵 ---- */
  std() {
    const D = VC.D, box = $("#q-std"), items = D.std;
    const cov = items.filter((i) => i.st === "covered").length;
    this.head(box, `课标覆盖：${items.length} 条内容要求，100% 有归属`, `${cov} 条映射到知识点 + ${items.length - cov} 条判为不适用`);
    const wf = el("div", { class: "waffle" });
    wf.append(el("div"), ...["s1", "s2", "s3"].map((s, i) => el("div", { class: "h", text: ["第一学段 1–2 年级", "第二学段 3–4 年级", "第三学段 5–6 年级"][i] })));
    VC.DOMS.forEach((d) => {
      wf.append(el("div", { class: "r", style: { color: `var(--${d})` }, text: VC.DOM[d].name }));
      ["s1", "s2", "s3"].forEach((s) => {
        const cell = el("div", { class: "wcell" });
        items.filter((i) => i.d === d && i.s === s).forEach((it, j) => {
          const sq = el("i", { class: it.st === "covered" ? "" : "na-st", style: { background: `var(--${d})`, animationDelay: j * 18 + "ms" } });
          sq.addEventListener("mousemove", (e) => VC.tip.show(`<b>${esc(it.t)}</b><div class="tm">${it.st === "covered" ? "对应：" + it.k.map((i) => esc(D.kps[i].n)).join("、") : "不适用：" + esc(it.rs || "")}</div>`, e.clientX, e.clientY));
          sq.addEventListener("mouseleave", () => VC.tip.hide());
          sq.addEventListener("click", () => it.k[0] != null && VC.goExplore(it.k[0]));
          cell.append(sq);
        });
        wf.append(cell);
      });
    });
    box.append(wf, el("div", { class: "legend-inline", style: { marginTop: "36px" }, html: `<span><i style="background:var(--ink-2)"></i>有对应知识点</span><span><i class="na-st" style="background:var(--ink-3)"></i>不适用（如素养性表述、活动类要求）</span><span class="muted">一个方块 = 一条课标内容要求，悬停看原文与对应知识点</span>` }));
  },

  kappa() {
    const E = VC.D.eval, box = $("#q-kappa");
    this.head(box, "标注数据质量：双标注一致性", "Cohen's κ · 分歧经仲裁后入库");
    const lv = (k) => (k >= 0.8 ? "几乎一致" : k >= 0.6 ? "较高" : k >= 0.4 ? "中等" : k >= 0.2 ? "一般" : "较低");
    E.kappa.slice().sort((a, b) => b.k - a.k).forEach((r) => box.append(el("div", { class: "kbar" }, [el("span", { text: r.name }), el("div", { class: "tr" }, [el("i", { class: "grow", style: { background: r.k >= 0.6 ? "var(--ok)" : r.k >= 0.4 ? "var(--warn)" : "var(--bad)", width: Math.max(2, r.k * 100) + "%" } })]), el("b", { text: r.k.toFixed(2), title: `${lv(r.k)} · n=${r.n}` })])));
    if (E.gold.length) box.append(el("div", { class: "caveat", html: "<b>标注数据抽样复核</b>（79 条，盲核）：" + E.gold.map((g) => `${esc(g.name)} ${g.p.toFixed(2)} [${g.lo.toFixed(2)}, ${g.hi.toFixed(2)}] n=${g.n}`).join("；") + "。区间下限多低于 0.95，只能说明“未发现系统性错误”。" }));
  },

  limits() {
    const box = $("#q-limits");
    this.head(box, "诚实的局限");
    box.append(el("ul", { class: "limits" }, [
      "标注数据为多模型交叉标注，检索探针 κ 只有 0.26，比例类指标宜读作“相对基线的改善”。",
      "题型长尾偏多：单实例题型 52%，生成环节需把相近题型合并展示。",
      "越界探针端到端查准 0.83：题面特征抽取偶把基础数量关系当作概念。",
      "螺旋复习类检索 recall@5 仅 0.65（n=4）。",
      "14 个补全知识点由版本对齐阶段按依赖关系推断补上，不是教材原文。",
    ].map((t) => el("li", { text: t }))));
  },
};

/** 元素首次进入视野时回调（图表动画触发） */
VC.onReveal = function (node, fn) {
  const pane = node.closest(".tabpane");
  if (!pane || pane.classList.contains("in")) return fn();
  (pane._cbs = pane._cbs || []).push(fn);
};
