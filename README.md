# VeriChalk · 小学数学课程知识库

面向小学数学教师的「可验证命题 Agent」的**知识底座**：把北师大版小学数学 12 册教材（一～六年级上下册）压缩成一个可程序化调用的课程知识库——知识点图谱、题型卡片、情境库、能力边界、查询接口。后续的意图解析、检索与生成、验证闭环、组卷、螺旋复习只需读取 `data/` 与 `curriculum` 接口，不再需要打开教材 PDF。

项目的工程原则、评测纪律与目录约定见 [CLAUDE.md](CLAUDE.md)，最初的任务书见 [KICKOFF.md](KICKOFF.md)。

## 现状（Stage 0–8 完成，Stage 9 可视化未做）

| 内容 | 数量 |
|---|---|
| 教材册数 / 单元 / 课时 | 12 / 130 / 567 |
| 规范知识点（教材 419 + 补全 14） | 433 |
| 习题实例（含原文）| 4319 |
| 题型卡片（教材 2009 + 补全 49） | 2058 |
| 关系边 | 前置 3751（直接 852 + 隐含 2899）、递进 1851、相关 113、易混淆 2、螺旋扩展 282 |
| 能力边界 | 567 个课时，7 个维度 |

评测结果、基线对比与已知局限见 [reports/eval.md](reports/eval.md)，各书/各阶段的数量统计见 [reports/stats.md](reports/stats.md)，版本冲突与缺口补全的明细见 [reports/editions.md](reports/editions.md)。金标由多模型交叉标注生成，**尚未经过完整的人工核验**（见 eval.md「已知局限」）。

## 使用

```bash
conda activate verichalk            # 环境见 environment.yml
python -m curriculum search "三年级两位数乘一位数的竖式"
python -m curriculum chain <知识点ID> --depth 3
python -m curriculum learned <课时ID>
python -m curriculum boundary <课时ID>
```

```python
from curriculum import Curriculum
cur = Curriculum()
cur.search("小数加减法", k=5, grade=4)
cur.chain("kp.na.小数加减法.…", direction="prerequisite", depth=2)
cur.boundary("g4a.u3.l02")          # 该课时的能力边界
cur.check_item(features, "g4a.u3.l02")   # 判断一道题是否超纲，以及超在哪一维
```

接口细节见 [curriculum/README.md](curriculum/README.md)。

## 流水线

抽取观测 → 知识点实体消解 → 题型归纳 → 关系推断 → 版本对齐/补全/约简 → 能力边界推导 → 查询接口 → 端到端评测。每一步只消费上一步冻结的产物，评测先于实现（`eval/specs/stageN.md`）。

| 阶段 | 入口 | 产物 |
|---|---|---|
| 1 抽取 | （子 agent 逐册抽取，规则见 `docs/extraction_guide.md`） | `work/books/<book>/` |
| 2 实体消解 | `python -m curriculum.stage2` | `data/knowledge_points.json`、`kp_local_map.json`、`edges_extends.json` |
| 3 题型归纳 | `python -m curriculum.stage3` | `data/archetypes.json`、`contexts.json`、`glossary.json` |
| 4 关系推断 | `python -m curriculum.stage4` | `data/edges_relations.json` |
| 5 版本对齐 | `python -m curriculum.stage5` | 更新知识点/边/课时；`data/stage5_reconciliation.json`、`reports/editions.md` |
| 6 能力边界 | `python -m curriculum.boundary enrich → build → apply` | `data/boundaries.json`、知识点 `grants` |
| 7 查询接口 | `python -m curriculum …` | — |
| 8 评测 | `python -m curriculum.eval --split test`、`python scripts/gen_stats.py` | `reports/eval.md`、`reports/stats.md` |

**重跑顺序**：Stage 5 之后必须再跑 Stage 6 的 `apply`（把补全的能力增量写回知识点）；若整体重建 Stage 5，grants 会丢失。DashScope 调用按内容缓存在 `.cache/`（不入库），重跑只为未命中的请求付费。

## 数据与版权

`data/` 是规范数据的唯一事实来源（`data/judgments/` 保存每一次模型判定的输入、结论、置信度与理由，可审计、可重放）；`work/` 是各阶段的观测与中间产物。`textbook/` 下的教材 PDF 不入库。`data/exercises.json` 含单题粒度的习题原文摘录（教学研究用途），整页渲染图与整页 OCR 文本不入库；题型卡片的改写示例均为新写，不是教材原题的复述。

## 环境

Python 3.11（conda 环境 `verichalk`）。调用阿里云百炼（DashScope）需设置环境变量 `DASHSCOPE_API_KEY`（及可选的 `DASHSCOPE_BASE_URL`），**密钥绝不写入文件或提交**。
