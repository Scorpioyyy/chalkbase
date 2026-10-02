# Stage 5 进度（版本对齐、补全与约简）

状态：**代码、数据、测试、报告已完成并自洽**；未提交（由主 agent 统一提交）。

## 已完成
- 评测规格 `eval/specs/stage5.md`；代码 `curriculum/stage5/`（discover / standard / conflicts / gaps / reconcile / review / difficulty / evaluate / report / build）。
- 数据：`data/knowledge_points.json`（419 → 433，补全 14）、`data/lessons.json`、`data/edges_relations.json`（3751 条前置边，直接 852、隐含 2899）、`data/stage5_reconciliation.json`（修复日志）、`data/gap_kps_pending_cards.json`（14 个待生成卡片的缺口）、`data/curriculum_coverage.json`（课标 133 条：124 覆盖 + 9 不适用）。
- 判定记录：`data/judgments/stage5_*`；审阅：`eval/annotation/reconciliation/`（首轮 *_round1.*，末轮 stats.json）。
- 报告：`reports/editions.md`；决策 D22–D25；`eval/CHANGELOG.md` 追加；`curriculum/eval.py` 注册 `stage5_reconciliation`。
- 既有测试调整：Stage 2（两处）、Stage 4（一处）；Stage 3 沙箱 / eval.py 子进程 UTF-8。
- 当前数字：逆序前置边 11 → 0（前移 0，取消前置 11）；环 0；约简保持可达；课标覆盖 100%；审阅通过率首轮 0.808 → 末轮 0.982（109/111）；Stage 4 闭包 F1 val 0.8372 / test 0.8283，与修复前完全一致。

## 未做 / 待主 agent 触发
1. 14 个缺口知识点的题型卡片（见 reports/editions.md 第 7 节）。
2. 题型难度重算：`python -m curriculum.stage5 difficulty`（只读 archetypes.json；当前 0 个题型需要变化，Stage 3 重生成后若引入位置不变则无需 `--write`；变化时用 `--write`）。
3. 最终停顿点用户抽查：被核实事实推翻审阅否决的缺口（长方形/正方形面积、分数的意义、面积单位）；Agent 复核定案的 2 个课标条目（`curriculum/stage5/standard.py`）。
4. 2 条审阅仲裁调用因欠费中断失败未补跑（即上述被推翻否决的缺口，不影响数据）。
5. 其他 agent 的 Stage 6 派生量（tests/test_stage6_invariants.py::test_not_yet_learned_partition 此前基于 419 个知识点）需基于新的 433 个知识点重算。

## 续跑命令（均命中 .cache/，重跑免费）
```
python -m curriculum.stage5                 # 确定性修复，幂等
python -m curriculum.stage5 report          # 重新生成 reports/editions.md
# 从原始状态完整重建：
git checkout data/knowledge_points.json data/lessons.json data/edges_relations.json && rm data/stage5_reconciliation.json && python -m curriculum.stage5
# 需要模型的步骤：discover | standard pre|post | triage | consolidate | curate | link | adjudicate | review
```

## 费用 / 耗时
约 ¥30（缺口发现 ¥9.3，课标覆盖 pre/post/终审 ¥8 左右，缺口汇总/审查/链接 ¥6 左右，审阅四轮 ¥2.5 左右）；墙钟约 1 小时 20 分钟。
