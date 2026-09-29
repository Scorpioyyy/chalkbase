"""VeriChalk 课程知识库数据模型。

单一事实来源：规范数据只存在于 data/，本文件定义其 schema。
所有小数一律用 Decimal 表示（json 中以字符串编码），禁止浮点误差进入规范数据。

ID 约定见 CLAUDE.md 第 6 节：
  书 g4a / g4b，单元 g4a.u1，课时 g4a.u1.l03，
  知识点 kp.<领域>.<主线>.<slug>，题型 at.<知识点slug>.<序号>，
  习题实例 ex.<课时ID>.<序号>。
"""
from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# 枚举
# --------------------------------------------------------------------------


class Domain(str, Enum):
    """2022 版课标四大领域。"""

    NUMBER_ALGEBRA = "na"  # 数与代数
    GEOMETRY = "gg"  # 图形与几何
    STATISTICS_PROBABILITY = "sp"  # 统计与概率
    INTEGRATED_PRACTICE = "ip"  # 综合与实践


class UnitType(str, Enum):
    REGULAR = "unit"  # 常规单元
    INTEGRATED_PRACTICE = "integrated_practice"  # 综合与实践
    MATH_FUN = "math_fun"  # 数学好玩
    REVIEW = "review"  # 整理与复习
    FINAL_REVIEW = "final_review"  # 总复习


class MasteryLevel(str, Enum):
    """课标对知识点的要求层级（描述性，非严格递进）。"""

    KNOW = "know"  # 了解
    UNDERSTAND = "understand"  # 理解
    MASTER = "master"  # 掌握
    APPLY = "apply"  # 运用


class ItemForm(str, Enum):
    FILL_BLANK = "fill_blank"  # 填空
    CHOICE = "choice"  # 选择
    JUDGE = "judge"  # 判断
    COMPUTE = "compute"  # 计算/竖式/脱式
    WORD_PROBLEM = "word_problem"  # 解决问题/应用题
    DRAW = "draw"  # 画图/作图
    MEASURE = "measure"  # 测量/操作
    READ_CHART = "read_chart"  # 读图/读表/统计图表
    OTHER = "other"


class AnswerForm(str, Enum):
    NUMBER = "number"  # 单一数值
    EXPRESSION = "expression"  # 算式/表达式
    MULTI_BLANK = "multi_blank"  # 多空
    CHOICE_LETTER = "choice_letter"
    BOOLEAN = "boolean"
    FREE_TEXT = "free_text"  # 文字说明/推理过程
    DRAWING = "drawing"
    TABLE_OR_CHART = "table_or_chart"


class VerifiableType(str, Enum):
    PROGRAM = "program"  # 可由程序求解校验
    RULE = "rule"  # 可由规则（非数值计算）校验，如画图规则
    HUMAN = "human"  # 需人工评判（开放性表达题）


class EdgeType(str, Enum):
    PREREQUISITE = "prerequisite"  # 前置：不掌握 A 无法学会 B
    BUILDS_ON = "builds_on"  # 递进但非强前置（如万以内数→亿以内数）
    RELATED = "related"  # 相关但无方向性依赖
    CONFUSABLE = "confusable"  # 易混淆
    EXTENDS = "extends"  # 螺旋扩展（Stage 2 成对判定产物，窄→宽；作为 Stage 4 的证据，见 decisions.md D13）


class Provenance(str, Enum):
    TEXTBOOK = "textbook"  # 直接来自教材
    RECONCILED = "reconciled"  # 版本对齐时修复或补全


class JudgmentTaskType(str, Enum):
    PAIRWISE_ENTITY_RESOLUTION = "pairwise_entity_resolution"  # Stage 2
    ARCHETYPE_GRANULARITY = "archetype_granularity"  # Stage 3
    PREREQUISITE_JUDGMENT = "prerequisite_judgment"  # Stage 4
    RECONCILIATION_REVIEW = "reconciliation_review"  # Stage 5
    OTHER = "other"


# --------------------------------------------------------------------------
# 课程结构：Book / Unit / Lesson
# --------------------------------------------------------------------------


class PageRange(BaseModel):
    """印刷页码与 PDF 页序可能不一致，两者都记录。"""

    pdf_start: int = Field(..., ge=1, description="起始 PDF 页序（1-indexed）")
    pdf_end: int = Field(..., ge=1, description="结束 PDF 页序（含）")
    printed_start: Optional[str] = Field(None, description="起始印刷页码（原书页码，可能含前言等非阿拉伯数字页码）")
    printed_end: Optional[str] = Field(None, description="结束印刷页码")


class Lesson(BaseModel):
    id: str = Field(..., description="如 g4a.u1.l03")
    unit_id: str
    index: int = Field(..., ge=1, description="在单元内的顺序号，从 1 开始")
    title: str = Field(..., description="教材原标题，北师大版常以情境命名，如「人口普查」，不等同于知识点")
    pages: PageRange
    intro_knowledge_point_ids: list[str] = Field(
        default_factory=list, description="本课时新引入（首次教授）的知识点"
    )
    practice_knowledge_point_ids: list[str] = Field(
        default_factory=list, description="本课时练习/复现但非首次引入的知识点"
    )
    summary: str = Field(..., description="一句话概括本课时内容（自己的话，不摘抄教材）")


class Unit(BaseModel):
    id: str = Field(..., description="如 g4a.u1")
    book_id: str
    index: int = Field(
        ..., ge=0, description="在书内的顺序号，从 0 开始；0 号保留给部分一年级教材开学之初的非正式起始单元（如「我上学啦」）"
    )
    title: str
    unit_type: UnitType
    pages: PageRange
    lesson_ids: list[str] = Field(default_factory=list)


class Book(BaseModel):
    id: str = Field(..., description="如 g4a（四年级上册）")
    grade: int = Field(..., ge=1, le=6)
    semester: str = Field(..., pattern="^[ab]$", description="a=上册 b=下册")
    title: str
    edition_authority: str = Field(..., description="封面标注的审定/审核机构与年份原文")
    curriculum_standard_year: int = Field(..., description="依据的课标年份，如 2022 或 2011")
    has_text_layer: bool
    pdf_path: str = Field(..., description="相对项目根目录的路径，如 textbook/数学四年级上册（北师大版）.pdf")
    pdf_page_count: int
    page_offset_note: str = Field(
        ..., description="印刷页码到 PDF 页序的映射规则说明，如「PDF 第 N 页 = 印刷页码 N-4（前 4 页为封面/扉页/版权页/目录）」"
    )
    unit_ids: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# 知识点
# --------------------------------------------------------------------------


class CapabilityGrant(BaseModel):
    """学会某知识点后获得的能力增量（半格上的一次合并操作数），见 Stage 6。

    各维度语义为「新增到已有能力上」，聚合时对应维度分别取 max / 并集。
    空值/空集合表示该知识点对这一维度无增量。
    """

    integer_domain_max: Optional[int] = Field(
        None, description="整数数域上限（如学到「亿以内数」后为 10**9 - 1）"
    )
    decimal_max_places: Optional[int] = Field(None, description="小数最多位数上限")
    fraction_types: set[str] = Field(
        default_factory=set, description="新增允许的分数类型，如 {'真分数', '假分数', '带分数'}"
    )
    operation_operand_forms: dict[str, set[str]] = Field(
        default_factory=dict,
        description="四则运算各自新增允许的操作数形态，如 {'除法': {'三位数除以两位数', '有余数'}}",
    )
    concepts: set[str] = Field(default_factory=set, description="新增已学概念，如「公因数」「三角形内角和」")
    units_of_measure: set[str] = Field(default_factory=set, description="新增计量单位，如「千米」「公顷」")
    geometry_vocab: set[str] = Field(default_factory=set, description="新增几何词汇，如「对角线」「圆心角」")


class KnowledgePoint(BaseModel):
    id: str = Field(..., description="如 kp.na.亿以内数的认识.read")
    name: str
    aliases: list[str] = Field(default_factory=list)
    domain: Domain
    topic: str = Field(..., description="2022 版课标主题，如「数与运算」")
    thread: str = Field(..., description="主线，来自种子主线词表，如「多位数的认识」")
    description: str
    mastery_level: MasteryLevel
    first_introduced_lesson_id: str
    review_lesson_ids: list[str] = Field(default_factory=list, description="复现（练习/综合运用）该知识点的课时")
    is_assessable: bool = Field(True, description="是否可直接作为出题考查点（部分为铺垫性概念，不单独考查）")
    typical_errors: list[str] = Field(default_factory=list)
    grants: CapabilityGrant = Field(default_factory=CapabilityGrant)
    explicit_review_refs: list[str] = Field(
        default_factory=list,
        description="教材中「回顾以前所学」类的显式引用，记录被引用的知识点局部名称或课时（Stage 1 抽取时的原始证据）",
    )
    provenance: Provenance = Provenance.TEXTBOOK
    provenance_note: Optional[str] = Field(None, description="provenance=reconciled 时必填，说明修复/补全的理由")


# --------------------------------------------------------------------------
# 习题实例与题型
# --------------------------------------------------------------------------


class OperandFeatures(BaseModel):
    """操作数特征，用于 Stage 3 计算题型签名。"""

    number_types: set[str] = Field(default_factory=set, description="如 {'整数', '小数', '分数'}")
    integer_digits: Optional[int] = Field(None, description="最大整数位数")
    decimal_places: Optional[int] = Field(None, description="最大小数位数")
    requires_carry_or_borrow: Optional[bool] = None
    requires_exact_division: Optional[bool] = None
    operation_steps: Optional[int] = Field(None, description="运算步数")


class ExerciseInstance(BaseModel):
    id: str = Field(..., description="如 ex.g4a.u1.l03.02")
    lesson_id: str
    pdf_page: int
    printed_page: Optional[str] = None
    item_form: ItemForm
    primary_knowledge_point_id: str
    secondary_knowledge_point_ids: list[str] = Field(default_factory=list)
    operand_features: OperandFeatures = Field(default_factory=OperandFeatures)
    answer_form: AnswerForm
    requires_figure: bool = False
    figure_type: Optional[str] = Field(None, description="如「数轴」「方格图」「统计图」，requires_figure=True 时填写")
    context_theme: Optional[str] = Field(None, description="生活情境主题，如「买菜」「行程」")
    summary: str = Field(..., max_length=40, description="一句不超过 20 字的自述概括")
    text: str = Field(
        ...,
        description="习题原文；图形部分用文字描述配图内容（如「数轴上标出 0～10 的刻度，箭头指向 3」）。"
        "仅供标注模型组与人工核对参照，不作为生成环节的题目来源。",
    )


class RewrittenExample(BaseModel):
    """题型卡片的改写示例：由 Agent/模型依据模板与参数约束新写，不得复述教材原题。"""

    problem: str
    answer: str
    solution: str = Field(..., description="简要解法")
    params: dict = Field(default_factory=dict, description="生成该示例所用的槽位取值（program 类用于程序重算校验）")
    answer_value: Optional[Any] = Field(
        None, description="程序可比较的答案（program 类必须等于 solver_program 对 params 的重算结果）"
    )


class ItemArchetype(BaseModel):
    id: str = Field(..., description="如 at.亿以内数的读法.01")
    primary_knowledge_point_id: str
    secondary_knowledge_point_ids: list[str] = Field(default_factory=list)
    item_form: ItemForm
    template: str = Field(..., description="抽象模板，带槽位，如「读出下面的数：{number}」")
    parameter_constraints: dict = Field(
        default_factory=dict, description="槽位的参数约束，取该组实例观测值的包络（来自教材数据而非臆测）"
    )
    answer_form: AnswerForm
    solution_steps: list[str] = Field(default_factory=list)
    allowed_contexts: list[str] = Field(default_factory=list, description="情境库中的情境 ID")
    figure_types: list[str] = Field(default_factory=list)
    verifiable_type: VerifiableType
    solver_program: Optional[str] = Field(
        None, description="program 类必填：Python 源码，定义 solve(**槽位)，返回 int/Decimal/Fraction/str/bool（禁止浮点）；"
        "由 curriculum.stage3.sandbox 在受限环境中执行，用于示例重算与生成探针"
    )
    difficulty: int = Field(..., ge=1, le=5)
    difficulty_features: dict = Field(
        default_factory=dict, description="难度特征原值：解题步数、涉及知识点数、位置跨度、是否逆向思考、是否读图"
    )
    source_instance_ids: list[str] = Field(default_factory=list, description="所归纳的教材实例 ID 列表")
    rewritten_examples: list[RewrittenExample] = Field(..., min_length=2, max_length=3)
    typical_errors: list[str] = Field(default_factory=list)
    provenance: Provenance = Provenance.TEXTBOOK
    provenance_note: Optional[str] = None


# --------------------------------------------------------------------------
# 关系与判定
# --------------------------------------------------------------------------


class Edge(BaseModel):
    id: str = Field(..., description="如 e.<from>.<to>")
    type: EdgeType
    from_knowledge_point_id: str
    to_knowledge_point_id: str
    evidence: dict = Field(
        default_factory=dict,
        description="确定性计算的证据，如共现比例、显式引用、主线相邻、Stage2 的 extends 判定",
    )
    judgment_id: Optional[str] = Field(None, description="关联的 Judgment 记录 ID")
    is_direct: bool = Field(True, description="传递约简后是否为保留的直接边；False 表示被约简为隐含边")
    provenance: Provenance = Provenance.TEXTBOOK
    provenance_note: Optional[str] = None


class Judgment(BaseModel):
    """每一次语言模型判断的记录，可审计、可重放。"""

    id: str
    task_type: JudgmentTaskType
    input_summary: str
    conclusion: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str
    model: str
    mode: str = Field(..., description="'thinking' 或 'non_thinking'")
    prompt_hash: str
    timestamp: str = Field(..., description="ISO 8601")
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_cny: Optional[Decimal] = None


# --------------------------------------------------------------------------
# 能力边界 / 情境 / 术语规范
# --------------------------------------------------------------------------


class CapabilityBoundary(BaseModel):
    """某课时及之前所有已学知识点 grants 的合并结果（Stage 6 单调折叠的产物，脚本生成，不手工编辑）。"""

    lesson_id: str
    integer_domain_max: Optional[int] = None
    decimal_max_places: Optional[int] = None
    fraction_types: set[str] = Field(default_factory=set)
    operation_operand_forms: dict[str, set[str]] = Field(default_factory=dict)
    concepts: set[str] = Field(default_factory=set)
    units_of_measure: set[str] = Field(default_factory=set)
    geometry_vocab: set[str] = Field(default_factory=set)


class Context(BaseModel):
    """教材使用的生活情境及其合理数值范围。"""

    id: str = Field(..., description="如 ctx.买菜")
    theme: str
    applicable_grades: list[int] = Field(default_factory=list)
    value_ranges: dict = Field(default_factory=dict, description="该情境下常见的数值范围（取观测包络）")
    source_instance_ids: list[str] = Field(default_factory=list)
    provenance: Provenance = Provenance.TEXTBOOK
    provenance_note: Optional[str] = None


class GlossaryEntry(BaseModel):
    """规范术语、记号与题干措辞。"""

    term: str = Field(..., description="规范名称")
    aliases: list[str] = Field(default_factory=list)
    notation: Optional[str] = Field(None, description="标准记号，如分数的书写形式")
    phrasing_patterns: list[str] = Field(default_factory=list, description="教材中常见的题干措辞模式")
    notes: Optional[str] = None
