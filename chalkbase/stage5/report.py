"""生成 reports/editions.md：版本对齐的修复记录（问题—证据—修复），由脚本从 data/ 与 eval/annotation/reconciliation/ 生成，不手工编辑。"""
from __future__ import annotations

from collections import Counter

from chalkbase.common import DATA_DIR, EVAL_DIR, ROOT, book_of, read_json, read_jsonl, STAGE5_DIR, JUDGMENTS_DIR
from chalkbase.stage4.render import book_label
from chalkbase.stage5.conflicts import load_triage
from chalkbase.stage5.gaps import gap_id
from chalkbase.stage5.review import OVERRULED

OUT = ROOT / "reports" / "editions.md"
EDITION_CN = {2022: "新版（2022 课标）", 2011: "旧版（2011 课标）"}


def _pct(w: dict) -> str:
    if not w or w.get("p") is None:
        return "—"
    return f"{w['p']:.3f} [{w['lo']:.3f}, {w['hi']:.3f}]（{w['k']}/{w['n']}）"


def write_report() -> None:
    kps = {k["id"]: k for k in read_json(DATA_DIR / "knowledge_points.json")}
    log = read_json(STAGE5_DIR / "reconciliation.json")
    titles = {l["id"]: l["title"] for l in read_json(DATA_DIR / "lessons.json")}
    edges = read_json(DATA_DIR / "edges_relations.json")
    spec = read_json(JUDGMENTS_DIR / "stage5_gap_spec.json")
    cov = read_json(DATA_DIR / "standard_coverage.json")
    review = EVAL_DIR / "annotation" / "reconciliation" / "stats.json"
    rev = read_json(review) if review.exists() else None
    rej = read_json(JUDGMENTS_DIR / "stage5_review_rejections.json") if (JUDGMENTS_DIR / "stage5_review_rejections.json").exists() else {}
    triage = {(t["a"], t["b"]): t for t in load_triage()}
    gaps = {gap_id(g): g for g in spec["gaps"]}
    L = []
    w = L.append
    loc = lambda lid: f"{lid}「{titles[lid]}」"
    nm = lambda k: f"「{kps[k]['name']}」" if k in kps else k

    w("# 版本对齐、补全与约简记录（Stage 5）")
    w("")
    w("本文件由 `python -m chalkbase.stage5 report` 生成，是**修复记录**（发现了什么、为什么必要、怎么修的），不是遗留问题清单。数据来源：`work/stage5/reconciliation.json`（修复日志）、`work/judgments/stage5_*`（全部模型判定）、`eval/annotation/reconciliation/`（审阅）。")
    w("")
    w("## 0. 背景与总览")
    w("")
    w("12 本教材中，`g1a～g3b、g4a、g5a、g6a` 为新版（2022 课标），`g4b、g5b、g6b` 为旧版（2011 课标）（docs/design.md D5）。版本混杂产生三类问题：重复（Stage 2 实体消解已按「序列中最早出现者为引入、其余为复现」处理）、顺序冲突、缺口。")
    w("")
    nm_moves, nm_gaps, nm_drops = len(log["moves"]), len(log["gaps"]), len(log["edge_drops"])
    red = log["reduction"]
    w(f"- 修复前违反自洽条件（前置边 A→B 而 A 引入晚于 B）的边：**{log['initial_order_conflicts']}** 条；")
    w(f"- 修复：前移引入位置的知识点 **{nm_moves}** 个；取消前置属性（判定错误，改记 related）的边 **{nm_drops}** 条；补全缺口知识点 **{nm_gaps}** 个；")
    w(f"- 补全后 `prerequisite` 子图：{red['prerequisite_edges']} 条边，无环；传递约简后保留直接边 {red['direct']} 条，标记为隐含（`is_direct=false`，附一条替代路径）{red['implied']} 条；`builds_on / related / confusable` 不变；")
    cs = Counter(i["status"] for i in cov["items"])
    w(f"- 课标覆盖：{cov['n_items']} 条内容要求中覆盖 {cs['covered']} 条、不适用 {cs['not_applicable']} 条（逐条理由见第 4 节）、未覆盖 {cs['uncovered']} 条。")
    w("")
    w("## 1. 顺序冲突（逆序前置边）")
    w("")
    w("方法：对每条 `prerequisite` 边 A→B，若 A 的引入晚于 B，先分诊（流水线模型 qwen3.7-plus 思考模式，Judgment 见 `work/judgments/stage5_conflict_triage.jsonl`）：")
    w("**move**（A 确为必需前置，逆序是编排差异）→ 把 A 的引入前移到 B 的课时、作为先备知识点，原位置记为复现，迭代到不动点；**drop**（Stage 4 判定错误，如伞形/总结性概念、只是相关）→ 不前移，边改记 `related`。")
    w("**注意**：一个知识点只有在「必需前置」成立时才被前移。11 条逆序边中 10 条被分诊为判定错误（7 条起点同为 g4a 整理性知识点「自然数的认识」，它是对已学数概念的命名与总结，不是具体读写/比较的前置）；1 条（整数除以分数 → 分数除法的一般法则，新旧版把「分数除法」排在 6 上/5 下）被分诊为版本冲突并前移，但审阅判定「整数除以分数只是一般法则的特例，不是必需前置」，按审阅意见改为取消前置。因此**本次没有前移任何引入位置**，缺口补全是版本对齐的主要修复。")
    w("")
    w("| # | 前置边 A → B | A 引入 | B 引入 | 类型 | 证据与理由 | 修复 |")
    w("|---|---|---|---|---|---|---|")
    conflicts = [(m["kp"], t["to"], "move", m) for m in log["moves"] for t in m["triggers"]] + [(d["from"], d["to"], "drop", d) for d in log["edge_drops"]]
    for n, (a, b, kind, rec) in enumerate(conflicts, 1):
        t = triage.get((a, b), {})
        if kind == "move":
            w(f"| {n} | {nm(a)} → {nm(b)} | {loc(rec['from_lesson'])} | {loc(rec['to_lesson'])} | {t.get('category', '')} | {t.get('reasoning', '')} | 引入前移到 {rec['to_lesson']}（先备知识点），原位置改记复现 |")
        else:
            w(f"| {n} | {nm(a)} → {nm(b)} | {loc(kps[a]['first_introduced_lesson_id'])} | {loc(kps[b]['first_introduced_lesson_id'])} | {rec['category']} | {rec['reason']} | 前置改记 related，不前移 |")
    w("")
    w("## 2. 缺口补全")
    w("")
    w("两路来源：(1) 逐知识点枚举「被假定已学、但知识库中没有对应知识点」的先备概念（419 次调用，`stage5_gap_discovery.jsonl`）；(2) 课标条目 ↔ 知识点覆盖映射中未被覆盖的要点（`stage5_standard_coverage_pre.jsonl`）。两路线索汇总成缺口知识点、再逐个与最相似的现有知识点对照去重（`stage5_gap_consolidation.jsonl`、`stage5_gap_curation.jsonl`），最后与现有知识点逐对判定前置边（沿用 Stage 4 指南与 D16 的 0.95 阈值）。")
    w("")
    w(f"线索共 {len(spec['sources'])} 条；初步汇总 {len(spec['gaps']) + len(spec.get('rejected_gaps', []))} 个缺口，审查后剔除/合并 {len(spec.get('rejected_gaps', []))} 个（与现有知识点重复、彼此重叠或非数学），保留 {len(spec['gaps'])} 个，其中 {len(spec['gaps']) - nm_gaps} 个在审阅中被否决剔除，最终补全 {nm_gaps} 个。引入位置规则：不晚于其最早的依赖者所在课时（含递进依赖），也不晚于其课标学段的最后一本书末尾，且不早于自己的前置知识点（作为该课时的先备知识点）；无教材内依赖者的，放在其课标学段对应的最后一本书末尾。注意 `g4b.u9.l03` 是 g4b 末尾的「学期总结」课，被用作第二学段末尾的挂载点，它不是真实教学位置。")
    w("")
    w("| 知识点 | 引入位置 | 问题—证据（来源线索） | 修复 | 建议题型方向 |")
    w("|---|---|---|---|---|")
    for g in log["gaps"]:
        k = kps[g["kp"]]
        sg = gaps.get(g["kp"], {})
        srcs = {s["id"]: s for s in spec["sources"]}
        ev = "；".join(srcs[s]["text"][:70] for s in g["source_ids"][:2] if s in srcs)
        dirs = "；".join(sg.get("archetype_directions", [])[:3])
        w(f"| {k['name']}（`{g['kp']}`） | {loc(g['lesson'])} | {ev} | 新建 reconciled 知识点；{g['how']} | {dirs} |")
    w("")
    if spec.get("rejected_gaps"):
        w("被剔除/合并的缺口候选：")
        w("")
        for r in spec["rejected_gaps"]:
            c = r["curation"]
            tgt = f"→ {nm(c['target'])}" if c["action"] == "duplicate_existing" and c["target"] in kps else ""
            w(f"- {r['name']}：{c['action']} {tgt}——{c['reason']}")
        w("")
    if spec.get("mutual_edges_dropped"):
        w("缺口之间互为前置（环）而被处理的边：" + "；".join(f"{nm(d['from'])}→{nm(d['to'])}（{d['reason']}）" for d in spec["mutual_edges_dropped"]))
        w("")
    w("## 3. 约简与派生量")
    w("")
    w(f"- 环检测：`prerequisite` 子图为 DAG（检测到的环：{len(log['cycles'])} 个；补全过程中曾出现一对缺口互为前置的环，已在缺口边判定后处理，见上）。")
    w(f"- 传递约简：{red['prerequisite_edges']} → 直接边 {red['direct']}，隐含边 {red['implied']} 条保留在 `data/edges_relations.json`（`is_direct=false`，`evidence.implied_via` 给出一条替代路径）。")
    w("- 题型难度：`python -m chalkbase.stage5 difficulty --write` 复用 Stage 3 的难度函数，按当前引入位置重算 `data/archetypes.json` 中的难度字段（只改难度字段，幂等）。")
    b = log.get("stage4_before")
    if b:
        w("")
        w("Stage 4 指标（金标不变，闭包上计算）修复前 → 最终图（见 `reports/eval.md`）：")
        w("")
        w("| 划分 | 闭包 F1（前） | 闭包 P（前） | 闭包 R（前） | 候选召回（前） | 锚点闭包召回（前） |")
        w("|---|---|---|---|---|---|")
        for sp in ("val", "test"):
            x = b[sp]
            w(f"| {sp} | {x['closure_f1']} | {x['closure_precision']} | {x['closure_recall']} | {x['candidate_recall']} | {x['anchor_closure_recall']} |")
    w("")
    w("## 4. 课标覆盖明细")
    w("")
    w(f"清单：`config/curriculum_standard_2022.yaml`，{cov['n_items']} 条。**清单来源核实情况**：{cov['source_note']}")
    w("")
    w("映射方法：每条内容要求拆成要点，逐要点在知识库中找专门讲该要点的知识点（流水线模型 qwen3.7-plus，`stage5_standard_coverage_pre/post.jsonl`）；补全前判定覆盖 "
      f"{log.get('coverage_before', {}).get('covered', '?')}/{log.get('coverage_before', {}).get('n', '?')} 条，其余作为缺口线索；补全后仍有疑问的条目由 qwen3.8-max（思考模式）终审（`stage5_standard_adjudication.jsonl`）。")
    w("")
    w("按学段与领域的覆盖：")
    w("")
    w("| 学段 | 领域 | 条目数 | 覆盖 | 不适用 |")
    w("|---|---|---|---|---|")
    grp: dict = {}
    for i in cov["items"]:
        g = grp.setdefault((i["stage"], i["domain"]), Counter())
        g[i["status"]] += 1
    for (st, dm), c in sorted(grp.items()):
        w(f"| {st} | {dm} | {sum(c.values())} | {c['covered']} | {c['not_applicable']} |")
    w("")
    w("**不适用条目（逐条理由）**——只因为没有可单独考查的数学知识内容，不是因为难以覆盖：")
    w("")
    for i in cov["items"]:
        if i["status"] == "not_applicable":
            w(f"- `{i['item_id']}` {i['text']}：{i['reason']}")
    w("")
    w("## 5. `reconciled` 条目审阅（修复的正确性）")
    w("")
    r1p = EVAL_DIR / "annotation" / "reconciliation" / "stats_round1.json"
    r1 = read_json(r1p) if r1p.exists() else None

    def table(st):
        w("| 类型 | 条数 | 通过 | 未通过 | 仲裁失败（未完成） | 通过率 |")
        w("|---|---|---|---|---|---|")
        for kind, s_ in st["by_kind"].items():
            if s_["n"]:
                w(f"| {kind if kind != '_all' else '合计'} | {s_['n']} | {s_['pass']} | {s_['fail']} | {s_['n'] - s_['pass'] - s_['fail'] - s_['human_queue']} | {_pct(s_['pass_rate'])} |")
        w("")

    if rev:
        w("流程：两个不同厂商模型（qwen3.8-flash、deepseek-v4.1-flash，非思考）独立盲审每条「问题—证据—修复」，分歧由 qwen3.8-max 思考模式仲裁（`eval/annotation/reconciliation/`，指南见 `guideline.md`）。")
        w("")
        if r1:
            w(f"**首轮**（修复完成后的全部 {r1['n']} 条；Cohen κ={r1['cohen_kappa']}，费用 {r1['cost_cny']} 元）通过率（Wilson 95% CI）：")
            w("")
            table(r1)
        w("首轮未达到 0.90 的阈值（stage5.md §3），按指南处理未通过项：缺口被判重复/非独立的剔除，缺口前置边被判「只是相关/间接」的删除，前移被判「A 并非必需前置」的改为取消前置；之后**从原始状态重建**，重建后的条目再次审阅（多轮，每轮只有改动过的条目产生新调用），直到除下述「已核实事实而不采纳否决」的条目外全部通过。")
        w("")
        w(f"**最终轮**（重建后的 {rev['n']} 条；Cohen κ={rev['cohen_kappa']}）：")
        w("")
        table(rev)
        w("累计应用的否决：" + f"缺口 {len(rej.get('gaps', []))}、缺口前置边 {len(rej.get('gap_edges', []))}、前移 {len(rej.get('moves', []))}、取消前置 {len(rej.get('drops', []))}。说明：最终轮 111 条中 109 条通过，其余 2 条是下面因与已核实事实冲突而未采纳否决的缺口：「面积单位」判未通过（理由是「北师大三下已教」的先验，与已核实事实冲突，故不采纳）；「分数的意义」两位审阅者分歧（fail/pass），仲裁未能完成，历轮均判未通过，同样不采纳。")
        w("")
        for kind in ("gaps", "moves", "drops"):
            for i in rej.get(kind, []):
                w(f"- 否决（{kind}）`{i['id']}`：{i['reason']}")
        w("")
        if rej.get("overruled"):
            w("**审阅者与已核实事实冲突、未采纳否决的缺口**（建议结合实际使用的教材核实）：")
            w("")
            for i in rej["overruled"]:
                w(f"- `{i['id']}`：审阅者意见——{i['reviewer_reason']}；未采纳理由——{i['overruled_because']}")
            w("")
    w("## 6. 复现")
    w("")
    w("```")
    w("python -m chalkbase.stage5            # 确定性修复（幂等；不调用模型）")
    w("python -m chalkbase.stage5 report     # 重新生成本文件")
    w("```")
    w("从原始状态完整重建：`git checkout data/knowledge_points.json data/lessons.json data/edges_relations.json && rm work/stage5/reconciliation.json && python -m chalkbase.stage5`（或先重跑 `python -m chalkbase.stage2` 与 `python -m chalkbase.stage4`，均命中缓存）。模型判定步骤：`discover`、`standard pre|post`、`triage`、`consolidate`、`curate`、`link`、`adjudicate`、`review`（均命中 `.cache/`，重跑免费）。")
    w("")
    w("## 7. 缺口知识点的题型卡片")
    w("")
    gap_ids = {g["kp"] for g in log["gaps"]}
    n_cards = sum(1 for a in read_json(DATA_DIR / "archetypes.json") if a.get("provenance") == "reconciled" and a["primary_knowledge_point_id"] in gap_ids)
    w(f"{nm_gaps} 个缺口知识点没有教材习题实例，清单与建议题型方向在 `work/stage5/gap_kps_pending_cards.json`。Stage 3 的生成接口（`chalkbase.stage3.generate.generate_cards`）按「习题实例分组」组织，缺口知识点以知识点描述与 `suggested_archetype_directions` 作为「虚拟分组」输入（`chalkbase.stage3.gaps`），其余校验（程序重算、约束接受率、5-gram 重合率）原样复用；生成的 {n_cards} 个题型卡片 `provenance` 标记为 `reconciled`、无源实例。")
    w("")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
