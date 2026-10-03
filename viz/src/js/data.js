/* 数据解包与派生索引 */
async function unpack(b64) {
  const bin = atob(b64.trim());
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  return JSON.parse(await new Response(stream).text());
}

VC.load = async function () {
  const D = (VC.D = await unpack($("#d-core").textContent));
  VC.archP = null;
  const A = $("#d-arch").textContent;
  VC.getArch = () => (VC.archP = VC.archP || unpack(A).then((a) => (VC.ARCH = a.a, VC.ARCH_META = a, a.a)));

  D.kps.forEach((k, i) => (k.i = i));
  D.kpById = new Map(D.kps.map((k) => [k.id, k]));
  D.lb = D.lu.map((u) => D.units[u].b);              // 课时 → 书序号
  D.bookLessons = D.books.map(() => [Infinity, -1]);
  D.lb.forEach((b, i) => { const r = D.bookLessons[b]; r[0] = Math.min(r[0], i); r[1] = Math.max(r[1], i); });
  D.kps.forEach((k) => { k.b = D.lb[k.l]; k.grade = D.books[k.b].g; k.sem = D.books[k.b].s; k.std = D.books[k.b].std; });

  // 邻接：全部边、直接前置边
  D.adj = D.kps.map(() => []);
  D.out = D.kps.map(() => []);
  D.inn = D.kps.map(() => []);
  D.edges.forEach((e, i) => {
    e.i = i;
    D.adj[e.f].push(i); D.adj[e.t].push(i);
    if (e.k === 0 && e.d === 1) { D.out[e.f].push(i); D.inn[e.t].push(i); }
  });
  D.direct = D.edges.filter((e) => e.k === 0 && e.d === 1);

  // 每个课时新引入的知识点、已学累计
  D.byLesson = new Map();
  D.kps.forEach((k) => { (D.byLesson.get(k.l) || D.byLesson.set(k.l, []).get(k.l)).push(k.i); });
  D.learnedCum = new Array(D.lessons.length);
  let c = 0;
  for (let i = 0; i < D.lessons.length; i++) { c += (D.byLesson.get(i) || []).length; D.learnedCum[i] = c; }

  // 累计概念数
  D.cc = []; let cc = 0;
  for (let i = 0; i < D.lessons.length; i++) { cc += (D.tl.add[i] && D.tl.add[i].nc) || 0; D.cc.push(cc); }
  return D;
};

const cnGrade = (g) => CN[g] + "年级";
VC.semName = (bi) => { const b = VC.D.books[bi]; return cnGrade(b.g) + (b.s === "a" ? "上册" : "下册"); };
VC.semShort = (bi) => { const b = VC.D.books[bi]; return CN[b.g] + (b.s === "a" ? "上" : "下"); };

/** 课时定位 */
VC.loc = function (li) {
  const D = VC.D, u = D.units[D.lu[li]], b = D.books[u.b];
  const nth = li - u.l0 + 1;
  return {
    li, id: D.lessons[li][0], title: D.lessons[li][1], unit: u.t, unitId: u.id, bi: u.b, book: b.id, grade: b.g, sem: b.s, std: b.std,
    nth, sem_name: VC.semName(u.b),
    label: `${VC.semName(u.b)} · ${u.t} · ${D.lessons[li][1]}`,
    short: `${VC.semName(u.b)}${u.t.replace(/\s+/g, " ")}第${nth}课`,
  };
};
VC.kpLoc = (k) => VC.loc(k.l);
VC.lessonIdOf = (li) => VC.D.lessons[li][0];

VC.verifTypes = (k) => k.vt.map((n, i) => (n ? VC.VT[i] : null)).filter(Boolean);

/* ---------- 全局状态与过滤 ---------- */
VC.S = {
  view: "pan", sel: null, hover: null, lesson: null, playing: false,
  grade: new Set(), domain: new Set(), vt: new Set(), gap: false, std: null,
  depth: 2, show: { implied: false, builds_on: false, related: false, confusable: false, extends: false },
  size: "na", hits: new Map(), dim: true,
};

/** 知识点是否通过当前筛选 */
VC.passFilter = function (k) {
  const S = VC.S;
  if (S.grade.size && !S.grade.has(k.grade)) return false;
  if (S.domain.size && !S.domain.has(k.d)) return false;
  if (S.vt.size && !VC.verifTypes(k).some((t) => S.vt.has(t))) return false;
  if (S.gap && !k.pv) return false;
  if (S.std && k.std !== S.std) return false;
  return true;
};
VC.filterActive = () => { const S = VC.S; return S.grade.size || S.domain.size || S.vt.size || S.gap || S.std; };
