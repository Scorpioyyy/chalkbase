# 抽取指南（Stage 1）

本指南规定 Stage 1「抽取观测」的规则：把一本教材的一个单元转成 `chalkbase/models.py` 定义的结构化记录（`Lesson` / `KnowledgePoint` / `ExerciseInstance`，情境与术语观测见文末）。抽取前应先通读本指南与完整样例；遇到指南没覆盖的情况，按本指南的精神判断，并在该书的 `progress.md` 里记录，作为修订指南的依据。

## 1. 基本原则

- **课时标题不是知识点**。北师大版课时常以情境命名（如「走进美丽乡村」「人口普查」「买菜」），这是这节课用的**情境**，知识点要另外从课时内容里提炼。
- **知识点粒度：以「老师会把它当作一个考点来说」为准**。
  - 合适的例子："亿以内数的读法""四舍五入求近似数""小数末尾添 0 去 0 大小不变""小数加减法（需进位/退位）""三角形内角和""三角形三边关系"。
  - 太粗的反例："数的认识"（这是一整个主线，不是一个考点）。
  - 太细的反例："5+3 这种个位不进位的加法"（这是题型/参数范围，不是知识点；应归到"10以内数的加法"这个知识点下，进位与否是题型的操作数特征）。
  - 经验法则：如果一个说法既可以单独出现在试卷的一道题目要求里，又不需要再拆成"先...再..."两步才能描述，通常粒度合适。
- **不要把螺旋复现当成新知识点**，也不要把螺旋扩展误判为同一知识点。判断标准：这节课教的东西，学生如果没学过会不会学不会本课内容？
  - 如果本课只是复习/巩固已经教过的同一件事 → 记为该已有知识点的 `review_lesson_ids`，不新建知识点。
  - 如果本课在原有基础上扩大了范围或引入了新规则（如"万以内数"到"亿以内数"，数位增加、读法规则有新内容） → 新建知识点，用 `Edge(type=builds_on)` 或后续 Stage 4 的前置边连接，不要合并成同一个知识点。
  - 拿不准时倾向于**分开**记录（宁可 Stage 2 合并，不要在这里就抹掉信息）。
  - **明显是低年级已学、但本书内找不到对应知识点可挂靠的内容**（如复习课里出现的"探索规律""图形分类"等，本书没有重新定义它，但也没有其他书的 KP id 可引用）：**在本书内新建一个知识点记录**（当作本书局部的正式知识点，不是临时占位），交给 Stage 2 实体消解去跟其他书的对应知识点合并（D8）。
- **ExerciseInstance 是小题粒度，以「独立编号」为唯一拆分依据**（D7）：
  - 有独立小题号/子编号（(1)(2)(3)、①②③ 等）→ 每个编号拆成 1 条 `ExerciseInstance`。三个小题共享同一组数据、只是问同一件事的不同侧面时也照样分开记录（`text` 里各自写清共享的背景信息），因为它们的操作数特征、知识点组合可能不同。
  - **没有独立编号**，即使同一大题下罗列了多个同类算式/填空目标/判断对错项（如"直接写出得数：12×20=　12×200=　120×20="，或一排"判断对错"小题），**整体记为 1 条 `ExerciseInstance`**，不要按算式/空位再拆分。`text` 完整列出这一组的全部内容（不得省略任何一项），`operand_features` 按这组题的整体/典型特征填写（如涉及的最大位数、是否含进退位），不必逐项枚举。
  - 判断口诀：**看编号，不看内容条数**——有编号就按编号拆，无编号就整体算一条，不用再判断"是否共享同一组数据"或"是否意在展示规律"这类更细的例外。
- **`text` 字段**：原文照抄题干文字；图形/情境不可用文字省略的部分（数轴刻度、方格图、统计图数值、实物计数图的对象与数量），用简洁准确的文字描述补全，让不看原书的人也能凭 `text` 重新出一道等价的题。**不要**在 `text` 里加入解题过程或答案。
- **改写示例（`ItemArchetype.rewritten_examples`）必须新写**，不得复述或改编某条 `ExerciseInstance.text`；它们是校验模板/参数约束是否可用的产物，也是后续生成环节的参考范式。
- **图片信息因渲染精度无法判读**（如算盘珠子数量、统计图具体数值、身份证号码等细小/模糊图形）：如实在 `text` 里注明"未能判读"，**不得编造数值**，答案相关字段留空或注明。默认渲染分辨率（120～150 DPI）已够用，不为个别难以判读的图专门用更高 DPI 重新渲染核实（D9）。

## 2. Lesson 的划分与 summary

- 一个 Lesson 大致对应教材里一个有独立标题、包含"新课讲解 + 练一练/试一试"的教学单元（在目录里通常看不到 Lesson 级别，需要进正文识别标题）。
- `summary` 要求一句话、自己的话概括本课时**教的内容**（不是情境），例如"用图片和实物学习10以内数的点数与序数（第几）"，而不是"走进美丽乡村"。

## 3. 操作数特征粗分桶（`OperandFeatures`）

供 Stage 3 计算题型签名使用，抽取阶段先如实记录观测值，不要主观取整：
- `number_types`：出现的数的类型集合（整数/小数/分数）。
- `integer_digits` / `decimal_places`：该题里最大整数位数/小数位数。
- `requires_carry_or_borrow`：加减法是否需要进位/退位，只在涉及加减法时填。
- `requires_exact_division`：除法是否整除，只在涉及除法时填。
- `operation_steps`：完成本题需要的运算步数（如"先加后减"算2步）。
- 低年级不涉及某个维度的字段留空（`None`），不要填 0 或猜测值。

## 4. 常见坑

- **目录/标题页可能乱码**：有文字层的书，装饰字体的标题（尤其目录页）用 PyMuPDF 抽取可能得到乱码（字体缺 ToUnicode 映射）。乱码不代表没有文字层，只代表**这一页**要改用渲染图核对，其余正文页通常仍可直接抽取文字，抽取前先用一两页验证。
- **印刷页码到 PDF 页序的偏移不保证全书线性**：如 g1b 的偏移中途从 +5 漂移到 +4。不能只在书的开头验证一次偏移就应用到全书，应在每个单元开始附近抽样核对。
- **教材版本**：不能假设"上册=新版、下册=旧版"。一～三年级和四～六年级上册共 9 本是 2022 课标新版，仅四、五、六年级下册 3 本是 2011 课标旧版（见 `docs/design.md` D5）。version 信息以每本书 `work/books/<id>/registry.json` 里已经核实的 `Book.curriculum_standard_year` 为准。

## 5. 完整样例

来源：`g1a`（数学一年级上册）第一单元「生活中的数」第 1 课时「走进美丽乡村」，PDF 第 17～19 页（印刷页码 12～14）。以下为**节选**（完整抽取需覆盖该页全部小题，此处为示范核心模式，略去了部分结构相近的小题）。

### 5.1 Lesson

```json
{
  "id": "g1a.u1.l01",
  "unit_id": "g1a.u1",
  "index": 1,
  "title": "走进美丽乡村",
  "pages": {"pdf_start": 17, "pdf_end": 19, "printed_start": "12", "printed_end": "14"},
  "intro_knowledge_point_ids": ["kp.na.10以内数的认识.count", "kp.na.10以内数的认识.ordinal"],
  "practice_knowledge_point_ids": [],
  "summary": "用乡村情境图和实物计数学习10以内数的点数，并学习用「第几」描述位置（序数）。"
}
```

### 5.2 KnowledgePoint（本课新引入的两个）

```json
[
  {
    "id": "kp.na.10以内数的认识.count",
    "name": "10以内数的点数与认识",
    "aliases": ["数一数", "认数"],
    "domain": "na",
    "topic": "数与运算",
    "thread": "10以内数的认识",
    "description": "能正确点数10以内的物体数量，用基数词表示，建立数与物体数量的一一对应。",
    "mastery_level": "understand",
    "first_introduced_lesson_id": "g1a.u1.l01",
    "review_lesson_ids": [],
    "is_assessable": true,
    "typical_errors": ["漏数或重复数导致计数错误", "点数顺序混乱（未按固定路径点数）"],
    "grants": {"integer_domain_max": 10},
    "provenance": "textbook"
  },
  {
    "id": "kp.na.10以内数的认识.ordinal",
    "name": "序数（第几）",
    "aliases": ["第几", "排第几"],
    "domain": "na",
    "topic": "数与运算",
    "thread": "10以内数的认识",
    "description": "能用序数词描述物体在一列中的位置（第几个），区分「几个」（基数）与「第几个」（序数）的不同含义。",
    "mastery_level": "understand",
    "first_introduced_lesson_id": "g1a.u1.l01",
    "review_lesson_ids": [],
    "is_assessable": true,
    "typical_errors": ["把序数当基数回答", "点数方向不一致导致序数错误（如从右往左数）"],
    "grants": {"concepts": ["序数"]},
    "provenance": "textbook"
  }
]
```

### 5.3 ExerciseInstance（节选 4 条，来自该课「练一练」）

> **关于 ex.01～ex.03 的说明**：下面这三条（大雁/跑步/小熊）在教材原书里是三个并列、没有独立编号的图框，按 §1 的拆分规则（无编号一律合并）应合并为 1 条 `ExerciseInstance`。样例为了展示同一模板下三个不同参数取值而保留了拆分形式，这**不代表可以按内容条数拆分**——正式抽取严格执行 §1 的规则："看编号，不看内容条数"，无编号批量题一律合并为 1 条，`text` 里完整列出全部内容。本节样例仅用于说明 Lesson → KnowledgePoint → ItemArchetype 的整体结构，不作为编号判定的先例。

```json
[
  {
    "id": "ex.g1a.u1.l01.01",
    "lesson_id": "g1a.u1.l01",
    "pdf_page": 19,
    "printed_page": "14",
    "item_form": "word_problem",
    "primary_knowledge_point_id": "kp.na.10以内数的认识.ordinal",
    "secondary_knowledge_point_ids": ["kp.na.10以内数的认识.count"],
    "operand_features": {"number_types": ["整数"], "integer_digits": 1},
    "answer_form": "multi_blank",
    "requires_figure": true,
    "figure_type": "实物排列计数图（一排大雁，其中一只用图标特别标出）",
    "context_theme": "动物观察",
    "summary": "数大雁只数并指出被标出的那只排第几",
    "text": "图中画着一排大雁（共5只，其中一只用剪影单独标出），提问：有几只大雁？第几只是这只（剪影标出的）大雁？"
  },
  {
    "id": "ex.g1a.u1.l01.02",
    "lesson_id": "g1a.u1.l01",
    "pdf_page": 19,
    "printed_page": "14",
    "item_form": "word_problem",
    "primary_knowledge_point_id": "kp.na.10以内数的认识.ordinal",
    "secondary_knowledge_point_ids": ["kp.na.10以内数的认识.count"],
    "operand_features": {"number_types": ["整数"], "integer_digits": 1},
    "answer_form": "multi_blank",
    "requires_figure": true,
    "figure_type": "实物排列计数图（跑道上4名跑步的学生，其中一人用图标标出）",
    "context_theme": "体育运动",
    "summary": "数跑步人数并指出被标出的同学排第几",
    "text": "图中4名小朋友在跑道上跑步，其中一人用图标单独标出，提问：有几人？这名小朋友排第几？"
  },
  {
    "id": "ex.g1a.u1.l01.03",
    "lesson_id": "g1a.u1.l01",
    "pdf_page": 19,
    "printed_page": "14",
    "item_form": "word_problem",
    "primary_knowledge_point_id": "kp.na.10以内数的认识.ordinal",
    "secondary_knowledge_point_ids": ["kp.na.10以内数的认识.count"],
    "operand_features": {"number_types": ["整数"], "integer_digits": 1},
    "answer_form": "multi_blank",
    "requires_figure": true,
    "figure_type": "实物排列计数图（竖杆上挂着若干只小熊玩偶，其中一只用图标标出）",
    "context_theme": "游戏玩具",
    "summary": "数小熊只数并指出被标出的那只排第几",
    "text": "一根竖杆上挂着若干只小熊玩偶，其中一只用图标单独标出，提问：有几只小熊？第几只是这只小熊？"
  },
  {
    "id": "ex.g1a.u1.l01.04",
    "lesson_id": "g1a.u1.l01",
    "pdf_page": 19,
    "printed_page": "14",
    "item_form": "fill_blank",
    "primary_knowledge_point_id": "kp.na.10以内数的认识.count",
    "secondary_knowledge_point_ids": [],
    "operand_features": {"number_types": ["整数"], "integer_digits": 1},
    "answer_form": "multi_blank",
    "requires_figure": true,
    "figure_type": "多组实物计数图与数字卡片连线",
    "context_theme": null,
    "summary": "数出6组物体数量并与对应数字1~10连线",
    "text": "图中给出6组物体（鸟、飞机、风车、圆圈等，数量分别覆盖1～10且顺序打乱），下方排列数字卡片1～10。要求数一数每组物体的数量，把物体与对应的数字用线连起来。"
  }
]
```

### 5.4 ItemArchetype（由 ex.01～ex.03 归纳得到）

```json
{
  "id": "at.10以内数的认识.ordinal.01",
  "primary_knowledge_point_id": "kp.na.10以内数的认识.ordinal",
  "secondary_knowledge_point_ids": ["kp.na.10以内数的认识.count"],
  "item_form": "word_problem",
  "template": "图中有一排{context_object}，共{total}个，其中第{target_position}个用特殊标记标出。问：一共有几个{context_object}？标记的是第几个？",
  "parameter_constraints": {
    "total": {"type": "int", "min": 3, "max": 10},
    "target_position": {"type": "int", "min": 1, "max": "total"},
    "context_object": ["大雁", "跑步的同学", "小熊", "小鸭"]
  },
  "answer_form": "multi_blank",
  "solution_steps": ["从左到右依次点数，得到物体总数", "从左到右数到被标记的物体，其序号即为答案"],
  "allowed_contexts": ["动物观察", "校园活动"],
  "figure_types": ["实物排列计数图"],
  "verifiable_type": "program",
  "difficulty": 1,
  "difficulty_features": {"solution_steps": 2, "kp_count": 2, "position_span": 0, "reverse_thinking": false, "requires_figure": true},
  "source_instance_ids": ["ex.g1a.u1.l01.01", "ex.g1a.u1.l01.02", "ex.g1a.u1.l01.03"],
  "rewritten_examples": [
    {
      "problem": "操场上有7只鸽子排成一行觅食，其中第4只鸽子突然飞了起来。一共有几只鸽子？飞起来的是第几只？",
      "answer": "7只；第4只",
      "solution": "从左到右数鸽子，共7只；飞起来的鸽子排在队伍中的第4位，所以是第4只。"
    },
    {
      "problem": "书架上从左到右摆着9本绘本，老师抽走了最右边的那一本。一共有几本绘本？被抽走的是第几本？",
      "answer": "9本；第9本",
      "solution": "点数绘本共9本；最右边即排在最后一位，也就是第9本。"
    }
  ],
  "typical_errors": ["把序数当基数回答（问第几却答总数）", "从右往左数导致序数方向搞反", "漏数或重复数导致总数错误"],
  "provenance": "textbook"
}
```

## 6. 情境与术语观测（简要）

同一批抽取顺带记录：
- **情境观测**：出现的生活情境主题（如"乡村生活""体育运动"）及涉及的数值范围，供 Stage 3 汇总情境库。
- **术语与措辞**：教材固定用语（如"照样子""练一练""试一试""数一数，连一连"）与记号习惯，供 Stage 3 汇总表述规范表（`GlossaryEntry`）。
不需要为每条都建独立文件，抽取阶段先在该书的 `work/books/<id>/` 下用简单列表记录原始观测，汇总去重留给 Stage 3。
