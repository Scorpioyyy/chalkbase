# ChalkBase 使用说明（面向调用方 Agent）

ChalkBase 是北师大版小学数学（1～6 年级共 12 册）的课程知识库。你用它回答三类问题：**教什么**（知识点与先后依赖）、**怎么考**（题型与可验证的出题）、**学到哪**（某课时之前学生会什么、不会什么）。它不调用语言模型（除明确标注的可选功能），所有结果确定、可复现。

```python
from chalkbase import Curriculum
cur = Curriculum()          # 一次加载，重复使用
```

## 三个对象与 ID

- **知识点** `kp.na.整数乘法.carry`：一个原子教学内容。领域前缀 `na` 数与代数、`gg` 图形与几何、`sp` 统计与概率、`ip` 综合与实践。
- **题型** `at.<知识点slug>.<序号>`：某个知识点下的一类题，带槽位与约束，能被实例化成无数道具体题。
- **课时** `g4a.u3.l02`：四年级上册第 3 单元第 2 课时。书 ID `g<年级><a上|b下>`。课时 ID 是「学生学到哪」的唯一表达。

**不要自己编 ID。** 先用 `search` / `find_kp` 得到知识点 ID，再用 `archetypes(kp_id=...)` 得到题型 ID。

## 常用调用

| 目的 | 调用 |
|---|---|
| 教师说了一个需求，找对应知识点 | `cur.search("三年级两位数乘一位数的竖式", k=5)` → `SearchHit(kp_id, name, grade, semester, lesson_id…)`；需求里的年级/学期会被自动解析 |
| 某知识点有哪些题型 | `cur.archetypes(kp_id=…, verifiable_type="program")` → 题型的模板、难度 1–5、可验证类型 |
| 出一道题 | `cur.instantiate(archetype_id, seed=0, lesson_id=…, only_in_bounds=True)` → `Problem` |
| 同一题型出多道不同的题 | `cur.instantiate_many(archetype_id, n=5, lesson_id=…)` |
| 一个知识点先要会什么 | `cur.chain(kp_id, direction="prerequisite", depth=2)`（后续：`direction="dependents"`） |
| 学到某课时，学生已学什么 | `cur.learned_before(lesson_id, inclusive=True)`、`cur.not_yet_learned(lesson_id)` |
| 螺旋复习：把以前学的穿插进来 | `cur.review_candidates(lesson_id, target_kp_ids=[…])` |
| 一道题是否超纲 | `cur.check_item(features, lesson_id)` → `BoundaryReport.verdict` |
| 某课时学生能处理的数与运算范围 | `cur.boundary(lesson_id)` |

## 典型流程：按教师需求出一道不超纲的题

1. `hits = cur.search(需求)`，取最相关的 `kp_id`；教师没说学到哪就用 `hits[0].lesson_id`（该知识点首次引入的课时）。
2. `ats = cur.archetypes(kp_id=kp_id, verifiable_type="program")`，按 `difficulty` 选一个。
3. `p = cur.instantiate(ats[0].id, seed=随机数, lesson_id=教师当前课时, only_in_bounds=True)`。
4. 把 `p.problem` 交给教师，**答案用 `p.answer`，不要让语言模型重新计算**——这是本库存在的意义：答案由程序求出，保证正确。

## 必须知道的语义

- **可验证类型**：`program` 有程序算出的唯一答案（`p.answer` 可信）；`rule` 只能按规则检查（如画图），`p.answer` 为 `None`；`human` 是开放作答，`p.answer` 为 `None`。只有 `program` 题型能做到「答案不靠模型」。
- **边界判定三值**：`in` 全部在边界内；`borderline` 只是同一单元内稍后才引入、教材自身也常先行探索，可用但建议复核；`out` 明确超纲。`only_in_bounds=True` 必须同时给 `lesson_id`，找不到合法参数会抛 `NoInBoundsSample`。
- **可复现**：同一 (题型, seed) 永远得到同一道题；换 seed 得到不同题。要「再来一道」就换 seed。
- **数值精确**：参数与答案是 `int` / `Decimal` / `Fraction`，没有浮点误差；显示用 `p.answer`，计算用 `p.answer_value`。
- **`Problem.features` 只覆盖数值维度**（最大整数、小数位数、分数类型），不含概念、单位、几何词汇。要判断一段自由文本的题是否超纲，先用 `chalkbase.boundary.extract.extract_features(题面)` 抽特征（需要 DashScope key），再 `check_item`。
- **检索**：有 `DASHSCOPE_API_KEY` 时融合向量，没有时自动降级为词法检索（会警告一次），结果仍可用。其余功能完全离线。
- **复习与先备**：`prerequisite` 是严格先备，`builds_on` 是递进依赖（强度较弱）；做复习穿插优先用 `review_candidates`，不要自己拼链。
- **版本差异**：12 册中 9 册是 2022 课标新版、3 册（四下、五下、六下）是 2011 课标旧版，课时顺序按新版教学序列。个别内容（如分数初步认识、长方形面积）在教材里没有对应课时，库里以「补全」知识点（`provenance=reconciled`）表示，其引入课时是最小满足先备关系的位置，不一定是真实教学位置。

## 出错时

- `KeyError`：ID 不存在（多半是编造或拼错），重新 `search`。
- `InstantiationError`：约束无法满足/求解失败，换 seed 或换题型。
- `NoInBoundsSample`：该课时下这个题型采不到边界内的参数，换更早引入的题型或放宽到 `accept_borderline=True`。

更完整的接口（全部方法、`Problem` 字段、数据文件、版本兼容性）见 `docs/api.md`；命令行 `python -m chalkbase --help`。
