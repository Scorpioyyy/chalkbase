"""Stage 3 · 语义细分（D26）：(主知识点, 题目形式) 组内，按「考什么、怎么答、解法思路」划分子组，每个子组一个题型。

为什么需要语义：签名（操作数分桶）是数字特征，分不出「计算 vs 说理 vs 画图」「正向求值 vs 逆向求条件」；
而这些恰是老师眼中的题型边界（粒度金标上「过粗」的主要来源，见 eval/specs/stage3.md）。
这一步确实需要语义理解，所以交给流水线模型 qwen3.7-plus（非思考，D13），每次判断以 Judgment 落盘。
"""
from __future__ import annotations

import json
from collections import defaultdict

from chalkbase.annotate.client import AnnotationClient, AnnotationRequest
from chalkbase.annotate.gold import ModelConfig, judgment_record

PIPELINE = ModelConfig("qwen3.7-plus", False, max_tokens=2048)
MAX_TEXT = 200

SYSTEM = """你是经验丰富的小学数学教研员，负责把教材里同一知识点、同一题目形式的一批习题归成「题型」。

「一种题型」= 老师出题时说「再来几道这种题」的那个「这种」。把下面编号的习题划分成若干子组，同一子组内的题应当是同一种题。**粒度要粗**：只在差别明显、老师一定会分开讲分开练时才拆。

先把每道题按「作答方式」归入下列大类之一：
 A. 计算 / 求值 / 填数 / 列式解应用题（得到一个数或式子）
 B. 画图 / 动手操作 / 拼摆 / 涂色 / 连线
 C. 判断对错 / 选择
 D. 读图读表取数 / 填表整理数据
 E. 开放表达（说一说、想一想、与同伴交流）。这一大类再分三种，不同话题但同一种作答方式的不再细拆：
    E1 解释说理：解释概念、公式、算理、方法「为什么」；
    E2 创编提问：自己提问题、编题、举生活中的例子；
    E3 探究实践：调查、设计方案、收集整理数据、估测、拼摆探究等活动环节，以及总结收获、反思评价、与同伴交流经验。

**拆分规则**：
1. 不同大类（A/B/C/D/E1/E2/E3）之间拆开；
2. 同一大类内，只有出现下面情形才再拆：正向求结果 vs 已知结果逆求条件（解法明显不同）；一步直接算 vs 多步综合 / 列表枚举的复杂问题；**应用题里含有两种以上明显不同的数量关系模型**（如「比……多/少几分之几」与「折扣」，「求相遇时间」与「估计相遇位置并画图」）；考的明显是该知识点下两件不同的事；
3. **不要拆**：数的大小、位数、有无进位退位、整数/小数、情境与人名物名、措辞、题量多少、次要知识点不同、同一数量关系模型下的不同情境。

规则：
- 每个编号恰好属于一个子组；子组数量通常 1～3 个。**宁合勿拆**：拿不准时放在同一子组。
- 只有当某道习题与其余明显属于不同大类、或解法明显不同时，才允许它单独成一个子组。
- 单条习题内含多个小问时，按其主要考查点归类，不因夹带的次要问法而拆开。
- 每个子组给一个不超过 20 字的名称，说明这一类考什么/怎么答。

只输出 JSON：{"subgroups": [{"name": "子组名称", "members": [编号, ...]}, ...]}"""

USER_TMPL = """知识点：{kp_name}（{kp_desc}）
题目形式：{item_form}

习题（编号 | 答案形式 | 原文）：
{lines}

请划分子组。"""


def _make_validator(n: int):
    def v(d) -> bool:
        if not isinstance(d, dict) or not isinstance(d.get("subgroups"), list) or not d["subgroups"]:
            return False
        seen = []
        for s in d["subgroups"]:
            if not isinstance(s, dict) or not isinstance(s.get("members"), list) or not s["members"] or not s.get("name"):
                return False
            seen += s["members"]
        return sorted(seen) == list(range(1, n + 1)) and all(isinstance(x, int) for x in seen)

    return v


def partition_requests(base_groups: dict[tuple, list[dict]], kps: dict[str, dict]) -> list[tuple[tuple, AnnotationRequest, str]]:
    out = []
    for key, inst in sorted(base_groups.items()):
        if len(inst) < 2:
            continue
        kp = kps[key[0]]
        lines = "\n".join(f"{i + 1} | {e['answer_form']} | {e['text'][:MAX_TEXT].replace(chr(10), ' ')}" for i, e in enumerate(inst))
        msg = USER_TMPL.format(kp_name=kp["name"], kp_desc=kp["description"][:120], item_form=key[1], lines=lines)
        req = AnnotationRequest(
            request_id=f"part:{key[0]}|{key[1]}", model=PIPELINE.model, thinking=PIPELINE.thinking,
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": msg}],
            response_schema_validator=_make_validator(len(inst)), max_tokens=PIPELINE.max_tokens,
        )
        out.append((key, req, msg))
    return out


def base_groups_of(exercises: list[dict]) -> dict[tuple, list[dict]]:
    g = defaultdict(list)
    for e in exercises:
        g[(e["primary_knowledge_point_id"], e["item_form"])].append(e)
    return g


def partition_instances(exercises: list[dict], kps: dict[str, dict], client: AnnotationClient) -> tuple[list[dict], list[dict]]:
    """返回 (groups, judgments)。groups: [{signature: (kp, form), name, instance_ids}]，确定性排序。

    模型输出不合法（重试后仍失败）的组整体保留为一个子组（宁合勿拆），并计入 judgments 的 ok=False。
    """
    base = base_groups_of(exercises)
    reqs = partition_requests(base, kps)
    results = client.run_batch([r for _, r, _ in reqs], label="语义细分")
    groups, judgments = [], []
    done = set()
    for (key, req, msg), res in zip(reqs, results):
        inst = base[key]
        done.add(key)
        conclusion = json.dumps(res.parsed, ensure_ascii=False) if res.ok else "ERROR"
        judgments.append(dict(judgment_record("other", f"part.{key[0]}|{key[1]}", "pipeline", res, msg, conclusion), task="archetype_partition"))
        if res.ok:
            for s in res.parsed["subgroups"]:
                groups.append({"signature": key, "name": s["name"], "instance_ids": sorted(inst[i - 1]["id"] for i in s["members"])})
        else:
            groups.append({"signature": key, "name": "", "instance_ids": sorted(e["id"] for e in inst)})
    for key, inst in base.items():
        if key not in done:  # 单实例组：不需要划分
            groups.append({"signature": key, "name": "", "instance_ids": [inst[0]["id"]]})
    groups.sort(key=lambda g: (g["signature"][0], g["signature"][1], -len(g["instance_ids"]), g["instance_ids"][0]))
    return groups, judgments
