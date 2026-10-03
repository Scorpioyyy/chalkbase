/* 全景图：12 学期 × 4 领域泳道，canvas 绘制，d3-zoom 缩放平移 */

/** 沿直接前置边的上游 / 下游传递闭包（含边） */
VC.chain = function (idx, maxDepth = Infinity) {
  const D = VC.D;
  const walk = (start, adjList, pick) => {
    const nodes = new Map([[start, 0]]), edges = [];
    let frontier = [start];
    for (let d = 1; d <= maxDepth && frontier.length; d++) {
      const nxt = [];
      for (const u of frontier) for (const ei of adjList[u]) {
        const e = D.edges[ei], v = pick(e);
        edges.push(ei);
        if (!nodes.has(v)) { nodes.set(v, d); nxt.push(v); }
      }
      frontier = nxt;
    }
    return { nodes, edges };
  };
  return { up: walk(idx, D.inn, (e) => e.f), down: walk(idx, D.out, (e) => e.t) };
};

VC.Panorama = class {
  constructor(canvas, opts = {}) {
    this.cv = canvas; this.opts = opts; this.compact = !!opts.compact;
    this.tr = d3.zoomIdentity; this.dirty = true; this.running = false; this.flash = []; this.hoverIdx = null; this.t0 = performance.now();
    this.pad = this.compact ? { l: 10, t: 22, r: 10, b: 8 } : { l: 92, t: 54, r: 14, b: 46 };
    this.chainSel = null; this.chainHover = null;
    this.nodes = VC.D.kps.map((k) => ({ k, i: k.i }));
    this.layout();
    if (!this.compact) this.initZoom();
    this.bind();
  }

  /* ---------- 布局 ---------- */
  layout() {
    const D = VC.D, r = this.cv.getBoundingClientRect(), p = this.pad;
    this.vw = Math.max(300, r.width); this.vh = Math.max(200, r.height);
    const W = this.vw - p.l - p.r, H = this.vh - p.t - p.b;
    this.W = W; this.H = H; this.bandW = W / 12;
    const gap = this.compact ? 5 : 10, wts = [0.47, 0.30, 0.12, 0.11];
    let y = p.t; this.lanes = [];
    VC.DOMS.forEach((d, i) => { const h = (H - gap * 3) * wts[i]; this.lanes.push({ d, top: y, h, mid: y + h / 2 }); y += h + gap; });
    const sizeMul = this.compact ? 0.62 : 1, byAd = VC.S.size === "ad";
    this.nodes.forEach((n, i) => {
      const k = n.k, bl = D.bookLessons[k.b], frac = (k.l - bl[0]) / Math.max(1, bl[1] - bl[0]);
      const lane = this.lanes[VC.DOM[k.d].i];
      n.lane = lane; n.ax = p.l + this.bandW * (k.b + 0.1 + 0.8 * frac); n.ay = lane.mid;
      n.r = (byAd ? 3.4 + 1.5 * (k.ad || 1) : 3.4 + 1.55 * Math.sqrt(Math.max(k.na, 0.6))) * sizeMul;
      n.x = n.ax; n.y = n.ay + ((i * 37) % 13 - 6);
      n.bx0 = p.l + this.bandW * k.b + 3; n.bx1 = p.l + this.bandW * (k.b + 1) - 3;
    });
    const clampF = () => this.nodes.forEach((n) => {
      n.x = clamp(n.x, n.bx0 + n.r, n.bx1 - n.r); n.y = clamp(n.y, n.lane.top + n.r + 1, n.lane.top + n.lane.h - n.r - 1);
    });
    const sim = d3.forceSimulation(this.nodes).alpha(1).alphaDecay(0.02).velocityDecay(0.35)
      .force("x", d3.forceX((n) => n.ax).strength(0.22)).force("y", d3.forceY((n) => n.ay).strength(0.09))
      .force("c", d3.forceCollide((n) => n.r + (this.compact ? 0.8 : 1.6)).iterations(3)).force("clamp", clampF).stop();
    for (let i = 0; i < 260; i++) sim.tick();
    clampF();
    this.qt = d3.quadtree().x((n) => n.x).y((n) => n.y).addAll(this.nodes);
    // 边几何与路径
    this.eg = VC.D.direct.map((e) => {
      const a = this.nodes[e.f], b = this.nodes[e.t], dx = Math.max(24, Math.abs(b.x - a.x) * 0.45);
      return { e, a, b, c1x: a.x + dx, c1y: a.y, c2x: b.x - dx, c2y: b.y };
    });
    this.edgeCache = null; this.dirty = true;
  }

  edgePaths() {
    const S = VC.S, key = [S.lesson, [...S.grade], [...S.domain], [...S.vt], S.gap, S.std, S.size].join("|");
    if (this.edgeCache && this.edgeCache.key === key) return this.edgeCache;
    const on = new Path2D(), off = new Path2D();
    for (const g of this.eg) {
      const act = this.alpha(g.a) > 0.5 && this.alpha(g.b) > 0.5, P = act ? on : off;
      P.moveTo(g.a.x, g.a.y); P.bezierCurveTo(g.c1x, g.c1y, g.c2x, g.c2y, g.b.x, g.b.y);
    }
    return (this.edgeCache = { key, on, off });
  }

  /* ---------- 透明度规则：筛选、课时进度、选中 ---------- */
  alpha(n) {
    const S = VC.S, k = n.k;
    let a = 1;
    if (VC.filterActive() && !VC.passFilter(k)) a = 0.1;
    if (S.lesson != null && k.l > S.lesson) a = Math.min(a, 0.12);
    return a;
  }

  /* ---------- 缩放 ---------- */
  initZoom() {
    this.zoom = d3.zoom().scaleExtent([1, 11]).extent([[0, 0], [this.vw, this.vh]]).translateExtent([[0, 0], [this.vw, this.vh]])
      .on("start", () => this.cv.classList.add("grabbing")).on("end", () => this.cv.classList.remove("grabbing"))
      .on("zoom", (ev) => { this.tr = ev.transform; this.dirty = true; this.kick(); VC.tip.hide(); $("#stage-hint").style.opacity = 0; });
    d3.select(this.cv).call(this.zoom).on("dblclick.zoom", null);
  }
  resetZoom(animate = true) {
    const s = d3.select(this.cv);
    (animate && !VC.reduced ? s.transition().duration(600) : s).call(this.zoom.transform, d3.zoomIdentity);
  }
  zoomTo(x, y, k, ms = 750) {
    const t = d3.zoomIdentity.translate(this.vw / 2, this.vh / 2).scale(k).translate(-x, -y);
    const s = d3.select(this.cv);
    (ms && !VC.reduced ? s.transition().duration(ms).ease(d3.easeCubicInOut) : s).call(this.zoom.transform, t);
  }
  zoomToNode(i, k) { const n = this.nodes[i]; this.zoomTo(n.x, n.y, k || Math.max(this.tr.k, 3.2)); }
  zoomBand(bi) { this.zoomTo(this.pad.l + this.bandW * (bi + 0.5), this.vh / 2, clamp((this.vw * 0.34) / this.bandW, 2, 6)); }

  /* ---------- 事件 ---------- */
  bind() {
    const cv = this.cv;
    const pos = (e) => { const r = cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    cv.addEventListener("pointermove", (e) => {
      if (e.buttons) return;
      const [px, py] = pos(e), n = this.pick(px, py);
      let hdr = null;
      if (!this.compact && py < this.pad.t - 6 && !n) hdr = clamp(Math.floor((((px - this.tr.x) / this.tr.k) - this.pad.l) / this.bandW), 0, 11);
      if (hdr !== this.hdr) { this.hdr = hdr; this.dirty = true; this.kick(); }
      cv.classList.toggle("pointer", !!n || hdr != null);
      const hi = n ? n.i : null;
      if (hi !== this.hoverIdx) { this.hoverIdx = hi; this.chainHover = hi == null ? null : this.localChain(hi); VC.S.hover = hi; this.dirty = true; this.kick(); }
      if (n) this.showTip(n, e); else if (hdr != null) this.showHdr(hdr, e); else VC.tip.hide();
    });
    cv.addEventListener("pointerleave", () => { this.hoverIdx = null; this.chainHover = null; this.hdr = null; this.dirty = true; this.kick(); VC.tip.hide(); });
    cv.addEventListener("click", (e) => {
      const [px, py] = pos(e), n = this.pick(px, py);
      if (n) { this.opts.onSelect && this.opts.onSelect(n.i); return; }
      if (!this.compact && py < this.pad.t - 6) { this.zoomBand(clamp(Math.floor((((px - this.tr.x) / this.tr.k) - this.pad.l) / this.bandW), 0, 11)); return; }
      if (!this.compact) this.opts.onSelect && this.opts.onSelect(null);
    });
    cv.addEventListener("dblclick", (e) => {
      const [px, py] = pos(e), n = this.pick(px, py);
      if (n) { this.opts.onSelect && this.opts.onSelect(n.i); this.opts.onFocus && this.opts.onFocus(n.i); }
      else if (!this.compact) this.resetZoom();
    });
  }
  pick(px, py) {
    const t = this.tr, x = (px - t.x) / t.k, y = (py - t.y) / t.k, sc = Math.pow(t.k, 0.55);
    const rad = (this.compact ? 7 : 11) / t.k + 1;
    let best = null, bd = Infinity;
    this.qt.visit((node, x0, y0, x1, y1) => {
      if (!node.length) { let d = node; do { const n = d.data, dist = Math.hypot(n.x - x, n.y - y) - (n.r * sc) / t.k; if (dist < rad && dist < bd && this.alpha(n) > 0.3) { bd = dist; best = n; } } while ((d = d.next)); }
      return x0 > x + rad + 20 || x1 < x - rad - 20 || y0 > y + rad + 20 || y1 < y - rad - 20;
    });
    return best;
  }
  localChain(i) {
    const D = VC.D, up = new Set(), down = new Set(), ue = [], de = [];
    D.inn[i].forEach((ei) => { ue.push(ei); up.add(D.edges[ei].f); });
    D.out[i].forEach((ei) => { de.push(ei); down.add(D.edges[ei].t); });
    return { up: { nodes: new Map([...up].map((x) => [x, 1])), edges: ue }, down: { nodes: new Map([...down].map((x) => [x, 1])), edges: de } };
  }
  showTip(n, e) {
    const k = n.k, L = VC.kpLoc(k), vts = VC.verifTypes(k).map((t) => VC.VTN[t]).join(" / ") || "—";
    const learned = VC.S.lesson != null ? (k.l <= VC.S.lesson ? "" : `<div class="tm">尚未学习（第 ${k.l + 1} 课时才引入）</div>`) : "";
    VC.tip.show(`<b>${esc(k.n)}</b><div class="tm">${esc(L.sem_name)} · ${esc(L.unit)} · ${esc(L.title)}</div>` +
      `<div>${VC.DOM[k.d].name} · ${VC.MASTERY[k.m] || k.m} · ${k.na} 个题型</div><div class="tm">${vts}</div>` +
      (k.pv ? `<div class="gp">◆ 版本缺口补全：教材未引入，由对齐阶段补上</div>` : "") + learned, e.clientX, e.clientY);
  }
  showHdr(bi, e) {
    const D = VC.D, b = D.books[bi], n = D.kps.filter((k) => k.b === bi).length;
    VC.tip.show(`<b>${VC.semName(bi)}</b><div class="tm">${b.std} 版课标 · 引入 ${n} 个知识点</div><div class="tm">点击放大该学期</div>`, e.clientX, e.clientY);
  }

  /* ---------- 动画循环 ---------- */
  kick() { this.dirty = true; if (!this.running) { this.running = true; raf((t) => this.loop(t)); } }
  needsAnim() {
    if (VC.reduced) return false;
    return !!(this.chainSel || this.chainHover || this.flash.length || VC.S.hits.size || VC.S.sel != null || VC.S.playing);
  }
  loop(now) {
    if (this.dirty || this.needsAnim()) { this.draw(now); this.dirty = false; raf((t) => this.loop(t)); } else this.running = false;
  }
  addFlash(from, to) {
    if (VC.reduced) return;
    const now = performance.now(), D = VC.D;
    for (let l = from + 1; l <= to && this.flash.length < 60; l++) (D.byLesson.get(l) || []).forEach((i) => this.flash.push({ i, t0: now }));
    this.kick();
  }
  setSelection(i) { this.chainSel = i == null ? null : VC.chain(i); this.dirty = true; this.kick(); }

  /* ---------- 绘制 ---------- */
  draw(now = performance.now()) {
    const { ctx, w, h, dpr } = VC.fitCanvas(this.cv);
    const C = VC.C, S = VC.S, t = this.tr, p = this.pad, k = t.k, D = VC.D;
    ctx.clearRect(0, 0, w, h);
    const X = (x) => t.x + k * x, Y = (y) => t.y + k * y, sc = Math.pow(k, 0.55);
    const time = now - this.t0;

    // 学期背景
    for (let b = 0; b < 12; b++) {
      const x0 = X(p.l + this.bandW * b), x1 = X(p.l + this.bandW * (b + 1));
      if (x1 < 0 || x0 > w) continue;
      if (D.books[b].std === 2011) { ctx.fillStyle = C.bandOld; ctx.fillRect(x0, 0, x1 - x0, h); }
      else if (b % 2 === 0) { ctx.fillStyle = C.bandNew; ctx.fillRect(x0, 0, x1 - x0, h); }
      if (this.hdr === b) { ctx.fillStyle = C.bandNew; ctx.fillRect(x0, 0, x1 - x0, h); }
    }
    // 泳道分隔
    ctx.strokeStyle = C.line; ctx.lineWidth = 1;
    this.lanes.forEach((ln, i) => {
      if (i === 0) return;
      const y = Y(ln.top) - (this.compact ? 2 : 5); ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
    });

    // 边：全部淡化，高亮另绘
    const ep = this.edgePaths();
    ctx.save(); ctx.setTransform(dpr * k, 0, 0, dpr * k, dpr * t.x, dpr * t.y);
    if (!this.compact) {
      ctx.lineWidth = 0.9 / k * Math.min(k, 1.6); ctx.strokeStyle = C.edge;
      ctx.globalAlpha = S.sel != null || this.hoverIdx != null ? 0.55 : 1; ctx.stroke(ep.on);
      ctx.globalAlpha = 0.25; ctx.stroke(ep.off); ctx.globalAlpha = 1;
    }
    ctx.restore();

    // 选中/悬停链
    const ch = this.chainSel || this.chainHover;
    const chainSet = new Map();
    if (ch) {
      ch.up.nodes.forEach((d, i) => chainSet.set(i, "up")); ch.down.nodes.forEach((d, i) => chainSet.set(i, "down"));
      const centre = this.chainSel ? S.sel : this.hoverIdx; chainSet.set(centre, "c");
      if (!this.compact) this.drawFlow(ctx, ch, time, dpr, k, t);
    }

    // 节点
    const dimOthers = ch && this.chainSel;
    const order = this.nodes.slice().sort((a, b) => this.nodeRank(a, chainSet) - this.nodeRank(b, chainSet));
    for (const n of order) {
      const x = X(n.x), y = Y(n.y);
      if (x < -20 || x > w + 20 || y < -20 || y > h + 20) continue;
      let a = this.alpha(n);
      if (dimOthers && !chainSet.has(n.i)) a *= 0.28;
      const r = n.r * sc;
      ctx.globalAlpha = a;
      ctx.fillStyle = C.dom[n.k.d];
      ctx.beginPath(); ctx.arc(x, y, r, 0, 6.2832); ctx.fill();
      ctx.lineWidth = 1.2; ctx.strokeStyle = C.bg; ctx.stroke();
      if (n.k.pv && !this.compact) {
        ctx.save(); ctx.strokeStyle = C.gold; ctx.lineWidth = 1.8; ctx.setLineDash([3, 2.6]); ctx.lineDashOffset = -time / 90;
        ctx.beginPath(); ctx.arc(x, y, r + 3.6, 0, 6.2832); ctx.stroke(); ctx.restore();
        ctx.fillStyle = C.gold; ctx.beginPath(); const bx = x + r + 2, by = y - r - 2; ctx.moveTo(bx, by - 3.6); ctx.lineTo(bx + 3.6, by); ctx.lineTo(bx, by + 3.6); ctx.lineTo(bx - 3.6, by); ctx.fill();
      }
      ctx.globalAlpha = 1;
    }

    // 搜索命中：涟漪
    if (S.hits.size) {
      S.hits.forEach((sc2, i) => {
        const n = this.nodes[i], x = X(n.x), y = Y(n.y), ph = ((time / 1400) + i * 0.13) % 1;
        ctx.strokeStyle = C.accent; ctx.lineWidth = 2; ctx.globalAlpha = 0.9;
        ctx.beginPath(); ctx.arc(x, y, n.r * sc + 3, 0, 6.2832); ctx.stroke();
        if (!VC.reduced) { ctx.globalAlpha = 0.6 * (1 - ph); ctx.beginPath(); ctx.arc(x, y, n.r * sc + 3 + ph * 16, 0, 6.2832); ctx.stroke(); }
        ctx.globalAlpha = 1;
      });
    }
    // 闪光：刚引入
    if (this.flash.length) {
      this.flash = this.flash.filter((f) => now - f.t0 < 1100);
      for (const f of this.flash) {
        const n = this.nodes[f.i], q = (now - f.t0) / 1100, x = X(n.x), y = Y(n.y);
        ctx.strokeStyle = C.gold; ctx.lineWidth = 2.4 * (1 - q) + 0.5; ctx.globalAlpha = 1 - q;
        ctx.beginPath(); ctx.arc(x, y, n.r * sc + 2 + q * 20, 0, 6.2832); ctx.stroke(); ctx.globalAlpha = 1;
      }
    }
    // 悬停光晕与选中环
    const ring = (i, kind) => {
      const n = this.nodes[i]; if (!n) return;
      const x = X(n.x), y = Y(n.y), r = n.r * sc;
      if (kind === "hover") {
        ctx.save(); ctx.shadowColor = C.dom[n.k.d]; ctx.shadowBlur = 18; ctx.strokeStyle = C.dom[n.k.d]; ctx.lineWidth = 2.2;
        ctx.beginPath(); ctx.arc(x, y, r + 2.5, 0, 6.2832); ctx.stroke(); ctx.restore();
      } else {
        ctx.strokeStyle = C.gold; ctx.lineWidth = 3; ctx.beginPath(); ctx.arc(x, y, r + 4, 0, 6.2832); ctx.stroke();
        if (!VC.reduced) { const ph = (time / 1600) % 1; ctx.globalAlpha = 0.55 * (1 - ph); ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(x, y, r + 4 + ph * 14, 0, 6.2832); ctx.stroke(); ctx.globalAlpha = 1; }
      }
    };
    if (this.hoverIdx != null && this.hoverIdx !== S.sel) ring(this.hoverIdx, "hover");
    if (S.sel != null) ring(S.sel, "sel");

    if (!this.compact) {
      this.drawLabels(ctx, w, h, X, Y, chainSet);
      this.drawPlayhead(ctx, w, h, X);
      this.drawFrame(ctx, w, h, X, Y);
      this.drawMini();
    } else this.drawPlayhead(ctx, w, h, X), this.drawFrameCompact(ctx, w, h, X, Y);
  }

  nodeRank(n, set) { return set.has(n.i) ? 2 : this.alpha(n) > 0.5 ? 1 : 0; }

  drawFlow(ctx, ch, time, dpr, k, t) {
    const C = VC.C, D = VC.D, tt = time / 1000;
    ctx.save(); ctx.setTransform(dpr * k, 0, 0, dpr * k, dpr * t.x, dpr * t.y);
    const paint = (edges, col, dirSign) => {
      ctx.strokeStyle = col; ctx.lineWidth = 1.7 / k * Math.min(k, 1.8); ctx.globalAlpha = 0.85;
      const P = new Path2D(); const list = [];
      for (const ei of edges) { const g = this.egMap().get(ei); if (!g) continue; P.moveTo(g.a.x, g.a.y); P.bezierCurveTo(g.c1x, g.c1y, g.c2x, g.c2y, g.b.x, g.b.y); list.push(g); }
      ctx.stroke(P); ctx.globalAlpha = 1;
      if (VC.reduced) return;
      ctx.fillStyle = col;
      list.forEach((g, j) => {
        for (let d = 0; d < 2; d++) {
          const u = (tt * 0.45 + j * 0.173 + d * 0.5) % 1, m = 1 - u;
          const x = m * m * m * g.a.x + 3 * m * m * u * g.c1x + 3 * m * u * u * g.c2x + u * u * u * g.b.x;
          const y = m * m * m * g.a.y + 3 * m * m * u * g.c1y + 3 * m * u * u * g.c2y + u * u * u * g.b.y;
          ctx.beginPath(); ctx.arc(x, y, 2.6 / Math.pow(k, 0.7), 0, 6.2832); ctx.fill();
        }
      });
    };
    paint(ch.up.edges, C.up); paint(ch.down.edges, C.down);
    ctx.restore();
  }
  egMap() { if (!this._egm || this._egm.src !== this.eg) { this._egm = new Map(this.eg.map((g) => [g.e.i, g])); this._egm.src = this.eg; } return this._egm; }

  drawLabels(ctx, w, h, X, Y, chainSet) {
    const k = this.tr.k, sc = Math.pow(k, 0.55), C = VC.C, S = VC.S, boxes = [];
    ctx.font = "11.5px " + getComputedStyle(document.body).fontFamily; ctx.textBaseline = "middle"; ctx.lineJoin = "round";
    const want = (n) => n.i === S.sel || n.i === this.hoverIdx || chainSet.has(n.i);
    const list = this.nodes.filter((n) => (k >= 2.1 && this.alpha(n) > 0.5) || (want(n) && this.alpha(n) > 0.1)).sort((a, b) => (want(b) ? 1 : 0) - (want(a) ? 1 : 0) || b.r - a.r);
    let drawn = 0;
    for (const n of list) {
      const x = X(n.x), y = Y(n.y);
      if (x < 0 || x > w || y < 40 || y > h) continue;
      if (S.sel != null && !chainSet.has(n.i) && !want(n)) continue;
      const maxc = want(n) ? 16 : k > 4 ? 14 : 8;
      let txt = n.k.n.length > maxc ? n.k.n.slice(0, maxc) + "…" : n.k.n;
      const tw = ctx.measureText(txt).width, bx = x + n.r * sc + 4, by = y - 7;
      if (!want(n) && boxes.some((b) => bx < b[2] && bx + tw > b[0] && by < b[3] && by + 14 > b[1])) continue;
      boxes.push([bx, by, bx + tw, by + 14]);
      ctx.lineWidth = 3.2; ctx.strokeStyle = C.bg; ctx.strokeText(txt, bx, y);
      ctx.fillStyle = want(n) ? C.ink : C.ink2; ctx.fillText(txt, bx, y);
      if (++drawn > 160) break;
    }
  }

  drawPlayhead(ctx, w, h, X) {
    const S = VC.S, D = VC.D;
    if (S.lesson == null) return;
    const b = D.lb[S.lesson], bl = D.bookLessons[b], frac = (S.lesson - bl[0]) / Math.max(1, bl[1] - bl[0]);
    const x = X(this.pad.l + this.bandW * (b + 0.1 + 0.8 * frac)), C = VC.C;
    const g = ctx.createLinearGradient(x - 26, 0, x, 0); g.addColorStop(0, "rgba(217,154,0,0)"); g.addColorStop(1, "rgba(217,154,0,.16)");
    ctx.fillStyle = g; ctx.fillRect(x - 26, 0, 26, h);
    ctx.strokeStyle = C.gold; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(x, this.compact ? 12 : 40); ctx.lineTo(x, h - (this.compact ? 0 : 0)); ctx.stroke();
    ctx.fillStyle = C.gold; ctx.beginPath(); const y0 = this.compact ? 12 : 40; ctx.moveTo(x - 5, y0 - 7); ctx.lineTo(x + 5, y0 - 7); ctx.lineTo(x, y0); ctx.fill();
  }

  drawFrame(ctx, w, h, X, Y) {
    const C = VC.C, D = VC.D, p = this.pad, ff = getComputedStyle(document.body).fontFamily;
    // 顶部学期表头
    ctx.fillStyle = C.bg; ctx.fillRect(0, 0, w, p.t - 8);
    ctx.strokeStyle = C.line; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(0, p.t - 8.5); ctx.lineTo(w, p.t - 8.5); ctx.stroke();
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    for (let b = 0; b < 12; b++) {
      const x0 = X(p.l + this.bandW * b), x1 = X(p.l + this.bandW * (b + 1)); if (x1 < p.l - 4 || x0 > w) continue;
      const cx = (Math.max(x0, p.l) + Math.min(x1, w)) / 2, old = D.books[b].std === 2011;
      ctx.fillStyle = this.hdr === b ? C.ink : C.ink2; ctx.font = `700 14px ${ff}`; ctx.fillText(VC.semShort(b), cx, 15);
      ctx.font = `11px ${ff}`; ctx.fillStyle = old ? C.gold : C.ink3; ctx.fillText(old ? "旧版 2011" : "2022 课标", cx, 32);
      if (old) { ctx.fillStyle = C.gold; ctx.fillRect(Math.max(x0, p.l) + 4, p.t - 12, Math.min(x1, w) - Math.max(x0, p.l) - 8, 2.5); }
      ctx.strokeStyle = C.line; ctx.beginPath(); ctx.moveTo(x0, 6); ctx.lineTo(x0, p.t - 8); ctx.stroke();
    }
    // 左侧泳道标签
    ctx.fillStyle = C.bg; ctx.fillRect(0, p.t - 8, p.l - 6, h);
    ctx.strokeStyle = C.line; ctx.beginPath(); ctx.moveTo(p.l - 5.5, p.t - 8); ctx.lineTo(p.l - 5.5, h); ctx.stroke();
    ctx.textAlign = "left";
    this.lanes.forEach((ln) => {
      const y = Y(ln.mid), top = Y(ln.top), bot = Y(ln.top + ln.h);
      if (bot < p.t || top > h) return;
      const yy = clamp(y, p.t + 14, h - 14);
      ctx.fillStyle = C.dom[ln.d]; ctx.fillRect(10, clamp(top + 4, p.t, h), 4, Math.max(8, Math.min(bot, h) - Math.max(top, p.t) - 8));
      ctx.font = `700 13px ${ff}`; ctx.fillText(VC.DOM[ln.d].name.slice(0, 2), 22, yy - 8);
      ctx.font = `12px ${ff}`; ctx.fillStyle = C.ink3; ctx.fillText(VC.DOM[ln.d].name.slice(2), 22, yy + 8);
    });
    ctx.fillStyle = C.bg; ctx.fillRect(0, 0, p.l - 6, p.t - 8);
    ctx.fillStyle = C.ink3; ctx.font = `11px ${ff}`; ctx.textAlign = "left"; ctx.fillText("学期 →", 14, 24);
    ctx.textAlign = "start";
  }
  drawFrameCompact(ctx, w, h, X, Y) {
    const C = VC.C, D = VC.D, p = this.pad, ff = getComputedStyle(document.body).fontFamily;
    ctx.textAlign = "center"; ctx.font = `600 11px ${ff}`;
    for (let b = 0; b < 12; b++) {
      const x0 = p.l + this.bandW * b; ctx.fillStyle = D.books[b].std === 2011 ? C.gold : C.ink3; ctx.fillText(VC.semShort(b), x0 + this.bandW / 2, 10);
    }
    ctx.textAlign = "start";
  }
  drawMini() {
    const mc = $("#minimap"); if (!mc || !this.zoom) return;
    const { ctx, w, h } = VC.fitCanvas(mc), C = VC.C, p = this.pad, sx = w / this.vw, sy = h / this.vh;
    ctx.clearRect(0, 0, w, h);
    for (let b = 0; b < 12; b++) if (VC.D.books[b].std === 2011) { ctx.fillStyle = C.bandOld; ctx.fillRect((p.l + this.bandW * b) * sx, 0, this.bandW * sx, h); }
    for (const n of this.nodes) { ctx.globalAlpha = this.alpha(n) > 0.5 ? 1 : 0.2; ctx.fillStyle = C.dom[n.k.d]; ctx.fillRect(n.x * sx - 0.9, n.y * sy - 0.9, 1.9, 1.9); }
    ctx.globalAlpha = 1;
    const t = this.tr; ctx.strokeStyle = C.gold; ctx.lineWidth = 1.6;
    ctx.strokeRect((-t.x / t.k) * sx, (-t.y / t.k) * sy, (this.vw / t.k) * sx, (this.vh / t.k) * sy);
  }

  layoutDeferred() { clearTimeout(this._ld); this._ld = setTimeout(() => this.relayout(), 80); }
  relayout() {
    const keep = this.tr;
    this.layout();
    if (this.zoom) { this.zoom.extent([[0, 0], [this.vw, this.vh]]).translateExtent([[0, 0], [this.vw, this.vh]]); }
    this.edgeCache = null; this.dirty = true; this.kick();
  }
};

VC.initMinimapEvents = function (pan) {
  const mc = $("#minimap");
  const go = (e) => {
    const r = mc.getBoundingClientRect(), x = ((e.clientX - r.left) / r.width) * pan.vw, y = ((e.clientY - r.top) / r.height) * pan.vh;
    const t = pan.tr, nt = d3.zoomIdentity.translate(pan.vw / 2, pan.vh / 2).scale(t.k).translate(-x, -y);
    d3.select(pan.cv).call(pan.zoom.transform, nt);
  };
  mc.addEventListener("pointerdown", (e) => { go(e); mc.setPointerCapture(e.pointerId); mc.onpointermove = go; });
  mc.addEventListener("pointerup", () => (mc.onpointermove = null));
};
