/* 首屏：粒子网络背景、滚动计数、流水线、现场实例化卡片 */
VC.ICONS = {
  textbook: '<path d="M4 5a2 2 0 012-2h12v15H6a2 2 0 00-2 2V5zM4 20a2 2 0 012-2h12"/>',
  extract: '<path d="M4 6h16M7 12h10M10 18h4"/>',
  resolve: '<circle cx="9" cy="12" r="5"/><circle cx="15" cy="12" r="5"/>',
  archetype: '<rect x="4" y="4" width="7" height="7" rx="1.6"/><rect x="13" y="4" width="7" height="7" rx="1.6"/><rect x="4" y="13" width="7" height="7" rx="1.6"/><rect x="13" y="13" width="7" height="7" rx="1.6"/>',
  relation: '<circle cx="6" cy="6" r="2.4"/><circle cx="18" cy="8" r="2.4"/><circle cx="12" cy="18" r="2.4"/><path d="M8 6.6l8 1M7 8l4 8M17 10l-4 6"/>',
  reconcile: '<path d="M5 8h13l-3-3M19 16H6l3 3"/>',
  boundary: '<path d="M4 20h4v-4h4v-4h4V8h4"/>',
  query: '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5"/>',
};

VC.initHero = function () {
  const D = VC.D, c = D.meta.counts;
  const st = D.stats;
  const nProg = D.kps.reduce((a, k) => a + k.vt[0], 0);
  const counters = [
    { n: c.books, l: "册教材", s: "2022 课标 9 册 + 2011 课标 3 册" },
    { n: c.lessons, l: "个课时", s: "逐课时定位" },
    { n: c.kps, l: "个知识点", s: "含 14 个版本缺口补全" },
    { n: c.archetypes, l: "个题型卡片", s: nProg + " 个程序可验证" },
    { n: c.exercises, l: "道教材习题", s: "归纳为题型模板" },
    { n: D.edges.filter((e) => e.k === 0).length, l: "条前置关系", s: c.direct_prerequisite + " 条直接 · 其余传递约简" },
    { n: c.standard_items, l: "条课标要求", s: "100% 有对应知识点" },
  ];
  const box = $("#counters");
  counters.forEach((o) => box.append(el("div", { class: "counter", role: "listitem" }, [
    el("div", { class: "n", "data-to": o.n, text: "0" }), el("div", { class: "l", text: o.l }), el("div", { class: "s", text: o.s })])));
  const run = () => $$(".counter .n").forEach((n, i) => {
    const to = +n.dataset.to;
    setTimeout(() => tween(1500, (p) => (n.textContent = Math.round(to * p).toLocaleString("en-US")), easeOut), 120 * i);
  });
  VC.heroCount = () => (VC.reduced ? $$(".counter .n").forEach((n) => (n.textContent = (+n.dataset.to).toLocaleString("en-US"))) : run());

  // 流水线
  const pl = $("#pipeline");
  pl.append(el("div", { class: "flow" }));
  const icons = ["textbook", "extract", "resolve", "archetype", "relation", "reconcile", "boundary", "query"];
  D.eval.pipeline.forEach((s, i) => {
    const e = el("div", { class: "step", tabindex: 0 }, [
      el("div", { class: "ic", html: `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#f3efe4" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${VC.ICONS[icons[i]]}</svg>` }),
      el("div", { class: "nm", text: s.name }), el("div", { class: "sub", text: s.sub }), el("div", { class: "bd", text: s.badge }),
      el("div", { class: "note", text: s.note }),
    ]);
    pl.append(e);
  });
  if (!VC.reduced) { // 流光依次点亮各步骤
    let k = 0; const steps = $$(".step");
    setInterval(() => { steps.forEach((s, i) => s.classList.toggle("on", i === k)); k = (k + 1) % steps.length; }, 1100);
  }
  VC.heroBg();
  $(".brand").title = `数据哈希 ${D.meta.sha.slice(0, 12)} · 评测记录 ${D.meta.eval_ts.slice(0, 10)}`;
};

/* ---------- 背景：缓动的网络 ---------- */
VC.heroBg = function () {
  const cv = $("#hero-bg"), hero = $("#p-overview");
  const cols = ["#5fb3ef", "#f59a52", "#3fcf9f", "#e69bcf"];
  let W = 0, H = 0, P = [], vis = true, mx = -999, my = -999, id = 0;
  const rnd = (() => { let s = 7; return () => ((s = (s * 16807) % 2147483647) / 2147483647); })();
  function resize() {
    const r = cv.getBoundingClientRect(); const dpr = Math.min(devicePixelRatio || 1, 2);
    W = r.width; H = r.height; cv.width = W * dpr; cv.height = H * dpr; cv.getContext("2d").setTransform(dpr, 0, 0, dpr, 0, 0);
    const n = Math.round(clamp((W * H) / 16000, 40, 110));
    P = Array.from({ length: n }, (_, i) => ({ x: rnd() * W, y: rnd() * H, vx: (rnd() - 0.5) * 0.22, vy: (rnd() - 0.5) * 0.22, r: 1.4 + rnd() * 2.6, c: cols[i % 4] }));
  }
  function frame() {
    id = 0; if (!vis) return;
    const ctx = cv.getContext("2d"); ctx.clearRect(0, 0, W, H);
    for (const p of P) {
      if (!VC.reduced) {
        p.x += p.vx; p.y += p.vy;
        if (p.x < -20) p.x = W + 20; if (p.x > W + 20) p.x = -20; if (p.y < -20) p.y = H + 20; if (p.y > H + 20) p.y = -20;
        const dx = p.x - mx, dy = p.y - my, d2 = dx * dx + dy * dy;
        if (d2 < 14000) { const f = (1 - d2 / 14000) * 0.9; p.x += (dx / Math.sqrt(d2 + 1)) * f; p.y += (dy / Math.sqrt(d2 + 1)) * f; }
      }
    }
    ctx.lineWidth = 1;
    for (let i = 0; i < P.length; i++) for (let j = i + 1; j < P.length; j++) {
      const dx = P[i].x - P[j].x, dy = P[i].y - P[j].y, d = dx * dx + dy * dy;
      if (d < 18000) { ctx.strokeStyle = `rgba(243,239,228,${0.16 * (1 - d / 18000)})`; ctx.beginPath(); ctx.moveTo(P[i].x, P[i].y); ctx.lineTo(P[j].x, P[j].y); ctx.stroke(); }
    }
    for (const p of P) { ctx.fillStyle = p.c; ctx.globalAlpha = 0.55; ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, 6.283); ctx.fill(); }
    ctx.globalAlpha = 1;
    if (!VC.reduced) id = raf(frame);
  }
  window.addEventListener("resize", debounce(() => { resize(); if (VC.reduced) frame(); }, 200));
  hero.addEventListener("pointermove", (e) => { const r = cv.getBoundingClientRect(); mx = e.clientX - r.left; my = e.clientY - r.top; });
  hero.addEventListener("pointerleave", () => { mx = my = -999; });
  new IntersectionObserver((es) => { vis = es[0].isIntersecting; if (vis) { resize(); if (id) cancelAnimationFrame(id); frame(); } }).observe(hero);
};

/* ---------- 现场实例化卡片 ---------- */
VC.initLive = async function () {
  const A = await VC.getArch(), D = VC.D;
  const pool = [];
  for (const g of [1, 2, 3, 4, 5, 6]) {
    const cand = [];
    D.kps.forEach((k) => {
      if (k.grade !== g || !k.na) return;
      (A[k.i] || []).forEach((a) => {
        if (a.v !== 0 || !a.sm || a.sm.length < 3 || a.d > 4) return;
        const q = a.sm[0][0];
        if (q.length < 14 || q.length > 64 || !/\d/.test(q) || /[|□_{}]/.test(q)) return;
        cand.push({ k, a, score: (a.sm[0][0].length % 7) + (k.pv ? -9 : 0) + (a.ni ? 3 : 0) });
      });
    });
    cand.sort((x, y) => y.score - x.score || x.a.i.localeCompare(y.a.i));
    cand.slice(0, 2).forEach((c) => pool.push(c));
  }
  if (!pool.length) { $("#live-card").hidden = true; return; }
  let i = 0, seed = 0, timer = null, typer = null;
  const q = $("#live-q"), a = $("#live-a"), foot = $("#live-foot"), kp = $("#live-kp");
  function show() {
    const { k, a: ar } = pool[i % pool.length];
    const s = ar.sm[seed % ar.sm.length];
    kp.textContent = `${VC.semName(k.b)} · ${k.n}`;
    a.classList.remove("show"); a.innerHTML = "";
    foot.innerHTML = "";
    clearInterval(typer);
    const text = s[0]; let n = 0;
    q.textContent = "";
    if (VC.reduced) q.textContent = text;
    else typer = setInterval(() => { n += 2; q.textContent = text.slice(0, n); if (n >= text.length) clearInterval(typer); }, 22);
    const t = VC.reduced ? 0 : Math.min(900 + text.length * 11, 2000);
    setTimeout(() => {
      a.innerHTML = `答案 <b>${esc(s[1])}</b>`; a.classList.add("show");
      foot.append(el("span", { class: "pill ok", text: "✓ 程序已验证" }), el("span", { class: "pill", text: "难度 " + "★".repeat(ar.d) }),
        el("span", { class: "pill", text: VC.VTN.program }));
    }, t);
  }
  const next = () => { i++; if (i % pool.length === 0) seed++; show(); };
  show();
  if (!VC.reduced) timer = setInterval(next, 6500);
  $("#live-card").addEventListener("click", () => { clearInterval(timer); next(); timer = setInterval(next, 6500); });
  $("#live-card").style.cursor = "pointer"; $("#live-card").title = "点击换一题";
};
