/* 聚焦图：选中知识点的前置链 / 后续依赖，dagre 分层布局，SVG 渲染，边可点击查看证据 */
VC.Focus = {
  NW: 168, NH: 48,
  EST: {
    pre:  { stroke: "var(--ink-2)", w: 1.7, dash: "" },
    imp:  { stroke: "var(--ink-3)", w: 1.1, dash: "5 4" },
    bld:  { stroke: "var(--na)", w: 1.5, dash: "2 3" },
    ext:  { stroke: "var(--up)", w: 1.6, dash: "7 3" },
    rel:  { stroke: "var(--sp)", w: 1.5, dash: "1 4" },
    con:  { stroke: "var(--bad)", w: 2, dash: "8 3" },
  },
  styleOf(e) { return e.k === 0 ? this.EST[e.d ? "pre" : "imp"] : this.EST[["", "bld", "ext", "rel", "con"][e.k]]; },

  init() {
    const svg = (this.svg = d3.select("#focus-svg"));
    this.defs = svg.append("defs");
    Object.entries(this.EST).forEach(([k, s]) => this.defs.append("marker").attr("id", "mk-" + k).attr("viewBox", "0 0 10 10").attr("refX", 9).attr("refY", 5)
      .attr("markerWidth", 7).attr("markerHeight", 7).attr("orient", "auto-start-reverse").append("path").attr("d", "M0,1 L9,5 L0,9 z").style("fill", s.stroke));
    this.vp = svg.append("g").attr("class", "vp");
    this.gE = this.vp.append("g"); this.gN = this.vp.append("g");
    this.zoom = d3.zoom().scaleExtent([0.25, 3]).on("zoom", (ev) => this.vp.attr("transform", ev.transform));
    svg.call(this.zoom).on("dblclick.zoom", null);
    svg.on("click", (ev) => { if (ev.target === svg.node()) this.closeEdge(); });
    this.buildBar();
    this.prevPos = new Map();
  },

  buildBar() {
    const S = VC.S, bar = $("#focus-bar");
    const tg = (key, label, cls) => {
      const lab = el("label", { class: "tg" }, [el("input", { type: "checkbox", "data-k": key }), el("i", { style: { borderColor: this.EST[cls].stroke, borderTopStyle: cls === "pre" ? "solid" : "dashed" } }), label]);
      lab.querySelector("input").addEventListener("change", (e) => { S.show[key] = e.target.checked; this.render(true); VC.hashSync(); });
      return lab;
    };
    bar.append(
      el("span", { class: "ttl", id: "fb-title", text: "" }), el("span", { class: "sep" }),
      el("label", {}, ["深度 ", el("input", { type: "range", id: "fb-depth", min: 1, max: 7, value: S.depth }), el("b", { id: "fb-depth-v", text: S.depth })]),
      el("span", { class: "sep" }), tg("implied", "隐含边", "imp"), tg("builds_on", "递进", "bld"), tg("related", "相关", "rel"), tg("confusable", "易混淆", "con"), tg("extends", "螺旋扩展", "ext"),
      el("span", { style: { marginLeft: "auto" } }), el("button", { class: "btn tiny", id: "fb-back", text: "← 返回全景", onclick: () => VC.setView("pan") }));
    $("#fb-depth").addEventListener("input", (e) => { S.depth = +e.target.value; $("#fb-depth-v").textContent = S.depth >= 7 ? "全部" : S.depth; this.render(true); VC.hashSync(); });
    $("#fb-depth-v").textContent = S.depth >= 7 ? "全部" : S.depth;
  },

  syncBar() {
    const S = VC.S;
    $$("#focus-bar input[data-k]").forEach((i) => (i.checked = !!S.show[i.dataset.k]));
    $("#fb-depth").value = S.depth; $("#fb-depth-v").textContent = S.depth >= 7 ? "全部" : S.depth;
  },

  /** 收集可见节点：前置链上下游（深度限制）+ 所选类型的一跳邻居 */
  collect(idx) {
    const D = VC.D, S = VC.S, depth = S.depth >= 7 ? Infinity : S.depth, CAP = 110;
    const nodes = new Map([[idx, 0]]);
    let truncated = false;
    const bfs = (adjList, pick, sign) => {
      let frontier = [idx], d = 0;
      while (frontier.length && d < depth) {
        d++; const nxt = [];
        for (const u of frontier) for (const ei of adjList[u]) {
          const v = pick(D.edges[ei]);
          if (!nodes.has(v)) { if (nodes.size >= CAP) { truncated = true; continue; } nodes.set(v, sign * d); nxt.push(v); }
        }
        frontier = nxt;
      }
    };
    bfs(D.inn, (e) => e.f, -1); bfs(D.out, (e) => e.t, 1);
    for (const [key, kcode] of [["builds_on", 1], ["extends", 2], ["related", 3], ["confusable", 4]]) {
      if (!S.show[key]) continue;
      const cand = D.adj[idx].map((ei) => D.edges[ei]).filter((e) => e.k === kcode).sort((a, b) => (b.c || 0) - (a.c || 0)).slice(0, 8);
      cand.forEach((e) => { const v = e.f === idx ? e.t : e.f; if (!nodes.has(v)) nodes.set(v, 0.5); });
    }
    // 边：端点都在可见集内
    const edges = [], seen = new Set();
    for (const v of nodes.keys()) for (const ei of D.adj[v]) {
      if (seen.has(ei)) continue; seen.add(ei);
      const e = D.edges[ei];
      if (!nodes.has(e.f) || !nodes.has(e.t)) continue;
      if (e.k === 0 && e.d === 1) edges.push(e);
      else if (e.k === 0 && S.show.implied) edges.push(e);
      else if (e.k === 1 && S.show.builds_on) edges.push(e);
      else if (e.k === 2 && S.show.extends) edges.push(e);
      else if (e.k === 3 && S.show.related) edges.push(e);
      else if (e.k === 4 && S.show.confusable) edges.push(e);
      if (edges.length > 600) break;
    }
    return { nodes, edges, truncated };
  },

  render(animate = true) {
    const S = VC.S, D = VC.D, idx = S.sel;
    const wrap = $("#focus-wrap");
    if (idx == null) { this.gN.selectAll("*").remove(); this.gE.selectAll("*").remove(); $("#fb-title").textContent = "请先选择一个知识点"; $("#empty-hint").hidden = false; $("#empty-hint").textContent = "在全景图中点击一个知识点，或用顶部搜索框，再回到聚焦图"; return; }
    $("#empty-hint").hidden = true;
    const { nodes, edges, truncated } = this.collect(idx);
    $("#fb-title").textContent = D.kps[idx].n; $("#fb-title").title = D.kps[idx].n;
    const g = new dagre.graphlib.Graph({ multigraph: false });
    g.setGraph({ rankdir: "LR", nodesep: 14, ranksep: 74, marginx: 24, marginy: 24, ranker: "network-simplex" });
    g.setDefaultEdgeLabel(() => ({}));
    nodes.forEach((_, i) => g.setNode(String(i), { width: this.NW, height: this.NH }));
    const pairKey = (e) => e.f + ">" + e.t, seen = new Set();
    edges.forEach((e) => {
      if (e.k === 0 && !e.d) return;
      if (seen.has(pairKey(e))) return; seen.add(pairKey(e));
      g.setEdge(String(e.f), String(e.t), { weight: e.k === 0 ? 3 : 1, minlen: 1 });
    });
    // 没有任何边相连的节点（仅被选择性邻居拉入）：连到焦点上
    nodes.forEach((_, i) => { if (i !== idx && !g.nodeEdges(String(i)).length) g.setEdge(String(idx), String(i), { weight: 0.5 }); });
    dagre.layout(g);

    // ---- 节点
    const data = [...nodes.keys()].map((i) => { const p = g.node(String(i)); return { i, x: p.x, y: p.y, k: D.kps[i] }; });
    const dur = animate && !VC.reduced ? 620 : 0, prev = this.prevPos;
    const sel = this.gN.selectAll("g.fnode").data(data, (d) => d.i);
    sel.exit().transition().duration(dur / 2).style("opacity", 0).remove();
    const ent = sel.enter().append("g").attr("class", "fnode").style("opacity", 0)
      .attr("transform", (d) => { const p = prev.get(d.i) || this.nearest(prev, d, data); return `translate(${(p ? p.x : d.x) - this.NW / 2},${(p ? p.y : d.y) - this.NH / 2})`; });
    ent.append("rect").attr("class", "bg").attr("width", this.NW).attr("height", this.NH).attr("rx", 11);
    ent.append("rect").attr("class", "bar").attr("width", 5).attr("height", this.NH - 14).attr("x", 0).attr("y", 7).attr("rx", 2.5);
    ent.append("text").attr("class", "t1").attr("x", 14).attr("y", 19);
    ent.append("text").attr("class", "t2").attr("x", 14).attr("y", 33);
    ent.append("text").attr("class", "g").attr("x", this.NW - 8).attr("y", this.NH - 7).attr("text-anchor", "end");
    ent.append("title");
    ent.on("click", (ev, d) => { VC.select(d.i, { refocus: true }); }).on("mouseenter", (ev, d) => this.hoverNode(d.i)).on("mouseleave", () => this.hoverNode(null));
    const all = ent.merge(sel);
    all.classed("sel", (d) => d.i === idx);
    all.select("rect.bar").style("fill", (d) => VC.C.dom[d.k.d]);
    all.select("rect.bg").style("stroke-dasharray", (d) => (d.k.pv ? "5 3" : null)).style("stroke", (d) => (d.k.pv && d.i !== idx ? "var(--gold)" : null));
    all.select("title").text((d) => `${d.k.n}\n${VC.kpLoc(d.k).label}`);
    all.each(function (d) {
      const name = d.k.n, a = name.slice(0, 9), b = name.length > 9 ? name.slice(9, 17) + (name.length > 17 ? "…" : "") : "";
      const s = d3.select(this);
      s.select("text.t1").text(a).attr("y", b ? 19 : 25); s.select("text.t2").text(b);
      s.select("text.g").text(VC.semShort(d.k.b)).style("display", b ? null : "none");
    });
    all.transition().duration(dur).style("opacity", 1).attr("transform", (d) => `translate(${d.x - this.NW / 2},${d.y - this.NH / 2})`);
    this.prevPos = new Map(data.map((d) => [d.i, { x: d.x, y: d.y }]));

    // ---- 边
    const P = new Map(); // 位置
    data.forEach((d) => P.set(d.i, d));
    const line = d3.line().curve(d3.curveBasis).x((p) => p.x).y((p) => p.y);
    const pathOf = (e) => {
      const ge = g.hasEdge(String(e.f), String(e.t)) ? g.edge(String(e.f), String(e.t)) : null;
      if (ge && ge.points) return line(ge.points);
      const a = P.get(e.f), b = P.get(e.t), mx = (a.x + b.x) / 2;   // 隐含边等不参与布局：画三次曲线
      return `M${a.x + this.NW / 2},${a.y} C${mx + 30},${a.y - 26} ${mx - 30},${b.y - 26} ${b.x - this.NW / 2},${b.y}`;
    };
    this.gE.selectAll("*").remove();
    const gl = this.gE.selectAll("g.fe").data(edges, (e) => e.i).enter().append("g").attr("class", "fe").style("opacity", 0);
    gl.append("path").attr("class", "fedge").attr("d", pathOf).each((e, i, nodesEl) => {
      const st = this.styleOf(e), p = d3.select(nodesEl[i]);
      p.style("stroke", st.stroke).style("stroke-width", st.w).style("stroke-dasharray", st.dash || null).attr("marker-end", e.k === 3 || e.k === 4 ? null : `url(#mk-${e.k === 0 ? (e.d ? "pre" : "imp") : ["", "bld", "ext", "rel", "con"][e.k]})`);
      if (e.k === 3 || e.k === 4) p.attr("marker-start", null);
    });
    gl.append("path").attr("class", "fedge-hit").attr("d", pathOf).on("click", (ev, e) => { ev.stopPropagation(); this.showEdge(e); })
      .on("mouseenter", (ev, e) => VC.tip.show(`<b>${VC.EDGE[e.k].name}${e.k === 0 ? (e.d ? " · 直接" : " · 隐含") : ""}</b><div class="tm">点击查看证据与判定理由</div>`, ev.clientX, ev.clientY)).on("mouseleave", () => VC.tip.hide());
    gl.transition().delay(dur * 0.4).duration(dur * 0.8).style("opacity", (e) => (e.k === 0 && !e.d ? 0.65 : 1));
    this.edgeSel = gl;
    this.syncLegend(edges);

    // 提示 + 适配视图
    const ud = [...nodes.values()];
    const msg = `上游 ${ud.filter((v) => v < 0).length} · 下游 ${ud.filter((v) => v > 0 && v !== 0.5).length}` + (truncated ? " · 已截断显示最近的 " + nodes.size + " 个" : "");
    $("#fb-title").title = msg;
    this.stat = msg; $("#focus-legend").dataset.stat = msg;
    this.fit(data, dur);
    if (this.edgeOpen && !edges.some((e) => e.i === this.edgeOpen)) this.closeEdge();
  },

  nearest(prev, d, data) {   // 新节点从它的邻居位置“长出来”
    const D = VC.D;
    for (const ei of D.adj[d.i]) { const e = D.edges[ei], o = e.f === d.i ? e.t : e.f; if (prev.has(o)) return prev.get(o); }
    return null;
  },

  hoverNode(i) {
    if (!this.edgeSel) return;
    const D = VC.D;
    this.edgeSel.style("opacity", (e) => (i == null ? (e.k === 0 && !e.d ? 0.65 : 1) : e.f === i || e.t === i ? 1 : 0.12));
    this.gN.selectAll("g.fnode").style("opacity", (d) => (i == null || d.i === i || D.adj[i].some((ei) => D.edges[ei].f === d.i || D.edges[ei].t === d.i) ? 1 : 0.35));
  },

  fit(data, dur) {
    const svg = $("#focus-svg"), W = svg.clientWidth || 800, H = svg.clientHeight || 500;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    data.forEach((d) => { x0 = Math.min(x0, d.x - this.NW / 2); x1 = Math.max(x1, d.x + this.NW / 2); y0 = Math.min(y0, d.y - this.NH / 2); y1 = Math.max(y1, d.y + this.NH / 2); });
    const pad = 36, k = clamp(Math.min((W - pad * 2) / (x1 - x0), (H - pad * 2) / (y1 - y0)), 0.25, 1.15);
    const t = d3.zoomIdentity.translate((W - (x1 - x0) * k) / 2 - x0 * k, (H - (y1 - y0) * k) / 2 - y0 * k).scale(k);
    (dur ? this.svg.transition().duration(dur) : this.svg).call(this.zoom.transform, t);
  },

  syncLegend(edges) {
    const kinds = new Set(edges.map((e) => (e.k === 0 ? (e.d ? "pre" : "imp") : ["", "bld", "ext", "rel", "con"][e.k])));
    const names = { pre: "直接前置", imp: "隐含前置", bld: "递进", ext: "螺旋扩展", rel: "相关", con: "易混淆" };
    $("#focus-legend").innerHTML = [...kinds].map((k) => `<span><i style="border-color:${this.EST[k].stroke};border-top-style:${this.EST[k].dash ? "dashed" : "solid"}"></i>${names[k]}</span>`).join("") +
      `<span style="color:var(--ink-3)">${esc(this.stat || "")}</span>`;
  },

  /* ---------- 边的证据 ---------- */
  ROUTES: { cooccurrence: "习题共现", time_same_thread: "同主线时间序", model_screen: "模型筛选（正向）", model_screen_reverse: "模型筛选（反向）", stage2_extends: "实体消解中的“扩展”关系", stage5_gap_link: "缺口补全时建立的链接" },
  showEdge(e) {
    const D = VC.D, a = D.kps[e.f], b = D.kps[e.t], ev = e.e || {}, card = $("#edge-card");
    this.edgeOpen = e.i;
    const type = e.k === 0 ? (e.d ? "直接前置" : "隐含前置（被传递约简）") : VC.EDGE[e.k].name;
    const rows = [];
    if (e.c != null) rows.push(["判定置信度", `<span class="conf"><i style="width:${e.c * 100}%"></i></span>${e.c.toFixed(2)}`]);
    if (e.lb) rows.push(["模型判定标签", esc(e.lb)]);
    if (ev.c != null) rows.push(["习题共现", `${ev.c} 次（占后者习题的 ${(ev.r * 100).toFixed(0)}%，后者共 ${ev.bi} 题）`]);
    if (ev.rc) rows.push(["反向共现", ev.rc + " 次"]);
    if (ev.st != null) rows.push(["同一主线", ev.st ? "是" : "否"]);
    if (ev.gap != null) rows.push(["引入间隔", `${ev.gap} 个学期`]);
    if (ev.ro && ev.ro.length) rows.push(["候选来源", ev.ro.map((r) => this.ROUTES[r] || r).join("、")]);
    if (ev.iv) rows.push(["传递路径", "经由 " + ev.iv.map((i) => esc(D.kps[i].n)).join("、")]);
    const src = e.s === 5 ? "版本对齐（Stage 5）" : e.s === 2 ? "实体消解（Stage 2）" : "关系推断（Stage 4）";
    rows.push(["来源阶段", src]);
    if (e.s5) rows.push(["复核动作", e.s5 === "dropped_prerequisite" ? "逆序前置被降为“相关”" : e.s5 === "gap_edge" ? "补全知识点的关系边" : esc(e.s5)]);
    card.innerHTML = `<button class="x" aria-label="关闭">×</button><h5>${esc(a.n)} <span style="color:var(--ink-3)">→</span> ${esc(b.n)}</h5>` +
      `<span class="tag ${e.k === 0 && e.d ? "" : "gold"}">${type}</span>` +
      (e.r ? `<div class="rs">${esc(e.r)}</div>` : `<div class="rs muted">（该边未记录判定理由）</div>`) +
      `<dl>${rows.map((r) => `<dt>${r[0]}</dt><dd>${r[1]}</dd>`).join("")}</dl>`;
    card.hidden = false;
    card.querySelector(".x").onclick = () => this.closeEdge();
    this.gE.selectAll("path.fedge").style("stroke-width", (x) => this.styleOf(x).w + (x.i === e.i ? 2 : 0)).style("opacity", (x) => (x.i === e.i ? 1 : 0.5));
  },
  closeEdge() {
    this.edgeOpen = null; $("#edge-card").hidden = true;
    this.gE.selectAll("path.fedge").style("stroke-width", (x) => this.styleOf(x).w).style("opacity", null);
  },
};
