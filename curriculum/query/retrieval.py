"""知识点检索：基线（TF-IDF）与改进版（需求解析 + BM25F + 年级/领域先验；改进记录见 eval/specs/stage7.md）。"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

if TYPE_CHECKING:
    from curriculum.query.store import Curriculum


class TfidfBaseline:
    """基线：名称 + 别名 + 描述拼接，字符 1～2 gram TF-IDF，余弦相似度；不做任何需求预处理。"""

    def __init__(self, cur: "Curriculum"):
        self.ids = list(cur.knowledge_points)
        docs = [" ".join([k.name, *k.aliases, k.description]) for k in (cur.knowledge_points[i] for i in self.ids)]
        self.vec = TfidfVectorizer(analyzer="char", ngram_range=(1, 2), sublinear_tf=True)
        self.mat = self.vec.fit_transform(docs)

    def rank(self, query: str, k: int = 50) -> list[tuple[str, float]]:
        q = self.vec.transform([query])
        sims = (self.mat @ q.T).toarray().ravel()
        order = np.argsort(-sims, kind="stable")[:k]
        return [(self.ids[i], float(sims[i])) for i in order]


# ====================================================================== 需求解析

CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6}
GRADE_RE = re.compile(r"(?:小学)?([一二三四五六1-6])\s*年级")
SEM_RE = re.compile(r"(上学期|上册|第一学期|上半学期|下学期|下册|第二学期|下半学期)")
DOMAIN_STRONG = {"数与代数": "na", "数与运算": "na", "图形与几何": "gg", "统计与概率": "sp", "综合与实践": "ip"}
SCOPE_RE = re.compile(r"总复习|期末|期中|综合卷|综合试卷|整张卷子|一张卷子|所有考点|全部考点|这学期|本学期|串起来")
# 教师需求里与知识点内容无关的套话（出题意图、情境/难度修饰）；按长度降序剔除
FILLER = [
    "我想要", "我想", "想要", "能不能", "可以", "出几道", "出一套", "出一道", "出一组", "出几", "出点", "出题", "几道", "一套", "一组", "一道", "一份",
    "最好", "那种", "这种", "之类的", "之类", "类似的", "新题", "的题目", "的题", "题目", "习题", "有点难度", "拔高", "基础", "难度", "由浅入深",
    "梯度递增", "从基础到", "贴近", "孩子", "日常", "生活", "情境", "场景", "换几种", "不同的", "每道题", "别老是", "学生", "老师", "需要", "要有",
    "要能", "都要", "覆盖", "来几道", "来一道", "考一考", "放在一起", "结合起来", "结合", "综合起来", "综合题",
    # 范围/综合类需求的范围词：表达"考多大范围"，不是知识点内容
    "总复习阶段", "总复习", "阶段", "期末", "期中", "考试范围", "综合卷", "综合试卷", "整张卷子", "一张卷子", "所有考点", "全部考点", "这学期", "本学期",
    "串起来", "揉在", "复习一下", "复习", "把之前", "把前面", "把以前", "学的", "题把", "时把", "也", "回顾", "串一遍", "带进来", "穿插进来", "穿插", "带上", "以前学的", "之前学的", "前面学的", "学过的", "课前热身", "热身", "衔接", "各出一套", "各来几道", "各出", "小升初衔接",
]
FILLER.sort(key=len, reverse=True)


# 螺旋复习类需求：子句的角色决定它在检索里的权重。
# OBJ：复习本身就是目的（「想复习一下二年级学过的…」）-> 该子句权重 1，其余 0.4；
# MIX：把以前学的内容穿插进当前主题（「把之前学的乘法竖式也带进来」）-> 该子句权重 0.45（当前主题为主，复习内容为辅）。
OBJ_STRONG_RE = re.compile(r"先复习|想复习|想要复习|帮.{0,6}复习|课前热身|热身")
MIX_RE = re.compile(r"也串|串一遍|带进来|带上|穿插|再串|衔接|把之前|把前面|把以前|以前学的|之前学的|前面学的|学过的")
OBJ_WEAK_RE = re.compile(r"复习一下")


@dataclass
class ParsedQuery:
    raw: str
    grade: Optional[int] = None  # 第一个出现的年级（当前教学年级）
    grades: list[int] = field(default_factory=list)  # 需求里提到的全部年级（复习类需求会同时提到当前年级与以前年级）
    semester: Optional[str] = None  # a / b
    domains: list[str] = field(default_factory=list)
    scope: bool = False  # 跨单元/总复习/综合卷类需求
    clauses: list[str] = field(default_factory=list)  # 去掉年级/学期/领域/套话后的子句（保序）
    weights: list[float] = field(default_factory=list)  # 与 clauses 对齐的子句权重（螺旋复习类需求不为 1）

    @property
    def has_content(self) -> bool:
        """去掉年级/学期/领域/套话/范围词后是否还剩知识点内容（至少两个汉字或一个数字/字母词）。"""
        return any(re.search(r"[一-鿿]{2,}|[A-Za-z0-9]", c) for c in self.clauses)


def parse_query(q: str) -> ParsedQuery:
    """解析教师口吻需求：年级、学期、领域、是否综合/总复习类；剩余文本按子句切分并剔除套话。"""
    pq = ParsedQuery(raw=q)
    text = q
    for m in GRADE_RE.finditer(text):
        g = CN_NUM[m.group(1)]
        if g not in pq.grades:
            pq.grades.append(g)
    if pq.grades:
        pq.grade = pq.grades[0]
        text = GRADE_RE.sub(" ", text)
    m = SEM_RE.search(text)
    if m:
        pq.semester = "a" if m.group(1).startswith(("上", "第一")) else "b"
        text = text[: m.start()] + " " + text[m.end():]
    for w, d in DOMAIN_STRONG.items():
        if w in text:
            pq.domains.append(d)
            text = text.replace(w, " ")
    pq.scope = bool(SCOPE_RE.search(q))
    raw_clauses = [c for c in re.split(r"[，,。；;！!？?\n]+", text) if c.strip()]
    roles = ["obj" if OBJ_STRONG_RE.search(c) else "mix" if MIX_RE.search(c) else "obj" if OBJ_WEAK_RE.search(c) else "cur" for c in raw_clauses]
    out, ws = [], []
    for c, role in zip(raw_clauses, roles):
        for f in FILLER:
            c = c.replace(f, " ")
        c = c.strip()
        if not c:
            continue
        if "obj" in roles:
            w = 1.0 if role == "obj" else 0.4
        elif "mix" in roles and "cur" in roles:
            w = 0.45 if role == "mix" else 1.0
        else:
            w = 1.0
        out.append(c)
        ws.append(w)
    pq.clauses, pq.weights = out, ws
    return pq


_RUN = re.compile(r"[一-鿿]+|[A-Za-z]+|\d+(?:\.\d+)?")


def tokens(text: str, unigram: bool = False, trigram: bool = False) -> list[str]:
    """汉字连续段取字符二元组（可加单字、三元组；单字段保留单字），数字/字母段取整词。"""
    out = []
    for run in _RUN.findall(text):
        if "一" <= run[0] <= "鿿":
            if len(run) == 1:
                out.append(run)
            else:
                out += [run[i : i + 2] for i in range(len(run) - 1)]
                if unigram:
                    out += list(run)
                if trigram:
                    out += [run[i : i + 3] for i in range(len(run) - 2)]
        else:
            out.append(run.lower())
    return out


@dataclass
class SearchHit:
    kp_id: str
    score: float
    name: str
    domain: str
    grade: int
    semester: str
    unit_title: str
    lesson_id: str
    lesson_title: str


CFG = dict(
    field_w={"name": 3.0, "alias": 3.0, "thread": 1.5, "desc": 1.0, "lesson": 1.0},
    k1=1.4,
    b=0.6,
    clause_w=(1.0, 1.0),
    grade_prior=True,
    domain_boost=1.3,
    later_penalty=0.6,  # 知识点引入年级晚于需求年级：每晚一年乘该系数（D14：教师说法与教材编排可能不一致，故为软先验）
    earlier_penalty=0.8,  # 最后复现年级早于需求年级：每早一年乘该系数（复习需求仍需要这些知识点）
    scope=True,  # 综合/总复习类需求：仅范围词时按年级/领域取知识点，并按主线分散
    spiral=True,  # 螺旋复习类需求：按子句角色（复习为目的/穿插复习/当前主题）加权
    unigram=False,
    trigram=False,
    preprocess=True,
    dense_w=0.5,  # >0 时与词法分数线性融合（需要向量缓存或 DASHSCOPE_API_KEY，否则自动降级为纯词法）
    dense_query="clean",  # clean：去掉年级/学期/领域/套话后的需求文本；raw：原文
)


class Searcher:
    def __init__(self, cur: "Curriculum", cfg: Optional[dict] = None):
        self.cur = cur
        self.cfg = {**CFG, **(cfg or {})}
        self.ids = list(cur.knowledge_points)
        self._locs = [cur.locate(i) for i in self.ids]
        self._index()

    # ---- 索引
    def _fields(self, kp, loc) -> dict[str, str]:
        return {
            "name": kp.name,
            "alias": " ".join(kp.aliases),
            "thread": f"{kp.thread} {kp.topic}",
            "desc": kp.description,
            "lesson": f"{loc.lesson_title} {loc.unit_title}",
        }

    def _index(self):
        c = self.cfg
        names = list(c["field_w"])
        raw = []
        flen = {f: [] for f in names}
        for i, loc in zip(self.ids, self._locs):
            fs = self._fields(self.cur.kp(i), loc)
            toks = {f: tokens(fs[f], c["unigram"], c["trigram"]) for f in names}
            raw.append(toks)
            for f in names:
                flen[f].append(len(toks[f]))
        avg = {f: max(1e-9, sum(v) / len(v)) for f, v in flen.items()}
        self.inv: dict[str, list[tuple[int, float]]] = defaultdict(list)
        df = Counter()
        for di, toks in enumerate(raw):
            wtf = Counter()
            for f in names:
                if not toks[f]:
                    continue
                norm = 1 - c["b"] + c["b"] * len(toks[f]) / avg[f]
                for t, n in Counter(toks[f]).items():
                    wtf[t] += c["field_w"][f] * n / norm
            for t, v in wtf.items():
                self.inv[t].append((di, v))
                df[t] += 1
        N = len(raw)
        self.idf = {t: math.log(1 + (N - n + 0.5) / (n + 0.5)) for t, n in df.items()}
        # 年级先验所需：引入年级与最后复现年级
        self._grade_span = []
        for kid, loc in zip(self.ids, self._locs):
            kp = self.cur.kp(kid)
            gr = max([loc.grade] + [int(l.split(".")[0][1]) for l in kp.review_lesson_ids if l in self.cur.lessons])
            self._grade_span.append((loc.grade, gr))

    # ---- 打分
    def lexical(self, pq: ParsedQuery) -> np.ndarray:
        c = self.cfg
        scores = np.zeros(len(self.ids))
        clauses = pq.clauses if c["preprocess"] and pq.clauses else [pq.raw]
        for ci, cl in enumerate(clauses):
            w = c["clause_w"][min(ci, len(c["clause_w"]) - 1)] if c["preprocess"] else 1.0
            if c["spiral"] and c["preprocess"] and pq.weights:
                w *= pq.weights[ci]
            for t in Counter(tokens(cl, c["unigram"], c["trigram"])):
                if t not in self.inv:
                    continue
                idf = self.idf[t]
                for di, v in self.inv[t]:
                    scores[di] += w * idf * (v * (c["k1"] + 1)) / (v + c["k1"])
        return scores

    def prior(self, pq: ParsedQuery) -> np.ndarray:
        f = np.ones(len(self.ids))
        c = self.cfg
        if pq.domains:
            f *= np.array([c["domain_boost"] if self.cur.kp(i).domain.value in pq.domains else 1.0 for i in self.ids])
        if pq.grade is None or not c["grade_prior"]:
            return f
        best = np.zeros(len(self.ids))
        for gi_, g in enumerate(pq.grades or [pq.grade]):
            for i, (loc, (gi, gr)) in enumerate(zip(self._locs, self._grade_span)):
                if gi <= g <= gr:
                    p = 1.2 if gi == g else 1.0
                    if gi == g and gi_ == 0 and pq.semester and loc.semester == pq.semester:
                        p *= 1.1
                elif gi > g:
                    p = c["later_penalty"] ** (gi - g)
                else:
                    p = c["earlier_penalty"] ** (g - gr)
                best[i] = max(best[i], p)  # 满足任一被提到的年级即可（如「三年级复习二年级学过的…」）
        return f * best

    # ---- 向量（可选）
    def _kp_matrix(self):
        if getattr(self, "_dense_mat", None) is None:
            from curriculum.query.embed import embed

            texts = [self._dense_text(i) for i in self.ids]
            self._dense_mat = embed(texts)
        return self._dense_mat

    def _dense_text(self, kid: str) -> str:
        kp = self.cur.kp(kid)
        return f"{kp.name}。{'、'.join(kp.aliases)}。{kp.description}"

    def dense(self, pq: ParsedQuery) -> Optional[np.ndarray]:
        from curriculum.query.embed import EmbeddingUnavailable, embed

        try:
            mat = self._kp_matrix()
            if self.cfg["spiral"] and pq.clauses and any(w != 1.0 for w in pq.weights):
                qs = embed(pq.clauses)
                w = np.array(pq.weights)
                return mat @ ((qs * w[:, None]).sum(0) / w.sum())
            q = pq.raw if self.cfg["dense_query"] == "raw" else " ".join(pq.clauses) or pq.raw
            return mat @ embed([q])[0]
        except EmbeddingUnavailable as e:
            if not getattr(self, "_warned", False):
                import warnings

                warnings.warn(f"向量检索不可用，降级为词法检索：{e}")
                self._warned = True
            return None

    def final_scores(self, pq: ParsedQuery) -> np.ndarray:
        c = self.cfg
        lex = self.lexical(pq)
        if c["dense_w"] > 0:
            d = self.dense(pq)
            if d is not None:
                # 余弦相似度减去全体均值后按最大值归一（≥0），与词法分数按最大值归一后线性融合
                d = np.maximum(d - d.mean(), 0)
                d = d / d.max() if d.max() > 0 else d
                l = lex / lex.max() if lex.max() > 0 else lex
                lex = (1 - c["dense_w"]) * l + c["dense_w"] * d
        if c["scope"] and pq.scope and not pq.has_content:
            return self._scope_only(pq)
        return lex * self.prior(pq)

    def _scope_only(self, pq: ParsedQuery) -> np.ndarray:
        """只有范围词（如「四年级这学期数与代数、图形与几何各出一套综合卷」）：按年级/学期/领域取可考查知识点，不用文本匹配。"""
        out = np.zeros(len(self.ids))
        for i, kid in enumerate(self.ids):
            kp, loc = self.cur.kp(kid), self._locs[i]
            if not kp.is_assessable or "总复习" in kp.name:
                continue
            v = 1.0
            if pq.domains and kp.domain.value not in pq.domains:
                continue
            if pq.grade is not None:
                if loc.grade != pq.grade:
                    v = 0.05
                elif pq.semester and loc.semester != pq.semester:
                    v = 0.5
            out[i] = v
        return out

    def _diversify(self, s: np.ndarray, keep: list[int], k: int, penalty: float = 0.8) -> list[int]:
        """综合/总复习类需求希望覆盖面广：贪心取分，同一主线已选 n 个后得分乘 penalty**n。"""
        chosen: list[int] = []
        used: Counter = Counter()
        pool = {i for i in keep if s[i] > 0}
        while pool and len(chosen) < k:
            best = max(pool, key=lambda i: (s[i] * penalty ** used[self.cur.kp(self.ids[i]).thread], -i))
            chosen.append(best)
            used[self.cur.kp(self.ids[best]).thread] += 1
            pool.discard(best)
        return chosen

    def search(self, query, k=10, *, grade=None, domain=None, verifiable_type=None, assessable_only=False) -> list[SearchHit]:
        pq = parse_query(query)
        s = self.final_scores(pq)
        vt = None
        if verifiable_type is not None:
            vt = {a.primary_knowledge_point_id for a in self.cur.archetypes_by_id.values() if a.verifiable_type.value == verifiable_type}
        keep = []
        for i, kid in enumerate(self.ids):
            kp = self.cur.kp(kid)
            if domain is not None and kp.domain.value != domain:
                continue
            if assessable_only and not kp.is_assessable:
                continue
            if vt is not None and kid not in vt:
                continue
            if grade is not None and self._locs[i].grade != grade:
                continue
            keep.append(i)
        if pq.scope and self.cfg["scope"]:
            order = self._diversify(s, keep, k)
        else:
            order = sorted(keep, key=lambda i: (-s[i], self.ids[i]))
            order = [i for i in order if s[i] > 0][:k]
        out = []
        for i in order:
            kp, loc = self.cur.kp(self.ids[i]), self._locs[i]
            out.append(SearchHit(self.ids[i], float(s[i]), kp.name, kp.domain.value, loc.grade, loc.semester, loc.unit_title, loc.lesson_id, loc.lesson_title))
        return out
