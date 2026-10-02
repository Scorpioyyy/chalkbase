# Stage 6 进度（2026-10-03，因额度阶段性收工）

## 已完成
- 评测规格 `eval/specs/stage6.md`；决策 D21（`docs/decisions.md`）；`eval/CHANGELOG.md` 已记。
- 代码 `curriculum/boundary/`：`vocab.py`（受控词表与确定性形态推导）、`fold.py`（纯函数半格折叠）、`mine.py`（教材实例特征挖掘）、`check.py`（`boundary()` / `check_item()` / `not_yet_learned()`，Stage 7 可直接调用；`check_item` 接受 `ItemFeatures` 或 dict，返回 `BoundaryReport`）、`enrich.py`（grants 补全）、`build.py`（折叠 + 写回）、`extract.py`（题面 → 特征）、`probe.py`（越界探针）、`genprobe.py`（生成探针边界通过率）、`evaluate.py`（注册到 `curriculum/eval.py` 的 `stage6_capability_boundary`）。
- 新增模型类（`curriculum/models.py` 末尾）：`OperationUse`、`ItemFeatures`、`BoundaryViolation`、`BoundaryReport`。
- 产物：`work/stage6/{grants_enriched,repairs,enrich_report,gen_probe,probe_eval_val,probe_eval_test}.json`、`data/boundaries.json`（增量格式，约 0.18MB）、`data/judgments/stage6_grants.jsonl`、`eval/annotation/boundary_probe/`、`eval/gold/{val,test}/boundary_probe.jsonl`。
- 测试 `tests/test_stage6_invariants.py`：8 通过 + 1 xfail（23 个实例早于其主知识点的引入课时，等 Stage 5；Stage 5 完成后把常量 `KNOWN_PENDING_STAGE5_ORDER_VIOLATIONS` 改为 0）。

## 当前数字（基于 data/ 当前状态：433 个知识点）
- grants 覆盖率（非空知识点数 / 433）：整数上限 11→75，小数位数 3→20，分数类型 5→4（草稿里有 5 个来自 Stage 5 新增知识点），运算形态 55→86，概念 361→427，计量单位 17→71，几何词汇 30→128。
- 教材实例（4319）在所在课时边界内：Stage 1 草稿 grants 3019（69.9%）；仅语义补全 4132（95.7%）；修复后 4319（100%），修复 54 条（`work/stage6/repairs.json`）。Stage 1 抽取错误 1 例（身份证号当成 18 位整数，豁免）。
- 越界探针（严格口径，金标 val 113 / test 120）：链路 A（标准特征）查准/查全 val 0.78/0.82、test 0.80/0.78；意图一致子集查准 0.97/0.95、查全 0.83/0.83；链路 B（题面抽特征）val 0.71/0.89（test 0.72/0.86，用的是改提示词之前的抽取）；基线（只验整数数域+小数位数）查全 0.14。**均未达 0.95**，原因见 D21 第 6 点（金标噪声、标准特征漏列概念、课时粒度模糊、上游缺分数基础知识点）。
- 生成探针边界通过率（1030 个 program 类题型 × 20 采样）：0.9998（阈值 0.98，达标）。
- 费用约 17 元（grants 补全 2、构造 2×2、标注 3 轮共约 11、抽取 <1）；墙钟约 1.5 小时。

## 没做完 / 需要续跑
1. **7 条越界探针的仲裁失败**（`bp.integer_domain.g5a.u2.l02.in` 等，全是 `qwen3.8-max` 思考模式连接错误，疑似欠费中断）：金标里 `source=failed`，不计入指标。标注上下文包含知识点清单，Stage 5 改动后提示词会变、缓存全部失效，所以**不单补这 7 条，等 Stage 5 定稿后统一重标**。
2. Stage 5 / Stage 3 定稿后的重跑（顺序）：
   - `python -m curriculum.boundary enrich`（语义补全按知识点缓存，只为新/改动的知识点调用 API）
   - `python -m curriculum.boundary build`（读最新 data/ 折叠，写 data/boundaries.json）
   - `python -m curriculum.boundary apply`（把补全后的 grants 写回 data/knowledge_points.json，需 Stage 5 不再改该文件时执行）
   - `python -m curriculum.boundary gen-probe`（Stage 3 的 archetypes.json 变化后重测边界通过率；Windows 下已用 UTF-8 字节收发，绕开 Stage 3 沙箱的 gbk 问题）
   - `python -m curriculum.boundary probe-label`（Stage 5 后重标金标，约 4 元，注意：提示词含知识点清单，会全量重新调用）→ `python -m curriculum.boundary probe-eval --split val`，验收时 `--split test`
   - `python -m pytest tests/test_stage6_invariants.py`，`python -m curriculum.eval`
3. 越界探针金标需最终停顿点抽样人工核验（约 40 条）；仲裁失败的 7 条与「意图与金标不一致」的条目优先。
4. 注意：`probe-build` 会重新生成题目（含上下文，温度 0.7，缓存失效就会产生不同题目），**不要再跑**，除非要整体重建探针集（会使现有金标作废）。

## 对 Stage 7 / 主 agent 的提示
- `curriculum/query/store.py` 的 `boundary()` / `check_item()` 占位可以直接转调 `curriculum.boundary.boundary` / `check_item`（`check_item` 返回 `BoundaryReport`，`.violations[*].dimension` 即越界维度列表）。
- 12 本教材缺「分数的初步认识、真分数假分数、约分通分」等基础知识点（D11/D14），Stage 5 补全后分数维度才完整。
