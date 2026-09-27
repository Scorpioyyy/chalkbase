# 决策记录

每条记录：背景 → 决策 → 理由 → （如适用）替代方案与为何放弃。

## 2026-09-27　Stage 0

### D1. `ExerciseInstance.text` 存原文，改写示例仍新写
背景：最初方案（KICKOFF.md 初稿）要求 `ExerciseInstance` 不存教材原文，理由是"标注模型不接触教材原文"。用户修正：取消该限制。
决策：`ExerciseInstance` 增加 `text` 字段存原文（图形部分用文字描述），供标注模型组与人工核对参照；`ItemArchetype.rewritten_examples` 仍由 Agent/模型新写，不得复述或改编某条 `text`。
理由：(1) 标注模型组看到原文能显著提升实体消解、题型粒度等金标质量；(2) 人工核对本来就需要对照原页面（找漏抽/误读），"不接触原文"从来不是核对环节存在的真实理由；(3) 改写示例要作为后续生成的参考范式，必须是新写的、经过模板与参数约束校验的题目，而不是教材原题的存档，这与是否存原文无关。
影响：`data/` 中会包含教材习题原文的结构化摘录。仓库为私有仓库，原文限于单题粒度的文字（不含整页版式/插图本身），风险可控；整页渲染图、OCR 整页原始文本仍严格限制在 `tmp/`，不入库。

### D2. Python 环境用 conda + pip 混装
决策：`conda create -n verichalk python=3.11`，包依赖用 pip 安装（PyMuPDF、pydantic 等在 conda-forge 与 PyPI 都有，PyPI 版本更新更及时），写入 `environment.yml` 的 pip 段。
理由：KICKOFF.md 明确要求 conda 环境；核心科学计算包用 pip 避免 conda-forge 源同步延迟。

### D3. tesseract 可用性
决策：通过 `conda install -c conda-forge tesseract` 装好 tesseract 5.5.3，含 `chi_sim`（简体中文）等 125 种语言包，`environment.yml` 已加入该 conda 依赖。
理由：KICKOFF.md 要求"先拿 2 页比较'渲染后直接看'与 OCR，选更可靠的方式"，需要 tesseract 可用才能做这个对比；conda-forge 上有现成 Windows 二进制，安装顺利，无需用户手动装系统级 tesseract。
后续：Stage 1 试点 g4b 时再做 2 页的直接看 vs OCR 对比，记录结论；预期直接渲染给视觉模型看的方式在数学教材（含公式、图形、方格图）上会更可靠，OCR 更适合做辅助校验或纯文字页的快速定位，但以实测为准。

### D4. DashScope 模型默认开启思考模式
实测（tmp/test_dashscope.py）发现 `qwen3.8-max`、`qwen3.8-flash`、`qwen3.7-plus`、`deepseek-v4.1-flash` 在**不传** `enable_thinking` 时默认仍产生 `reasoning_tokens`（即默认开启思考模式），必须显式传 `enable_thinking: false/true` 才能控制。`curriculum/annotate/client.py` 已在 `AnnotationRequest` 中把 `thinking` 设为必填字段并总是显式传递，避免意外产生思考开销。

### D5. 教材版本边界与 KICKOFF.md 初始假设不符（重要，影响 Stage 5 范围）
KICKOFF.md 原假设"上册新版、下册旧版"。Stage 0 逐本核对封面审核章与后记文字后发现：**新版（2022 课标，国家教材委员会专家委员会审核通过 2024）覆盖 `g1a g1b g2a g2b g3a g3b g4a g5a g6a` 共 9 本；旧版（2011 课标，教育部审定 2013）只有 `g4b g5b g6b` 3 本**。已同步修正 KICKOFF.md 第 5 节。
理由：这与教材"新课标逐年级滚动换版、目前仅四至六年级下册尚未跟进"的现实一致，用户只能获取现有出版物，无法自行补齐。
影响：Stage 5 的版本对齐工作量集中在 `g4b/g5b/g6b` 与其余 9 本之间，而非原先设想的"上下册各 6 本"均等对齐；这 3 本旧版下册在能力边界与知识点顺序上可能与前后相邻的新版形成更集中的冲突，需重点检查 `g3b→g4a→g4b→g5a` 与 `g4b→g5a`、`g5b→g6a` 等跨版本衔接处。
