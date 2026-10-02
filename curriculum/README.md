# curriculum：课程知识库与查询接口

VeriChalk 的"知识底座"：12 本北师大版小学数学教材 → 知识点、关系、题型卡片、情境与术语。后续各环节（意图解析、检索与生成、验证闭环、组卷、螺旋复习）**只通过本包的查询接口访问数据**，不再打开教材 PDF，也不直接读 `data/*.json`。

```python
from curriculum import Curriculum

cur = Curriculum()                       # 加载 data/（约 1 秒）
hits = cur.search("四年级小数加减法的拔高题，需要进位退位比较麻烦的那种", k=5)
for h in hits:
    print(h.kp_id, h.name, h.grade, h.unit_title, h.score)
```

## 接口一览

| 能力 | 方法 | 说明 |
|---|---|---|
| 检索 | `search(query, k, grade=, domain=, verifiable_type=, assessable_only=)` | 自然语言/名称/别名检索，返回 `SearchHit`（知识点 ID、名称、领域、引入年级/学期、单元标题、课时）。需求里的年级、学期、领域自动解析为软先验；显式参数是硬过滤 |
| 精确查找 | `kp(id)`、`find_kp(名称或别名)`、`kps(grade=, domain=, thread=, book=, assessable=, verifiable_type=)` | 按引入顺序返回 |
| 定位 | `locate(kp_id)`、`lesson_location(lesson_id)`、`kp_grade(kp_id)` | `Location`：书、年级、学期、单元、课时、教学序列中的位置 |
| 前置/后续链 | `chain(kp, direction=, depth=, edge_types=, include_implied=)`，快捷方式 `prerequisites()`、`dependents()` | `direction` 为 `"prerequisite"`（它依赖谁）或 `"dependents"`（谁依赖它）；`depth` 为最大深度，`None` 不限；`edge_types` 区分严格前置 `prerequisite` 与递进依赖 `builds_on`（还可包含 `extends`/`related`/`confusable`）；`include_implied=True` 时包含被传递约简的隐含边（`is_direct=False`，Stage 5 产出；此前所有边都是直接边）。返回 `ChainEntry(kp_id, depth, edge_type, via, implied)` |
| 其他关系 | `relations(kp, types=)` | 相关、易混淆、螺旋扩展（无方向性依赖） |
| 教学进度 | `learned_before(lesson_id, inclusive=, domain=, grade=)`、`not_yet_learned(lesson_id)`、`lesson_ids()`、`lesson_position()`、`lessons_of(book=, unit=)` | 按 `config/sequence.yaml` 的教学序列与课时序；"已学"指首次引入课时在该课时之前（含） |
| 螺旋复习 | `review_candidates(lesson_id, target_kp_ids=None, depth=3)` | 该课时前已学、且在目标知识点前置链（含递进依赖）上的知识点，按链距离、再按近期优先排序 |
| 题型卡片 | `archetypes(kp_id=, domain=, grade=, verifiable_type=, item_form=, difficulty=(lo,hi), include_secondary=)`、`archetype(id)` | `ItemArchetype`：抽象模板、参数约束、求解程序、改写示例、可验证类型（program/rule/human）、难度 1–5、典型错误 |
| 情境库 | `contexts(grade=, text=)`、`context(id)`、`contexts_for(archetype_id)` | 情境主题与按年级的数值范围 |
| 表述规范 | `glossary(术语或别名)`、`glossary_search(文本)` | 规范术语、记号、教材常见题干措辞 |
| 能力边界 | `boundary(lesson_id)`、`check_item(features, lesson_id)` | **预留，尚未实现**（Stage 6），现在抛 `NotImplementedError`。完成后：`boundary` 返回 `CapabilityBoundary`（沿教学序列对 `grants` 做半格单调折叠），`check_item` 返回越界维度列表。在此之前用 `learned_before` / `not_yet_learned` 判断"超纲知识点" |

## 命令行

```bash
python -m curriculum search "三年级学乘法分配律的应用题" -k 5 [--grade 3] [--domain na] [--verifiable program]
python -m curriculum kp 乘法分配律                 # ID 或名称/别名
python -m curriculum chain kp.gg.四边形.angle_sum --depth 2 [--dependents] [--types prerequisite,builds_on] [--implied]
python -m curriculum learned g4a.u3.l01 [--inclusive] [--domain na] [--grade 3]
python -m curriculum archetypes kp.gg.三角形.angle_sum --examples
python -m curriculum contexts --grade 3 --text 超市
python -m curriculum glossary 周长
python -m curriculum --json search "圆柱的体积"    # 全局 --json 放在子命令之前
```

## 检索的工作方式（`curriculum/query/retrieval.py`）

1. **需求解析**：抽出年级（可有多个，如"三年级复习二年级学过的…"）、学期、领域（数与代数等）、是否综合/总复习类；剔除"出几道""最好""情境"等套话与范围词。
2. **词法**：每个知识点的名称、别名、主线、描述、引入课时标题 → 字符二元组的 BM25F（字段加权）。
3. **向量**：DashScope `text-embedding-v4` 对名称+别名+描述的向量，与词法分数线性融合（权重 0.5）。向量缓存在 `.cache/embeddings/`；缓存缺失且无 `DASHSCOPE_API_KEY`/网络时自动降级为纯词法并给出警告。
4. **先验**：年级先验（满足需求年级为 1.2；知识点比需求年级晚 n 年引入乘 0.6ⁿ；最后复现年级早于需求年级乘 0.8ⁿ）与领域先验（×1.3）。年级只是软先验：新版教材把部分内容后移（长方形面积未在三年级出现，见 `docs/decisions.md` D10/D14），教师的说法与教材编排可能不一致。
5. **综合/总复习类**：只剩范围词时，按年级/学期/领域取可考查知识点；排序时同一主线内逐个降权，让结果覆盖更多主线。

评测与改进过程见 `eval/specs/stage7.md`。

## 数据文件（`data/`，唯一事实来源，只读）

| 文件 | 内容 | 来源阶段 |
|---|---|---|
| `books.json` | 12 本书、单元（含标题、页码范围、课时列表） | Stage 1 |
| `lessons.json` | 课时：标题、引入/练习的知识点、概括 | Stage 1 |
| `knowledge_points.json` | 规范知识点：名称、别名、领域/主题/主线、描述、引入课时与复现课时、`grants`（能力增量）、典型错误 | Stage 2（Stage 5 补全） |
| `kp_local_map.json` | 各书局部知识点 → 规范知识点的映射 | Stage 2 |
| `edges_relations.json` | 关系边：`prerequisite` / `builds_on` / `related` / `confusable`，带证据与 `is_direct` | Stage 4（Stage 5 约简） |
| `edges_extends.json` | 螺旋扩展边（窄→宽） | Stage 2 |
| `exercises.json` | 习题实例（含原文，仅供标注参照，生成环节不得照抄） | Stage 1 |
| `archetypes.json` | 题型卡片 | Stage 3 |
| `contexts.json` | 情境库 | Stage 3 |
| `glossary.json` | 术语、记号、题干措辞 | Stage 3 |
| `judgments/` | 语言模型判断的审计记录 | Stage 2–4 |
| `stage4_candidates.json` | 前置关系候选对（中间产物） | Stage 4 |

模型定义在 `curriculum/models.py`（`KnowledgePoint`、`ItemArchetype` 等），导出的 JSON Schema 在 `schema/`。接口只依赖这些模型里的稳定字段；缺失的可选文件（如隐含边标记）按"没有"处理。

## 评测与测试

```bash
python -m pytest tests/test_query.py -q             # 接口行为与不变量
python -m curriculum.query.evaluate --split val --errors 5   # 单独跑检索探针，打印最差的 5 条
python -m curriculum.eval --split val               # 全部评测，写 reports/eval.md、追加 reports/eval_history.jsonl
```

`--split val` 是开发集（原 val + 原 test 共 60 条），`--split test` 是新验收集（`eval/probes/retrieval_probes_holdout.md`，只在阶段验收时跑）。重新生成验收集金标：`python -m curriculum.query.holdout_gold`（调用 DashScope，约 4 元）。
