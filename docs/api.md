# ChalkBase 对外接口

ChalkBase 是北师大版小学数学 12 册教材的课程知识库：知识图谱、题型卡片、情境库、术语规范、能力边界，以及把题型卡片实例化成具体题目的运行时。下游项目只通过本文的接口访问数据，不需要教材 PDF，也不直接读 `data/*.json`。

> 本文是完整参考。给调用方 Agent 看的精简版（对象与 ID、常用调用、典型流程、必须知道的语义）在 [`chalkbase/AGENT_GUIDE.md`](../chalkbase/AGENT_GUIDE.md)，也可用 `python -m chalkbase guide` 或 `chalkbase.agent_guide()` 取得。

```bash
pip install chalkbase                  # 或固定版本：pip install chalkbase==0.1.1
```

```python
import chalkbase
from chalkbase import Curriculum

chalkbase.__version__                    # "0.1.1"
cur = Curriculum()                       # 加载随包数据（约 1 秒）
cur.search("四年级小数加减法的拔高题，需要进位退位比较麻烦的那种", k=5)
p = cur.instantiate("at.三角形_angle_sum.01", seed=3, lesson_id="g4b.u2.l03")
p.problem, p.answer, p.verdict           # 题面, 答案, "in" / "borderline" / "out"
```

## 安装与依赖

| 安装方式 | 依赖 | 能用的功能 |
|---|---|---|
| `pip install chalkbase` | pydantic、numpy | 全部查询、检索（词法；有 `DASHSCOPE_API_KEY` 时加向量）、能力边界、题型实例化 |
| `chalkbase[llm]` | + requests | `chalkbase.boundary.extract`（见下） |
| `chalkbase[build]` | + pyyaml、scikit-learn、networkx、pymupdf | 重跑构建流水线、评测 |
| `chalkbase[dev]` | + pytest | 跑测试 |

Python ≥ 3.11。

## 数据定位

规范数据的唯一事实来源是仓库根目录的 `data/`；wheel 把它打包到 `chalkbase/data/`。定位优先级：

1. 环境变量 `CHALKBASE_DATA`（指向任意数据目录）
2. 包内 `chalkbase/data`
3. 仓库根 `data/`（开发态）

`Curriculum(data_dir=...)` 可显式指定目录。wheel 不含 `work/`、`eval/`、`textbook/`。`data/exercises.json` 含教材习题原文摘录，随包发布：题型卡片通过 `source_instance_ids` 引用它，`archetype_lessons` 也据此定位课时；这些原文只供核对与定位，生成环节不得照抄（题型卡片的改写示例都是新写的）。

## Curriculum

### 检索与查找

| 方法 | 说明 |
|---|---|
| `search(query, k=10, *, grade=, domain=, verifiable_type=, assessable_only=)` | 自然语言/名称/别名检索知识点，返回 `SearchHit`（`kp_id, score, name, domain, grade, semester, unit_title, lesson_id, lesson_title`）。需求里的年级、学期、领域自动解析为软先验；显式参数是硬过滤（`grade` 按首次引入年级） |
| `kp(id)`、`find_kp(名称或别名)`、`kps(grade=, domain=, thread=, book=, assessable=, verifiable_type=)` | 精确查找，按引入顺序返回 |
| `locate(kp_id)`、`lesson_location(lesson_id)`、`kp_grade(kp_id)` | `Location`：书、年级、学期、单元、课时、教学序列位置 |
| `lesson(id)`、`lesson_ids()`、`lesson_position(id)`、`lessons_of(book=, unit=)` | 课时与课时全序 |

**服务端部署时的查询向量**：默认实现用标准库 `urllib` 同步请求（走环境代理、每次新建连接）。需要并发或连接复用时，用 `chalkbase.query.embed.set_embedder(fn)` 注入自己的实现（`fn(list[str]) -> list[list[float]]`，同步，可在工作线程里被并发调用；传 `None` 恢复默认）。网络请求在锁外进行，并发的查询互不等待；命中随包向量或本地缓存的文本不会调用它，计算出的向量同样写入本地缓存。实现抛出 `EmbeddingUnavailable` 时检索会降级为词法检索。

检索的工作方式：需求解析（年级、学期、领域、综合/总复习类、套话剔除）→ 名称/别名/主线/描述/课时标题的字符二元组 BM25F → 与 `text-embedding-v4` 向量相似度线性融合（权重 0.5）→ 年级/领域先验。知识点向量随包发布（`data/kp_embeddings.npz`，float16）；查询向量需要在线调用，读环境变量 `DASHSCOPE_API_KEY`（节点 `DASHSCOPE_BASE_URL`）。**没有 key 或网络不通时自动降级为纯词法检索并发出一次 `warnings.warn`**。查询向量缓存在 `CHALKBASE_CACHE`（默认 `~/.cache/chalkbase/embeddings`）。

### 关系图与教学进度

| 方法 | 说明 |
|---|---|
| `chain(kp, direction=, depth=, edge_types=, include_implied=)`，快捷方式 `prerequisites()`、`dependents()` | 前置/后续链。`direction` 为 `"prerequisite"` 或 `"dependents"`；`edge_types` 区分严格前置 `prerequisite` 与递进依赖 `builds_on`（还可含 `extends` / `related` / `confusable`）；`include_implied=True` 时包含被传递约简的隐含边。返回 `ChainEntry(kp_id, depth, edge_type, via, implied)` |
| `relations(kp, types=)` | 无方向性的关系：相关、易混淆、螺旋扩展 |
| `learned_before(lesson_id, inclusive=, domain=, grade=)`、`not_yet_learned(lesson_id)` | 已学 / 尚未学习的知识点集合（首次引入课时在该课时之前，含与否由 `inclusive` 决定） |
| `review_candidates(lesson_id, target_kp_ids=None, depth=3)` | 螺旋复习候选：该课时前已学、且在目标知识点前置链上的知识点，按链距离、再按近期优先排序 |

### 题型卡片、情境、术语

| 方法 | 说明 |
|---|---|
| `archetypes(kp_id=, domain=, grade=, verifiable_type=, item_form=, difficulty=(lo,hi), include_secondary=)`、`archetype(id)` | `ItemArchetype`：抽象模板、参数约束（槽位与 `constraints`）、求解程序、改写示例、可验证类型、难度 1–5、典型错误 |
| `archetype_lessons(archetype_id)` | 该题型所归纳的教材实例所在课时（教学序升序）。最后一个是参数包络完整出现的位置，可作为 `instantiate(..., lesson_id=)` 的默认课时 |
| `contexts(grade=, text=)`、`context(id)`、`contexts_for(archetype_id)` | 情境库 |
| `glossary(术语或别名)`、`glossary_search(文本)` | 规范术语、记号、教材常见题干措辞 |

可验证类型：`program`（答案由题面数值唯一确定，可由程序求解）、`rule`（可按规则检查但答案不唯一，如画图）、`human`（开放表达，需人工评判）。

### 题型实例化

`instantiate` 在题型卡片的槽位与约束内按种子采样参数，渲染题面模板，`program` 类在沙箱里求解。

```python
cur.instantiate(archetype_id, seed=0, lesson_id=None, *, only_in_bounds=False, accept_borderline=False, max_tries=50) -> Problem
cur.instantiate_many(archetype_id, n, seed0=0, lesson_id=None, *, only_in_bounds=False, accept_borderline=False, max_tries=50, unique=True) -> list[Problem]
cur.instantiate_with(archetype_id, params, lesson_id=None) -> Problem        # 用给定参数求解，seed=None
```

- **可复现**：同一 (卡片, 种子) 的结果完全确定（每个种子对应一条独立随机流，取其第一组满足 `constraints` 的参数）。不同种子给出不同的题；`instantiate_many` 默认跳过参数组合重复的种子，`Problem.seed` 是实际使用的种子，可单独复现；参数空间太小或边界太严时返回不足 `n` 道（至少一道，否则抛异常）。
- **精确数值**：槽位 `int` / `decimal` / `fraction` 分别解析为 `int` / `Decimal` / `Fraction`，求解返回浮点会被拒绝。
- **边界**：给 `lesson_id` 时同时返回该题在该课时能力边界下的判定。`only_in_bounds=True` 时只接受判定为 `in` 的参数（`accept_borderline=True` 时 `borderline` 也接受），在该种子的随机流里最多检查 `max_tries` 组，仍没有则抛 `NoInBoundsSample`；需要同时给 `lesson_id`。
- **rule / human**：没有求解器，返回渲染后的题面，`answer` 与 `answer_value` 为 `None`。
- **沙箱**：采样、约束检查与求解都在独立子进程中执行（`python -I`，只暴露 Decimal / Fraction / math 与一小组内建函数，禁止 import、open、dunder 属性等，带超时），调用方进程不执行卡片里的任何代码。这是进程级隔离加源码过滤，不是操作系统级沙箱；卡片来自本库的校验流水线，不要用它执行不可信来源的求解程序。
- **模板占位符**：`{槽位}` 代入槽位值；`{a + b}`、`{a // b}`、`{a ** 2}` 这类由槽位组成的算术表达式（AST 白名单，只允许 `+ - * / // %`、0～8 次整数幂、括号和常量，没有调用与属性访问；choice 槽位的整数选项按整数参与运算）按精确数值求值；`blank`、`ans`、`answer`、`result`、`quotient`、`remainder` 开头的空位渲染为 `______`。choice 选项文本里的占位符会再代入至多两轮。无法解析的占位符原样保留并记入 `Problem.warnings`；`tests/test_runtime.py` 保证数据里的 program 题型没有这种情况，rule / human 题型有 4 张例外（`KNOWN_UNRENDERABLE_NON_PROGRAM`，实例化会报错或带 warnings）。
- **无解参数**：program 类求解程序返回 `None` 的候选参数视为无解，自动顺延到该种子的下一组候选。
- 异常：`InstantiationError`（约束不可满足、求解出错、超时），其子类 `NoInBoundsSample`；未知题型/课时 ID 抛 `KeyError`，参数键与槽位不一致抛 `ValueError`。均可从 `chalkbase.runtime` 导入。

### Problem

`chalkbase.Problem`（不可变 dataclass）：

| 字段 | 含义 |
|---|---|
| `archetype_id`、`knowledge_point_id`、`item_form`、`difficulty`、`verifiable_type` | 题型信息 |
| `problem` | 渲染后的题面（图形部分用文字描述） |
| `answer` | 答案的显示串：分数 `a/b`，小数不带指数，布尔为「正确/错误」，多个答案用「，」连接；rule/human 为 `None` |
| `answer_value` | 精确答案：`int` / `Decimal` / `Fraction` / `bool` / `str`，或它们组成的 `list` / `dict`；rule/human 为 `None` |
| `solution` | 求解步骤（卡片里的抽象步骤，不含具体数值） |
| `params` | 本题参数，按槽位类型为 `int` / `Decimal` / `Fraction` / `str` |
| `features` | `ItemFeatures`，由数值参数推出：最大整数、小数位数、分数类型。**不含答案**（教材同类题的答案本来就可能比参数大），也不含概念、计量单位、几何词汇——需要时自行补充或用 `chalkbase.boundary.extract` |
| `seed` | 种子；`instantiate_with` 为 `None` |
| `lesson_id`、`boundary` | 给了 `lesson_id` 时的课时与完整 `BoundaryReport` |
| `verdict`、`violated_dimensions` | 便捷属性：判定三值 / 越界维度列表；未给课时为 `None` / `[]` |
| `warnings` | 渲染时发现的卡片缺陷 |
| `to_dict()` | JSON 友好字典（`Decimal` / `Fraction` 写成字符串） |

## 能力边界

```python
cur.boundary(lesson_id) -> CapabilityBoundary          # 该课时（含）之前学生具备的能力
cur.check_item(features, lesson_id) -> BoundaryReport  # features 为 ItemFeatures 或同结构 dict
```

`CapabilityBoundary` 的维度：整数数域上限 `integer_domain_max`、小数最多位数 `decimal_max_places`、分数类型 `fraction_types`、各运算的操作数形态 `operation_operand_forms`、概念 `concepts`、计量单位 `units_of_measure`、几何词汇 `geometry_vocab`。它是沿教学序列对各知识点 `grants` 做半格合并的结果，只增不减。

`ItemFeatures` 与这些维度一一对应：`integer_max`、`decimal_places`、`fraction_types`、`operations`（具体二元运算，形态由程序从数值推出）与 `operation_forms`（直接给形态标签）、`requires_carry_or_borrow`、`concepts`、`units_of_measure`、`geometry_vocab`。集合维度的取值须是受控词表中的规范名或其别名，校验时会规范化。

`BoundaryReport`：

| 字段 | 含义 |
|---|---|
| `verdict` | `"in"`：全部在边界内；`"borderline"`：越界项都只是「同一单元内稍后才引入」，教材自身习题也常在前一课时先行探索，建议人工复核；`"out"`：明确越界 |
| `in_bounds` | `verdict == "in"` |
| `violations` | 越界项列表：`dimension`、`item_value`、`allowed`、`introduced_at`（该能力最早引入的课时）、`detail` |
| `unknown` | 词表外、无法判定的取值（如未收录的概念名）；不计入越界，供人工或下游参考 |

全部确定性，不调用语言模型。

## 可选功能：从题面抽取特征（需要 DashScope key）

`chalkbase.boundary.extract` 用流水线模型 qwen3.7-plus（非思考）读题，列出题面里出现的数、运算、概念、单位、几何词汇，得到 `ItemFeatures`，再交给 `check_item` 判定。需要 `pip install chalkbase[llm]` 与环境变量 `DASHSCOPE_API_KEY`（可选 `DASHSCOPE_BASE_URL`）。

```python
from chalkbase.boundary.extract import extract_features, extract_features_batch
feats = extract_features("一个长方形的长是 3.25 米，宽是 2 米，面积是多少平方米？")
report = cur.check_item(feats, "g4a.u3.l01")
```

响应缓存在 `CHALKBASE_LLM_CACHE`（默认当前目录的 `.cache/`，没有则 `~/.cache/chalkbase/llm`）。这是核心接口之外的可选功能：没有 key 时不可用，其余接口不受影响。

## 数据文件（`data/`）

| 文件 | 内容 |
|---|---|
| `books.json` | 12 本书、单元（标题、页码范围、课时列表）；书的先后顺序即教学序列 |
| `lessons.json` | 课时：标题、引入/练习的知识点、概括 |
| `knowledge_points.json` | 规范知识点：名称、别名、领域/主题/主线、描述、引入课时与复现课时、`grants`（能力增量）、典型错误 |
| `kp_local_map.json` | 各书局部知识点 → 规范知识点的映射 |
| `edges_relations.json` | 关系边：`prerequisite` / `builds_on` / `related` / `confusable`，带证据与 `is_direct` |
| `edges_extends.json` | 螺旋扩展边（窄→宽） |
| `exercises.json` | 习题实例（含原文摘录，图形部分为文字描述） |
| `archetypes.json` | 题型卡片 |
| `contexts.json` | 情境库 |
| `glossary.json` | 术语、记号、题干措辞 |
| `boundaries.json` | 各课时能力边界（按课时增量存储） |
| `standard_coverage.json` | 2022 版课标「内容要求」条目及其与知识点的覆盖对照 |
| `kp_embeddings.npz` | 433 个知识点的 `text-embedding-v4` 向量（float16），附模型 ID 与文本哈希；文本变了的条目自动失效，改由在线调用补 |
| `manifest.json` | 清单（见下） |

模型定义在 `chalkbase/models.py`，导出的 JSON Schema 在仓库的 `schema/`。接口只依赖模型里的稳定字段；缺失的可选文件按「没有」处理。

## 版本与兼容性

- `chalkbase.__version__` 是版本的唯一来源（`pyproject.toml` 从中读取）。当前 `0.1.1`。
- `Curriculum.manifest` 是 `data/manifest.json` 的内容：`data_version`、`schema_version`、`embedding_model`、`built_at`、`files`（每个文件的 `records` 条目数、`bytes`、`sha256`；JSON 文件按 LF 行尾计算，Windows 检出也一致）。数据目录没有清单时为 `None`。
- schema 版本为 `major.minor`。加载时 major 与代码不同则抛 `chalkbase.DataSchemaError`，提示安装匹配的版本。同一 major 内只会新增可选字段（minor 加一），已有字段的含义与类型不变。
- 兼容性承诺（0.x 阶段）：本文列出的 `Curriculum` 方法、`Problem` / `ItemFeatures` / `BoundaryReport` 的字段、`chalkbase.runtime` 的异常类型视为公开接口；不兼容变更只在 minor 版本号（0.x → 0.y）上发生并写入变更记录。其余模块（`stage*`、`annotate`、`eval` 等）是构建流水线的内部实现，不在承诺范围内。
- 数据变动后：`python scripts/build_manifest.py` 重新生成清单；知识点名称/别名/描述变动后另需 `python scripts/build_embeddings.py` 重算向量。两者都有不变量测试，忘记重生成会失败。

## 命令行

```bash
python -m chalkbase --version
python -m chalkbase guide                         # 打印面向调用方 Agent 的精简使用说明
python -m chalkbase search "三年级学乘法分配律的应用题" -k 5 [--grade 3] [--domain na] [--verifiable program]
python -m chalkbase kp 乘法分配律                 # ID 或名称/别名
python -m chalkbase chain kp.gg.四边形.angle_sum --depth 2 [--dependents] [--types prerequisite,builds_on] [--implied]
python -m chalkbase learned g4a.u3.l01 [--inclusive] [--domain na] [--grade 3]
python -m chalkbase archetypes kp.gg.三角形.angle_sum --examples
python -m chalkbase instantiate at.三角形_angle_sum.01 -n 3 --seed 0 --lesson g4b.u2.l03 [--in-bounds]
python -m chalkbase boundary g4a.u3.l01           # 某课时的能力边界（JSON）
python -m chalkbase contexts --grade 3 --text 超市
python -m chalkbase glossary 周长
python -m chalkbase --json search "圆柱的体积"    # 全局 --json 放在子命令之前
```

## 验证

- 题型实例化全量 smoke：`python scripts/smoke_instantiate.py`（每张卡片 5 个种子）。
- 打包验证：`python scripts/smoke_wheel.py`（构建 wheel，在不在仓库目录下的全新 venv 里非 editable 安装，跑 CLI 检索、实例化、边界）。
- 测试：`python -m pytest`；评测：`python -m chalkbase.eval --split test`。
