# Stage 3 题型粒度返工：进度（2026-10-03，用户额度告急，阶段性收工）

## 已完成
- 粒度指南 v2（`eval/annotation/archetype_granularity/guideline.md`）；CHANGELOG 与 D26 已记录；`eval/specs/stage3.md` 已更新（§3 分组方法、§4 指标、§5 数据、§6 基线、§7 结果）。
- 新指南下重标旧方案（`archetype_granularity_oldscheme`）与基线 B（`archetype_granularity_baseline`）。
- v2 分组：`curriculum/stage3/partition.py`（805 次模型调用已全部缓存在 `.cache/`，重跑免费），2007 个题型（压缩率 2.15，单实例 1041）。
- v2 分组的粒度金标（`eval/gold/{val,test}/archetype_granularity.jsonl`，指南 v2，ID 按 build 同规则预先算出）：val 0.892 [0.801, 0.944]，test 0.863 [0.766, 0.924]；v1 旧方案同指南下 val 0.595 / test 0.613。
- `curriculum/stage3/build.py` 已改为增量重生成（实例集合不变的卡片复用 `data/archetypes.json`；情境库、glossary 沿用已落盘文件）；`tests/test_stage3_invariants.py` 新增「题型内实例同主知识点同形式」不变量（对 v1 与 v2 都成立，已通过）。

## 未完成（卡片生成被中止）
- `python -m curriculum.stage3` 跑到约 67%（921/1377 张新卡片）时被手动中止，**`data/archetypes.json` 仍是 v1（一致、可用）**，没有半新半旧的中间态。`data/judgments/stage3_partition.jsonl` 已是新的（v2 分组的判断记录）。
- 中间有一次不到 5 分钟的欠费中断：失败调用不写缓存，build 里这些轮次记为失败；续跑时这些调用会被重新请求（已成功的轮次命中缓存，免费），无需特殊处理。中止前统计的 68 个「失败」里有一部分是欠费造成的，续跑后应明显减少。
- 注意：`eval/gold/*/archetype_granularity.jsonl` 已是 v2 分组的金标，而 `data/archetypes.json` 在续跑完成前仍是 v1，此时跑 `python -m curriculum.eval` 的粒度指标是 v2 的、单实例/压缩率是 v1 的，不一致，**请在续跑完成后再看**。

## 续跑命令（按序）
1. `cd C:\Users\22418\Desktop\verichalk; $env:PYTHONPATH="."; python -m curriculum.stage3`（partition 与已完成卡片命中缓存；约 456 张新卡片，预计 10–20 分钟、约 15–20 元；完成后写 `data/archetypes.json` 并输出 summary，应为 n_archetypes=2007、n_reused≈630）。
2. 核对：`python -m pytest tests/test_stage3_invariants.py -q`；确认题型 ID 与金标里的 archetype_id 一致（二者按同一规则生成：知识点 slug + 分组顺序序号；若 Stage 5 改了知识点集合导致 slug 或分组变化，则金标 ID 对不上，需重跑 `python -m curriculum.stage3.gold granularity`，约 2 元）。
3. `python -m curriculum.eval`（更新 `reports/eval.md`）；程序可验证率 / 重算一致率必须 = 1.0。
4. 卡片降级为 human 的数量若偏高（中止前 68/921），查看 `data/judgments/stage3_generation_failures.json` 的错误归类；若主要是 API 故障则已在续跑中消除。

## 数字与花费
- 本任务 API 花费约 45 元（旧方案重标 2.7、dev 评测 2.7、细分 3 轮约 5.5、v2 金标 2.0、卡片生成已完成部分约 30）；耗时约 1 小时 45 分（含等待）。
- 遗留决策：单实例超额占比 0.332（门槛 ≤ 0.05，不通过），见 D26；人工核验未做。
