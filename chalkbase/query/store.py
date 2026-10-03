"""课程知识库的只读查询接口：加载 `data/`，提供按名称/自然语言检索、前置链、已学集合、题型卡片、情境与术语查询。

只依赖 `chalkbase.models` 中的稳定字段；缺失的可选文件（如 Stage 5 尚未产出的隐含边标记）一律容错。
后续各环节（意图解析、检索与生成、验证、组卷、螺旋复习）只通过本接口访问数据。
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Iterable, Optional, Sequence

from chalkbase.common import DATA_DIR, book_sequence, read_json
from chalkbase.manifest import check_compatible, load_manifest
from chalkbase.models import (
    Context,
    GlossaryEntry,
    ItemArchetype,
    KnowledgePoint,
    Lesson,
)

# 参与"前置链"遍历的边类型：prerequisite 为强前置，builds_on 为递进依赖；其余类型无方向性依赖语义。
DIRECTIONAL_EDGE_TYPES = ("prerequisite", "builds_on")
ALL_EDGE_TYPES = ("prerequisite", "builds_on", "extends", "related", "confusable")


@dataclass(frozen=True)
class Location:
    """知识点（或课时）在教材中的定位。"""

    book_id: str
    grade: int
    semester: str  # a=上册 b=下册
    unit_id: str
    unit_title: str
    lesson_id: str
    lesson_title: str
    position: int  # 课时在全部课时全序（教学序列）中的位置，从 0 开始

    @property
    def label(self) -> str:
        sem = "上" if self.semester == "a" else "下"
        return f"{self.grade}年级{sem}册 · {self.unit_title} · {self.lesson_title}"


@dataclass(frozen=True)
class ChainEntry:
    """前置链/后续链上的一个知识点。`depth`：到起点的最短边数；`edge_type`：到达该点所经最后一条边的类型。"""

    kp_id: str
    depth: int
    edge_type: str
    via: str  # 上一跳知识点（起点自身为 ""）
    implied: bool  # 最后一条边是否为被传递约简的隐含边


class Curriculum:
    """课程知识库。用法：`from chalkbase import Curriculum; cur = Curriculum()`。"""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        d = self.data_dir
        #: `data/manifest.json` 的内容（数据版本、schema 版本、各文件条目数与 sha256、构建日期）；数据目录没有清单时为 None
        self.manifest: Optional[dict] = load_manifest(d)
        if self.manifest is not None:
            check_compatible(self.manifest)  # schema 的 major 版本不一致时抛 DataSchemaError
        self.knowledge_points: dict[str, KnowledgePoint] = {}
        for r in _read(d / "knowledge_points.json"):
            kp = KnowledgePoint.model_validate(r)
            self.knowledge_points[kp.id] = kp
        self.lessons: dict[str, Lesson] = {}
        for r in _read(d / "lessons.json"):
            self.lessons[r["id"]] = Lesson.model_validate(r)
        self.archetypes_by_id: dict[str, ItemArchetype] = {}
        for r in _read(d / "archetypes.json"):
            a = ItemArchetype.model_validate(r)
            self.archetypes_by_id[a.id] = a
        self._contexts: dict[str, Context] = {}
        for r in _read(d / "contexts.json"):
            c = Context.model_validate(r)
            self._contexts[c.id] = c
        self._glossary: list[GlossaryEntry] = [GlossaryEntry.model_validate(r) for r in _read(d / "glossary.json")]
        self._books = _read(d / "books.json")
        self._edges = _read(d / "edges_relations.json") + _read(d / "edges_extends.json")
        self._searcher = None

    # ------------------------------------------------------------------ 课时全序与定位

    @cached_property
    def _unit_info(self) -> dict[str, tuple[int, str]]:
        """单元 ID → (单元序号, 标题)。"""
        out = {}
        for b in self._books:
            for u in b.get("units", []):
                out[u["id"]] = (u["index"], u["title"])
        return out

    @cached_property
    def _lesson_order(self) -> dict[str, int]:
        books = list(book_sequence())

        def key(l: Lesson):
            book = l.id.split(".")[0]
            bi = books.index(book) if book in books else len(books)
            return (bi, self._unit_info.get(l.unit_id, (0, ""))[0], l.index, l.id)

        ordered = sorted(self.lessons.values(), key=key)
        return {l.id: i for i, l in enumerate(ordered)}

    def lesson_ids(self) -> list[str]:
        """全部课时 ID，按教学序列排序。"""
        return sorted(self._lesson_order, key=self._lesson_order.get)

    def lesson_position(self, lesson_id: str) -> int:
        if lesson_id not in self._lesson_order:
            raise KeyError(f"未知课时: {lesson_id}")
        return self._lesson_order[lesson_id]

    def lesson(self, lesson_id: str) -> Lesson:
        return self.lessons[lesson_id]

    def lesson_location(self, lesson_id: str) -> Location:
        l = self.lessons[lesson_id]
        book = lesson_id.split(".")[0]
        return Location(
            book_id=book,
            grade=int(book[1]),
            semester=book[2],
            unit_id=l.unit_id,
            unit_title=self._unit_info.get(l.unit_id, (0, l.unit_id))[1],
            lesson_id=lesson_id,
            lesson_title=l.title,
            position=self._lesson_order[lesson_id],
        )

    def locate(self, kp_id: str) -> Location:
        """知识点首次引入位置（年级、学期、单元、课时）。"""
        return self.lesson_location(self.knowledge_points[kp_id].first_introduced_lesson_id)

    def kp_grade(self, kp_id: str) -> int:
        return self.locate(kp_id).grade

    # ------------------------------------------------------------------ 知识点

    def kp(self, kp_id: str) -> KnowledgePoint:
        return self.knowledge_points[kp_id]

    def find_kp(self, name: str) -> list[KnowledgePoint]:
        """按名称或别名精确匹配（忽略首尾空白），用于已知名称的快速定位；自然语言请用 `search`。"""
        name = name.strip()
        return [k for k in self.knowledge_points.values() if name == k.name or name in k.aliases]

    def kps(
        self,
        *,
        grade: Optional[int] = None,
        domain: Optional[str] = None,
        thread: Optional[str] = None,
        book: Optional[str] = None,
        assessable: Optional[bool] = None,
        verifiable_type: Optional[str] = None,
    ) -> list[KnowledgePoint]:
        """按引入年级 / 领域 / 主线 / 书 / 是否可考查 / 题型可验证类型（至少有一张该类型的题型卡）筛选，按引入顺序排序。"""
        vt_kps = None
        if verifiable_type is not None:
            vt_kps = {a.primary_knowledge_point_id for a in self.archetypes_by_id.values() if a.verifiable_type.value == verifiable_type}
        out = []
        for k in self.knowledge_points.values():
            if domain is not None and k.domain.value != domain:
                continue
            if thread is not None and k.thread != thread:
                continue
            if assessable is not None and k.is_assessable != assessable:
                continue
            if vt_kps is not None and k.id not in vt_kps:
                continue
            if grade is not None or book is not None:
                loc = self.locate(k.id)
                if grade is not None and loc.grade != grade:
                    continue
                if book is not None and loc.book_id != book:
                    continue
            out.append(k)
        out.sort(key=lambda k: (self.locate(k.id).position, k.id))
        return out

    # ------------------------------------------------------------------ 检索

    @property
    def searcher(self):
        if self._searcher is None:
            from chalkbase.query.retrieval import Searcher

            self._searcher = Searcher(self)
        return self._searcher

    def search(
        self,
        query: str,
        k: int = 10,
        *,
        grade: Optional[int] = None,
        domain: Optional[str] = None,
        verifiable_type: Optional[str] = None,
        assessable_only: bool = False,
    ):
        """自然语言检索知识点，返回 `SearchHit` 列表（带年级/单元/课时定位与得分）。

        需求里的年级、学期、领域会被自动解析并作为软先验；显式给出的 `grade` / `domain` /
        `verifiable_type` 是**硬过滤**（`grade` 按首次引入年级）。"""
        return self.searcher.search(
            query, k, grade=grade, domain=domain, verifiable_type=verifiable_type, assessable_only=assessable_only
        )

    # ------------------------------------------------------------------ 关系图

    @cached_property
    def _adj(self) -> dict[str, dict[str, list[tuple[str, str, bool]]]]:
        """{'forward': {from: [(to, type, implied)]}, 'backward': {to: [(from, type, implied)]}}。"""
        fwd, bwd = defaultdict(list), defaultdict(list)
        for e in self._edges:
            a, b, t = e["from_knowledge_point_id"], e["to_knowledge_point_id"], e["type"]
            if a not in self.knowledge_points or b not in self.knowledge_points:
                continue  # 引用不可解析的边忽略（不变量测试会单独报告）
            implied = (e.get("is_direct", True) is False) or bool(e.get("implied", False))
            fwd[a].append((b, t, implied))
            bwd[b].append((a, t, implied))
        return {"forward": dict(fwd), "backward": dict(bwd)}

    def chain(
        self,
        kp_id: str,
        *,
        direction: str = "prerequisite",
        depth: Optional[int] = 1,
        edge_types: Sequence[str] = ("prerequisite",),
        include_implied: bool = False,
    ) -> list[ChainEntry]:
        """前置链 / 后续链（广度优先，每个知识点取最短深度）。

        direction: 'prerequisite'（沿边反向，找 kp 依赖的知识点）或 'dependents'（沿边正向，找依赖 kp 的知识点）。
        depth: 最大深度，None 为不限。
        edge_types: 参与遍历的边类型。严格前置只给 ('prerequisite',)；含递进依赖给 ('prerequisite','builds_on')。
        include_implied: False 时只走传递约简后保留的直接边（`is_direct=True`）；True 时同时走被约简的隐含边
            （隐含边把间接前置变成一跳，因此深度更小）。Stage 5 之前所有边都是直接边，两者结果相同。
        """
        if kp_id not in self.knowledge_points:
            raise KeyError(f"未知知识点: {kp_id}")
        if direction not in ("prerequisite", "dependents"):
            raise ValueError("direction 须为 'prerequisite' 或 'dependents'")
        adj = self._adj["backward" if direction == "prerequisite" else "forward"]
        types = set(edge_types)
        seen = {kp_id}
        out: list[ChainEntry] = []
        q = deque([(kp_id, 0)])
        while q:
            cur, d = q.popleft()
            if depth is not None and d >= depth:
                continue
            for nxt, t, implied in sorted(adj.get(cur, ()), key=lambda x: (x[0], x[1])):
                if t not in types or nxt in seen:
                    continue
                if implied and not include_implied:
                    continue
                seen.add(nxt)
                out.append(ChainEntry(nxt, d + 1, t, cur, implied))
                q.append((nxt, d + 1))
        return out

    def prerequisites(self, kp_id: str, depth: Optional[int] = 1, **kw) -> list[ChainEntry]:
        return self.chain(kp_id, direction="prerequisite", depth=depth, **kw)

    def dependents(self, kp_id: str, depth: Optional[int] = 1, **kw) -> list[ChainEntry]:
        return self.chain(kp_id, direction="dependents", depth=depth, **kw)

    def relations(self, kp_id: str, types: Iterable[str] = ("related", "confusable", "extends")) -> list[tuple[str, str, str]]:
        """无方向性依赖的关系（相关、易混淆、螺旋扩展）：[(对方 ID, 类型, 'out'|'in')]。"""
        types = set(types)
        out = [(b, t, "out") for b, t, _ in self._adj["forward"].get(kp_id, ()) if t in types]
        out += [(a, t, "in") for a, t, _ in self._adj["backward"].get(kp_id, ()) if t in types]
        return sorted(out)

    # ------------------------------------------------------------------ 教学进度

    def learned_before(
        self,
        lesson_id: str,
        *,
        inclusive: bool = False,
        domain: Optional[str] = None,
        grade: Optional[int] = None,
        include_recurring: bool = False,
    ) -> set[str]:
        """某课时之前（`inclusive=True` 含该课时）已首次引入的知识点 ID 集合，按教学序列（`config/sequence.yaml`）。

        `grade`、`domain` 对知识点的引入年级/领域过滤。`include_recurring` 暂保留（引入即已学，复现不改变"已学"）。"""
        pos = self.lesson_position(lesson_id)
        out = set()
        for k in self.knowledge_points.values():
            p = self._lesson_order.get(k.first_introduced_lesson_id)
            if p is None:
                continue
            if p < pos or (inclusive and p == pos):
                if domain is not None and k.domain.value != domain:
                    continue
                if grade is not None and self.kp_grade(k.id) != grade:
                    continue
                out.add(k.id)
        return out

    def not_yet_learned(self, lesson_id: str, *, inclusive: bool = True) -> set[str]:
        """到该课时为止（含）尚未学习的知识点——`learned_before` 的补集，供越界判断/"超纲"提示。"""
        return set(self.knowledge_points) - self.learned_before(lesson_id, inclusive=inclusive)

    def lessons_of(self, *, book: Optional[str] = None, unit: Optional[str] = None) -> list[Lesson]:
        out = [l for l in self.lessons.values() if (book is None or l.id.startswith(book + ".")) and (unit is None or l.unit_id == unit)]
        return sorted(out, key=lambda l: self._lesson_order[l.id])

    def review_candidates(self, lesson_id: str, target_kp_ids: Optional[Sequence[str]] = None, *, depth: Optional[int] = 3) -> list[str]:
        """螺旋复习候选：该课时之前（不含）已学的知识点；给定 `target_kp_ids` 时，只保留其前置链（含递进依赖）上的知识点。
        按与目标的距离（链深度）升序，其次按引入顺序倒序（越近越先复习）。"""
        learned = self.learned_before(lesson_id)
        if not target_kp_ids:
            return sorted(learned, key=lambda k: -self.locate(k).position)
        best: dict[str, int] = {}
        for t in target_kp_ids:
            for e in self.chain(t, direction="prerequisite", depth=depth, edge_types=("prerequisite", "builds_on")):
                if e.kp_id in learned:
                    best[e.kp_id] = min(best.get(e.kp_id, 10**6), e.depth)
        return sorted(best, key=lambda k: (best[k], -self.locate(k).position, k))

    # ------------------------------------------------------------------ 题型卡片

    def _archetype_grades(self, a: ItemArchetype) -> list[int]:
        gs = sorted({int(i.split(".")[1][1]) for i in a.source_instance_ids if i.startswith("ex.g")})
        if gs:
            return gs
        return [self.kp_grade(a.primary_knowledge_point_id)] if a.primary_knowledge_point_id in self.knowledge_points else []

    def archetype(self, archetype_id: str) -> ItemArchetype:
        return self.archetypes_by_id[archetype_id]

    def archetypes(
        self,
        kp_id: Optional[str] = None,
        *,
        domain: Optional[str] = None,
        grade: Optional[int] = None,
        verifiable_type: Optional[str] = None,
        item_form: Optional[str] = None,
        difficulty: Optional[tuple[int, int]] = None,
        include_secondary: bool = False,
    ) -> list[ItemArchetype]:
        """题型卡片（抽象模板 + 参数约束 + 改写示例 + 可验证类型 + 难度）。

        `kp_id`：主知识点；`include_secondary=True` 时也返回把它作为次要知识点的题型。
        `grade`：按卡片所归纳教材实例所在年级（无实例时按主知识点引入年级）。`difficulty=(lo, hi)` 闭区间。"""
        out = []
        for a in self.archetypes_by_id.values():
            if kp_id is not None and a.primary_knowledge_point_id != kp_id and not (include_secondary and kp_id in a.secondary_knowledge_point_ids):
                continue
            if domain is not None:
                kp = self.knowledge_points.get(a.primary_knowledge_point_id)
                if kp is None or kp.domain.value != domain:
                    continue
            if verifiable_type is not None and a.verifiable_type.value != verifiable_type:
                continue
            if item_form is not None and a.item_form.value != item_form:
                continue
            if difficulty is not None and not (difficulty[0] <= a.difficulty <= difficulty[1]):
                continue
            if grade is not None and grade not in self._archetype_grades(a):
                continue
            out.append(a)
        out.sort(key=lambda a: (a.primary_knowledge_point_id, a.id))
        return out

    # ------------------------------------------------------------------ 情境与术语

    def contexts(self, *, grade: Optional[int] = None, text: Optional[str] = None) -> list[Context]:
        """情境库。`grade`：适用年级；`text`：主题名、观测到的主题变体或典型量中包含该字符串。"""
        out = []
        for c in self._contexts.values():
            if grade is not None and c.applicable_grades and grade not in c.applicable_grades:
                continue
            if text is not None:
                hay = c.theme + " " + " ".join(str(x) for x in c.value_ranges.get("observed_theme_variants", [])) + " " + " ".join(
                    str(x) for x in c.value_ranges.get("typical_quantities", [])
                )
                if text not in hay:
                    continue
            out.append(c)
        return out

    def context(self, context_id: str) -> Context:
        return self._contexts[context_id]

    def contexts_for(self, archetype_id: str) -> list[Context]:
        return [self._contexts[c] for c in self.archetype(archetype_id).allowed_contexts if c in self._contexts]

    def glossary(self, term: str) -> Optional[GlossaryEntry]:
        """按规范术语或别名精确查表述规范（记号、题干措辞）。"""
        for g in self._glossary:
            if term == g.term or term in g.aliases:
                return g
        return None

    def glossary_search(self, text: str) -> list[GlossaryEntry]:
        """术语表中规范术语/别名出现在 `text` 里的条目（长术语优先）。"""
        hits = [g for g in self._glossary if g.term in text or any(a and a in text for a in g.aliases)]
        return sorted(hits, key=lambda g: -len(g.term))

    # ------------------------------------------------------------------ 能力边界（Stage 6，`chalkbase.boundary`）

    def boundary(self, lesson_id: str):
        """某课时（含）之前的能力边界：整数数域、小数位数、分数类型、运算操作数形态、概念、计量单位、几何词汇。

        读 `boundaries.json`（沿教学序列对 `grants` 做半格单调折叠的产物），返回 `chalkbase.models.CapabilityBoundary`。
        未知课时 ID 抛 `KeyError`。"""
        if lesson_id not in self.lessons:
            raise KeyError(f"未知课时: {lesson_id}")
        return self.boundary_store.boundary(lesson_id)

    def check_item(self, features, lesson_id: str):
        """校验一道题的结构化特征（`ItemFeatures` 或同结构的 dict）是否超出 `lesson_id` 处的能力边界。

        返回 `BoundaryReport`：`verdict`（in / borderline / out）、越界维度列表 `violations`、词表外取值 `unknown`。"""
        if lesson_id not in self.lessons:
            raise KeyError(f"未知课时: {lesson_id}")
        from chalkbase.boundary import check_item

        return check_item(features, lesson_id, self.boundary_store)

    @cached_property
    def boundary_store(self):
        from chalkbase.boundary.check import BoundaryStore

        return BoundaryStore(read_json(self.data_dir / "boundaries.json"))

    # ------------------------------------------------------------------ 题型实例化（`chalkbase.runtime`）

    def archetype_lessons(self, archetype_id: str) -> list[str]:
        """题型卡片所归纳的教材实例所在课时（教学序列升序、去重）；无教材源实例的题型（版本对齐补全）取主知识点的引入课时。
        最后一个课时是该题型参数包络完整出现的位置，可作为 `instantiate(..., lesson_id=...)` 的默认课时。"""
        a = self.archetype(archetype_id)
        lessons = {self._exercise_lessons[i] for i in a.source_instance_ids if i in self._exercise_lessons}
        if not lessons and a.primary_knowledge_point_id in self.knowledge_points:
            lessons = {self.knowledge_points[a.primary_knowledge_point_id].first_introduced_lesson_id}
        return sorted((l for l in lessons if l in self._lesson_order), key=self._lesson_order.get)

    @cached_property
    def _exercise_lessons(self) -> dict[str, str]:
        return {e["id"]: e["lesson_id"] for e in _read(self.data_dir / "exercises.json")}

    def _bounds_check(self, lesson_id: Optional[str]):
        if lesson_id is None:
            return None
        if lesson_id not in self.lessons:
            raise KeyError(f"未知课时: {lesson_id}")
        return lambda feats: self.check_item(feats, lesson_id)

    def instantiate(self, archetype_id: str, seed: int = 0, lesson_id: Optional[str] = None, *, only_in_bounds: bool = False,
                    accept_borderline: bool = False, max_tries: int = 50):
        """从题型卡片实例化一道题：在槽位与 `constraints` 内按种子采样参数，渲染题面，`program` 类在沙箱里求解。

        返回 `chalkbase.runtime.Problem`。同一 (卡片, 种子) 结果完全确定。`rule` / `human` 类没有求解器，
        只返回渲染后的题面（`answer=None`）。

        lesson_id：给定时同时在该课时的能力边界下判定（`Problem.boundary` / `.verdict`）。
        only_in_bounds：True 时只接受判定为 `in` 的参数（`accept_borderline=True` 时 `borderline` 也接受），
        在该种子的随机流里最多检查 `max_tries` 组，仍没有则抛 `NoInBoundsSample`。"""
        from chalkbase import runtime

        return runtime.instantiate(self.archetype(archetype_id), seed, lesson_id=lesson_id, check=self._bounds_check(lesson_id),
                                   only_in_bounds=only_in_bounds, accept_borderline=accept_borderline, max_tries=max_tries)

    def instantiate_many(self, archetype_id: str, n: int, seed0: int = 0, lesson_id: Optional[str] = None, *, only_in_bounds: bool = False,
                         accept_borderline: bool = False, max_tries: int = 50, unique: bool = True):
        """从 seed0 起取种子实例化 n 道题；`unique=True`（默认）跳过参数组合重复的种子，`Problem.seed` 记录实际种子，
        用它调 `instantiate` 可单独复现。参数空间太小或边界太严时返回不足 n 道（至少一道，否则抛异常）。"""
        from chalkbase import runtime

        return runtime.instantiate_many(self.archetype(archetype_id), n, seed0, lesson_id=lesson_id, check=self._bounds_check(lesson_id),
                                        only_in_bounds=only_in_bounds, accept_borderline=accept_borderline, max_tries=max_tries,
                                        unique=unique)

    def instantiate_with(self, archetype_id: str, params: dict, lesson_id: Optional[str] = None):
        """用给定参数（槽位名 → int / Decimal / Fraction / str）渲染并求解，`Problem.seed` 为 None。
        用于核对一道题的答案，或在教师改动数值后重新求解。"""
        from chalkbase import runtime

        return runtime.instantiate_with(self.archetype(archetype_id), params, lesson_id=lesson_id, check=self._bounds_check(lesson_id))


def _read(path: Path) -> list:
    """容错读取：文件不存在时返回空列表。"""
    if not Path(path).exists():
        return []
    return read_json(path)
