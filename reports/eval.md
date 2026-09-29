# VeriChalk 评测报告

最近一次运行：2026-09-29T07:02:07.262051+00:00

## 不变量
- pytest 状态：通过（81 passed, 9 skipped in 0.69s）

## 组件指标（金标划分：val）
| Stage | 状态 |
|---|---|
| stage1_extraction | 未实现 |
| stage2_entity_resolution | 已实现 |
| stage3_archetype_induction | 未实现 |
| stage4_relation_inference | 未实现 |
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

## 下游探针
| 探针 | 状态 |
|---|---|
| retrieval_probe | 未实现 |
| boundary_probe | 未实现 |
| generation_probe | 未实现 |

---
历史记录见 `reports/eval_history.jsonl`；指标定义变更记录见 `eval/CHANGELOG.md`。
