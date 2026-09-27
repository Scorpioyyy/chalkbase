# 评测演进记录

记录评测定义的每次调整：改了什么、为什么更贴合任务。指标定义变更后用新定义重算历史版本，保证可比；不允许为了过线而修改评测。

## 2026-09-27 · Stage 0

- 初始化评测框架：`curriculum/eval.py`（`python -m curriculum.eval` 入口）、`tests/test_invariants.py`（不变量）、`eval/specs/overview.md`（总体评测方案）、`eval/probes/retrieval_probes.md`（60 条检索探针草案）、`eval/label/index.html`（人工核验页）。
- 此时各 stage 的组件指标与下游探针均未实现（`STAGE_METRIC_FUNCS` 为空），`python -m curriculum.eval` 只跑不变量层并生成占位报告。后续每完成一个 Stage 的评测代码，在 `curriculum/eval.py` 注册对应函数，并在本文件记一笔。

## 2026-09-28 · Stage 1 试点（g4a/g4b）

- 修正不变量"每个课时至少引入 1 个知识点"为"每个课时 `intro_knowledge_point_ids` 或 `practice_knowledge_point_ids` 至少一个非空"。原表述基于假设"每课都在教新东西"，但 g4a/g4b 试点抽取发现"整理与复习""练习N"类课时是真实存在且合理的教材结构——它们只复现/练习已有知识点，不引入新知识点。用旧表述会把这类正确课时误判为"漏抽"，与任务目标不符，故放宽为二选一非空（仍然禁止一个知识点都不挂的空课时）。
