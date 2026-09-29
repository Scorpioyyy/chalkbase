# VeriChalk · 任务书：Stage 2～4（知识点实体消解 → 题型归纳 → 关系推断）

你的上下文只有这个仓库，先把它当成唯一真相来源读一遍，不要凭训练知识猜测项目细节。阅读顺序：

1. `CLAUDE.md` —— 工程原则、评测纪律（**评测先于实现，最高优先级**）、ID 约定、目录结构。这是持续生效的项目规则，不是一次性说明。
2. `KICKOFF.md` —— 第 8、9 节，尤其 **Stage 2 / Stage 3 / Stage 4 的方法描述**，是本任务书的方法依据，本文件不重复展开，只补充执行本任务需要知道的现状与差异。
3. `docs/decisions.md` —— 全部决策记录，从头看到尾。里面记录了本项目所有非显然的判断和踩过的坑，Stage 2~4 会直接用到其中几条（见下文"关键背景"）。
4. `docs/extraction_guide.md`、`docs/seed_threads.md` —— Stage 1 的抽取规则，帮助理解 `work/books/` 里数据的生成逻辑。
5. `eval/specs/overview.md`、`eval/specs/stage1.md` —— 评测规格的既有范式，Stage 2/3/4 的评测规格照此风格续写。
6. `curriculum/models.py` —— 数据模型定义。`curriculum/annotate/client.py` —— 已经写好并实测可用的 DashScope 标注客户端（并发、重试、结构化校验、按内容缓存、费用统计），直接复用，不要重写。
7. `curriculum/eval.py`、`tests/test_invariants.py`、`tests/test_stage1_invariants.py` —— 已有的评测/不变量框架，`STAGE_METRIC_FUNCS` 是你要注册新阶段指标函数的地方。

## 现状：Stage 0、Stage 1 已完成并冻结

12 本教材（g1a～g6b）的书/单元/课时/知识点/习题实例已全部抽取完毕，产物在 `work/books/<book_id>/`：`registry.json`（Book+Unit）、`lessons.json`、`knowledge_points.json`（**本书局部**知识点，未跨书合并）、`exercises.json`、`contexts_observed.json`、`glossary_observed.json`。全库累计 567 课时、466 个局部知识点、4319 条习题实例，`tests/test_stage1_invariants.py` 全部通过。这是你的输入，**只读，不要修改**——除非你在做 Stage 2~4 时发现某条 Stage 1 数据确实是错的（如知识点归错课时、明显的抽取错误），这种情况下可以回去改，但要在 `docs/decisions.md` 记一笔说明改了什么、为什么（本项目原则：下游发现上游问题要回上游修，不在下游打补丁；Stage 0/1 阶段已经有过好几次这样的真实修复案例，可以参考 decisions.md 里的写法）。

`data/` 目前是空的。**从 Stage 2 开始，规范数据应该出现在 `data/` 下**：跨 12 本书合并后的知识点目录（规范 ID）、题型卡片、关系边等，是后续所有阶段与下游模块唯一读取的地方。`work/books/` 保留作为 Stage 1 的原始产物和溯源依据，不再是下游读取源。这是 `CLAUDE.md` 里"单一事实来源"原则第一次真正落地，请按这个架构来，不要继续把跨书合并结果堆在 `work/` 里。

## 关键背景（决定 Stage 4 判断质量的几件事）

- **版本混杂**：`g4b`/`g5b`/`g6b` 是旧版教材（2011 课标），其余 9 本是新版（2022 课标）。这不是本任务要修的问题（那是 Stage 5 的工作），但 Stage 4 做候选生成与前置判定时要如实呈现，不要试图自己"纠正"版本冲突——KICKOFF.md 原文说得很清楚："本阶段输出全部判定为前置的边（含与教学顺序相反的边），不在此处删改；顺序冲突在 Stage 5 统一修复"。
- **新版教材存在系统性内容后移**（`docs/decisions.md` D10、D11，三次独立验证过）：面积后移到五年级、分数运算/折线统计图后移到六年级，等等。做前置关系判定时不要用"传统教材应该更早学过"这种先验假设去否定证据，以 `config/sequence.yaml` 的实际引入顺序和教材内容本身为准。
- **本书局部知识点存在大量合理重复**：这正是 Stage 2 要解决的问题，不是 Stage 1 的错误。例如"小数加减法"在 g4b、g5a 等多本书里各自建了本地 KP，属于预期情况。
- **Stage 1 抽取指南的编号规则**（`docs/extraction_guide.md` §1）会影响 Stage 3 的签名分桶：无编号的批量题合并成 1 条 `ExerciseInstance`，做题型归纳时注意这类实例的 `operand_features` 是"整组的典型特征"而非单个算式的特征。

## 任务

按 `KICKOFF.md` 第 9 节 Stage 2 / Stage 3 / Stage 4 的方法完整实现，遵循 `CLAUDE.md` 第 4 节的评测纪律（每个 Stage 先写 `eval/specs/stageN.md`，跑基线，再实现；金标按 4.3 节的多模型交叉标注流程生产；DashScope 调用按 4.4 节规范，`.cache/` 缓存已经在用，继续用）。具体产物：

- **Stage 2**：`data/` 下的规范知识点目录（合并后，稳定 ID）、局部→规范 ID 映射（需要覆盖全部 12 本书、567 个局部知识点的映射关系，供后续重写 `ExerciseInstance` 的知识点引用）、`Edge(type=extends)` 记录（螺旋扩展关系，来自成对判定的 `extends` 结果）。
- **Stage 3**：`data/` 下的题型卡片（`ItemArchetype`）、情境库（`Context`，汇总 12 本书的 `contexts_observed.json`）、表述规范（`GlossaryEntry`，汇总 `glossary_observed.json`）。`program` 类改写示例必须真正过程序校验，这点没有商量余地。
- **Stage 4**：`data/` 下的前置关系边（`Edge(type=prerequisite/builds_on/related/confusable)`），暂不做传递约简（Stage 5 的工作），保留全部判定结果含冲突边。

## 金标与停顿点

按 `KICKOFF.md` 原文，Stage 3 完成后：用模型组生成前置关系金标、题型粒度打分、检索探针标准答案（连同 Stage 2 的实体消解金标），**在此处停下**，把这四类金标的核验样本一次性整理好（人工核验样本，走 `eval/label/index.html`，样本量与分层抽样按 `CLAUDE.md` 4.3 节），报告给用户，等待人工核验反馈后再继续。不要跳过这个停顿点自己往下做 Stage 5。

停下时的汇报要包含：各阶段的组件指标（对照金标的 P/R/F1 等）、相对基线的提升、金标标注的模型间一致性（kappa/alpha）与费用、核验样本本身（不是全量数据）、遇到的任何需要用户决策的问题。不要把详细的知识点/题型列表整段贴出来。

## DashScope

`DASHSCOPE_API_KEY` 会通过环境变量提供，直接读取即可，不要写入任何文件、日志或提交内容。模型 ID、思考模式行为、价格已在 `docs/decisions.md` D4 记录过一次实测结论（默认开思考模式，必须显式传 `enable_thinking`），标注模型组的选择按 `CLAUDE.md` 4.3.7 独立性原则（流水线判定用的模型不能出现在同任务的标注模型组里），具体组合自行决定并记录理由。

## 收尾

每个 Stage 完成、评测通过后按 `CLAUDE.md` 第 2/7 节清理 `tmp/`、跑 `python -m curriculum.eval`、跑全部 `pytest`，git commit + push（仓库已是公开仓库，正常提交推送即可，不需要额外确认）。commit message 遵循已有历史的风格（说清楚做了什么、关键数字、重要发现），结尾加：

```
Co-Authored-By: Claude <noreply@anthropic.com>
```
