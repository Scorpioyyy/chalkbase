# 评测演进记录

记录评测定义的每次调整：改了什么、为什么更贴合任务。指标定义变更后用新定义重算历史版本，保证可比；不允许为了过线而修改评测。

## 2026-09-27 · Stage 0

- 初始化评测框架：`curriculum/eval.py`（`python -m curriculum.eval` 入口）、`tests/test_invariants.py`（不变量）、`eval/specs/overview.md`（总体评测方案）、`eval/probes/retrieval_probes.md`（60 条检索探针草案）、`eval/label/index.html`（人工核验页）。
- 此时各 stage 的组件指标与下游探针均未实现（`STAGE_METRIC_FUNCS` 为空），`python -m curriculum.eval` 只跑不变量层并生成占位报告。后续每完成一个 Stage 的评测代码，在 `curriculum/eval.py` 注册对应函数，并在本文件记一笔。
