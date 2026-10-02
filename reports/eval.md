# VeriChalk 评测报告

最近一次运行：2026-10-02T17:27:24.366885+00:00

## 不变量
- pytest 状态：**未通过**（3 failed, 140 passed, 1 xfailed, 1 xpassed in 135.42s (0:02:15)）

## 组件指标（金标划分：val）
| Stage | 状态 |
|---|---|
| stage1_extraction | 未实现 |
| stage2_entity_resolution | 已实现 |
| stage3_archetype_induction | 已实现 |
| stage4_relation_inference | 已实现 |
| stage5_reconciliation | 已实现 |
| stage6_capability_boundary | 已实现 |
| stage7_query_interface | 已实现 |

### stage2_entity_resolution
| 指标 | 当前 | 95% CI | n | 基线 | 阈值 | 是否达标 |
|---|---|---|---|---|---|---|
| blocking_recall_same | 1.0 | [0.7847, 1.0] | 14 | 0.5 | 0.95 | ✅ |
| pair_same_f1 | 0.9362 |  |  | 0.3704 | 0.85 | ✅ |
| pair_same_precision | 0.88 | [0.7004, 0.9583] | 25 | 1.0 | — | — |
| pair_same_recall | 1.0 | [0.8513, 1.0] | 22 | 0.2273 | — | — |
| extends_as_same_rate | 0.0366 | [0.0125, 0.1021] | 82 | 0.0 | 0.05 | ✅ |
| macro_f1_3class | 0.9103 |  |  | 0.5818 | — | — |
| bcubed_f1 | 0.9794 | P=0.9718 R=0.9872 |  | 0.9737 | 0.85 | ✅ |
| bcubed_f1_nontrivial | 0.9156 | n=16 P=0.8854 R=0.9479（仅金标簇或预测簇大小≥2 的锚点） |  | 0.8529 | — | — |

主要失败类别：扩展漏判为不同 16；不同误判为扩展 4；扩展误判为同一 3

### stage3_archetype_induction
| 指标 | 当前 | 95% CI | n | 基线 | 阈值 | 是否达标 |
|---|---|---|---|---|---|---|
| granularity_ok_rate | 0.44 | [0.3333, 0.5525] | 75 | 0.5946 | 0.8 | ❌ |
| granularity_too_fine_rate | 0.0267 | 报告：过细占比 |  | 0.0541 | — | — |
| granularity_too_coarse_rate | 0.5333 | 报告：过粗占比 |  | 0.3514 | — | — |
| generation_probe_program_rate | 1.0 | [0.9998, 1.0] | 20600 | None | 1.0 | ✅ |
| singleton_ratio | 0.2482 | 原口径仅报告（原阈值 0.15）；数据下限 0.2482 |  | 0.5994 | — | — |
| singleton_excess_over_floor | 0.0 |  |  | 0.4223 | 0.05 | ✅ |
| compression | 2.866 | 4319 实例 → 1507 题型 |  | 2.045 | 2.0 | ✅ |

### stage4_relation_inference
| 指标 | 当前 | 95% CI | n | 基线 | 阈值 | 是否达标 |
|---|---|---|---|---|---|---|
| candidate_recall | 0.9732 | [0.9457, 0.9869] | 261 | None | 0.95 | ✅ |
| closure_f1 | 0.8372 |  |  | 0.6732 | 0.8 | ✅ |
| closure_precision | 0.9474 | [0.8271, 0.9854] | 38 | 0.6415 | — | — |
| closure_recall | 0.75 | [0.6122, 0.8508] | 48 | 0.7083 | — | — |
| anchor_closure_recall | 0.682 | [0.6232, 0.7355] | 261 | 0.3793 | — | — |
| judgment_macro_f1_5class | 0.6352 |  |  | None | — | — |

主要失败类别：跨主线前置漏判 7；同主线前置漏判 5；builds_on/prerequisite 边界 2

### stage5_reconciliation
| 指标 | 当前 | 95% CI | n | 基线 | 阈值 | 是否达标 |
|---|---|---|---|---|---|---|
| order_violations | 0 |  |  | 11 | 0 | ✅ |
| prerequisite_cycles | 0 |  |  | None | 0 | ✅ |
| invariants_violated | 1 | coverage_refs_valid |  | None | 0 | ❌ |
| standard_coverage | 1.0 | [0.9719, 1.0] | 133 | 0.8195 | 1.0 | ✅ |
| reconciled_review_pass_rate | 0.982 | [0.9367, 0.995] | 111 | 0.8082 | 0.9 | ✅ |
| d14_seed_gaps_found | 2 |  |  | 0 | 2 | ✅ |
| moved_introductions | 0 |  |  | None | — | — |
| gap_kps_added | 14 |  |  | 0 | — | — |
| stage4_final_closure_f1 | 0.8372 |  |  | 0.8372 | 0.8172 | ✅ |
| stage4_final_closure_precision | 0.9474 | [0.8271, 0.9854] | 38 | 0.9474 | — | — |
| stage4_final_closure_recall | 0.75 | [0.6122, 0.8508] | 48 | 0.75 | — | — |

### stage6_capability_boundary
| 指标 | 当前 | 95% CI | n | 基线 | 阈值 | 是否达标 |
|---|---|---|---|---|---|---|
| 教材实例在所在课时边界内的比例（补全+修复后） | 1.0 | [0.9991, 1.0] | 4319 | 0.6972 | 1.0 | ✅ |
| 教材实例覆盖率（仅语义补全，未修复） | 0.9539 | [0.9473, 0.9598] | 4319 | 0.6972 | — | — |
| 生成探针边界通过率（program 类 × 20 采样） | 0.9993 | [0.9989, 0.9996] | 20600 | None | 0.98 | ✅ |
| 越界探针·链路A（标准特征）查准 | 0.7826 | [0.6443, 0.8774] | 46 | 0.8571 | 0.95 | ❌ |
| 越界探针·链路A（标准特征）查全 | 0.8182 | [0.6804, 0.9049] | 44 | 0.1364 | 0.95 | ❌ |
| 越界探针·链路A（构造意图与金标一致的子集）查准 | 0.9722 | [0.8583, 0.9951] | 36 | 0.8571 | 0.95 | ✅ |
| 越界探针·链路A（构造意图与金标一致的子集）查全 | 0.8333 | [0.694, 0.9168] | 42 | 0.1364 | 0.95 | ❌ |
| 越界探针·链路B（题面抽特征，端到端）查准 | 0.6964 | [0.5666, 0.801] | 56 | 0.8571 | 0.95 | — |
| 越界探针·链路B（题面抽特征，端到端）查全 | 0.8864 | [0.7602, 0.9505] | 44 | 0.1364 | 0.95 | — |
越界探针金标 113 条（{'in': 69, 'out': 44}）；构造意图与金标一致率 0.8407；仲裁失败/人工队列 2 条。
容差口径（紧邻 2 课时内才引入的能力不计为越界误报）与按维度结果见 `work/stage6/probe_eval_val.json`。

### stage7_query_interface
| 指标 | 当前 | 95% CI | n | 基线 | 阈值 | 是否达标 |
|---|---|---|---|---|---|---|
| recall@5（封顶） | 0.8271 | [0.7508, 0.8958] | 59 | 0.6404 | 0.85 | ❌ |
| MRR | 0.8786 | [0.808, 0.9412] | 59 | 0.6698 | 0.7 | ✅ |
| hit@5 | 0.9661 | [0.8846, 0.9907] | 59 | 0.8644 | — | — |
| recall@10（封顶） | 0.898 | [0.8435, 0.9452] | 59 | 0.7566 | — | — |

检索探针（val，n=59；recall 为封顶口径 |top-k∩core|/min(|core|,k)，CI：recall/MRR 为 bootstrap，hit@5 为 Wilson；排除 D14 三条（p08/p35/p47）后 recall@5 0.8345、MRR 0.89；D14 三条首个核心命中名次：{'p35': {'first_rank': 1, 'recall@5': 0.667}, 'p47': {'first_rank': 2, 'recall@5': 1.0}, 'p08': {'first_rank': 2, 'recall@5': 0.4}}）。

val 是开发集（含已用于调参与错误分析的探针），不作验收；验收以 `--split test`（新验收集）为准，结果见 `eval/specs/stage7.md` 5.3。

| 探针类型 | n | recall@5 | MRR | 基线 recall@5 | 基线 MRR |
|---|---|---|---|---|---|
| context | 13 | 0.9487 | 1.0 | 0.7115 | 0.6859 |
| cross_unit | 11 | 0.7818 | 0.8409 | 0.4909 | 0.5391 |
| difficulty | 8 | 0.975 | 1.0 | 0.8833 | 0.875 |
| spiral | 7 | 0.569 | 0.7587 | 0.3 | 0.489 |
| variant | 20 | 0.8042 | 0.8139 | 0.6983 | 0.7125 |

## 下游探针
| 探针 | 状态 |
|---|---|
| retrieval_probe | 已实现（recall@5 0.8271（基线 0.6404）、MRR 0.8786（基线 0.6698），n=59，详见 Stage 7 一节） |
| boundary_probe | 已实现（指标见上方 stage6_capability_boundary；生成探针的边界通过率由 `python -m curriculum.boundary gen-probe` 产出） |
| generation_probe | 已实现：program 类可验证率 1.0 [0.9998, 1.0]（1030 个题型 × 20 次采样） |

## 金标质量

首轮 qwen3.8-flash + deepseek-v4.1-flash 独立盲标，分歧由 qwen3.8-max（思考）仲裁，仲裁置信度 < 0.7 进入人工队列。

| 任务 | 条目数 | Cohen κ | 一致 / 仲裁 / 人工队列 / 失败 | 费用（元） |
|---|---|---|---|---|
| archetype_granularity | 150 | 0.6821 | 125 / 25 / 0 / 0 | 3.0398765 |
| archetype_granularity_baseline | 40 | 0.6242 | 33 / 6 / 0 / 1 | 0.6643867 |
| archetype_granularity_oldscheme | 150 | 0.7261 | 130 / 19 / 0 / 1 | 2.1124658 |
| boundary_probe | 240 | 0.6388 | 198 / 35 / 0 / 7 | 4.1142920 |
| entity_resolution | 587 | 0.8436 | 539 / 47 / 0 / 1 | 5.5618766 |
| entity_resolution_anchor | 428 | 0.7693 | 372 / 50 / 0 / 6 | 8.0688739 |
| prerequisite | 300 | 0.7317 | 252 / 47 / 1 / 0 | 4.4638476 |
| prerequisite_anchor | 709 | 0.7408 | 597 / 110 / 2 / 0 | 12.5958554 |
| prerequisite_anchor（新验收集） | 405 | 0.5574 | 313 / 91 / 1 / 0 | 10.9668872 |
| reconciliation | 111 | -0.0123 | 107 / 2 / 0 / 2 | 0.3486832 |
| reconciliation | 146 | 0.5357 | 128 / 17 / 0 / 1 | 1.1874443 |
| retrieval_probe | 60 | 0.2606 | 224 / 171 / 12 / 0 | 7.6573989 |
| retrieval_probe_holdout（新验收集） | 30 | 0.1116 | 92 / 120 / 4 / 0 | 4.5312681 |

人工核验估计的金标准确率（Wilson 95% CI）：

- pr：1.0 [0.8389, 1.0]（n=20）
- er：1.0 [0.862, 1.0]（n=24）
- rp：1.0 [0.6756, 1.0]（n=8）
- ag：0.8667 [0.6212, 0.9626]（n=15）
- era：1.0 [0.7225, 1.0]（n=10）

---
历史记录见 `reports/eval_history.jsonl`；指标定义变更记录见 `eval/CHANGELOG.md`。
