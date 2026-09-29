# VeriChalk 评测报告

最近一次运行：2026-09-29T12:23:43.086942+00:00

## 不变量
- pytest 状态：通过（93 passed in 49.20s）

## 组件指标（金标划分：val）
| Stage | 状态 |
|---|---|
| stage1_extraction | 未实现 |
| stage2_entity_resolution | 已实现 |
| stage3_archetype_induction | 已实现 |
| stage4_relation_inference | 已实现 |
| stage5_reconciliation | 未实现 |
| stage6_capability_boundary | 未实现 |
| stage7_query_interface | 未实现 |

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
| granularity_ok_rate | 0.44 | [0.3333, 0.5525] | 75 | 0.7 | 0.85 | ❌ |
| generation_probe_program_rate | 1.0 | [0.9998, 1.0] | 20600 | None | 1.0 | ✅ |
| singleton_ratio | 0.2482 | 数据下限 0.2482 |  | 0.5994 | 0.15 | ❌ |
| singleton_excess_over_floor | 0.0 |  |  | 0.4223 | 0.05 | ✅ |
| compression | 2.866 | 4319 实例 → 1507 题型 |  | 2.045 | — | — |

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

## 下游探针
| 探针 | 状态 |
|---|---|
| retrieval_probe | 未实现（金标已生成，检索接口待 Stage 7） |
| boundary_probe | 未实现 |
| generation_probe | 已实现：program 类可验证率 1.0 [0.9998, 1.0]（1030 个题型 × 20 次采样） |

## 金标质量

首轮 qwen3.8-flash + deepseek-v4.1-flash 独立盲标，分歧由 qwen3.8-max（思考）仲裁，仲裁置信度 < 0.7 进入人工队列。

| 任务 | 条目数 | Cohen κ | 一致 / 仲裁 / 人工队列 / 失败 | 费用（元） |
|---|---|---|---|---|
| archetype_granularity | 150 | 0.6821 | 125 / 25 / 0 / 0 | 3.0398765 |
| archetype_granularity_baseline | 40 | 0.3585 | 26 / 14 / 0 / 0 | 1.2014860 |
| entity_resolution | 587 | 0.8436 | 539 / 47 / 0 / 1 | 5.5618766 |
| entity_resolution_anchor | 428 | 0.7693 | 372 / 50 / 0 / 6 | 8.0688739 |
| prerequisite | 300 | 0.7317 | 252 / 47 / 1 / 0 | 4.4638476 |
| prerequisite_anchor | 709 | 0.7408 | 597 / 110 / 2 / 0 | 12.5958554 |
| prerequisite_anchor（新验收集） | 405 | 0.5574 | 313 / 91 / 1 / 0 | 10.9668872 |
| retrieval_probe | 60 | 0.2606 | 224 / 171 / 12 / 0 | 7.6573989 |

人工核验：样本已生成（`eval/label/stage3_checkpoint.json`，79 条），待回收结果后补入准确率。

---
历史记录见 `reports/eval_history.jsonl`；指标定义变更记录见 `eval/CHANGELOG.md`。
