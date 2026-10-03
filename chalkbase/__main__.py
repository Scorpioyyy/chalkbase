"""命令行入口：`python -m chalkbase <子命令>`，详见 docs/api.md。"""
from __future__ import annotations

import argparse
import json
import sys


def _loc(cur, kp_id: str) -> str:
    return cur.locate(kp_id).label


def main(argv=None) -> int:
    from chalkbase.query import Curriculum

    from chalkbase import __version__

    ap = argparse.ArgumentParser(prog="python -m chalkbase", description="ChalkBase 课程知识库查询")
    ap.add_argument("--version", action="version", version=f"chalkbase {__version__}")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出（便于程序调用）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("search", help="自然语言/名称/别名检索知识点")
    p.add_argument("query")
    p.add_argument("-k", type=int, default=10)
    p.add_argument("--grade", type=int, help="硬过滤：首次引入年级")
    p.add_argument("--domain", choices=["na", "gg", "sp", "ip"])
    p.add_argument("--verifiable", choices=["program", "rule", "human"], help="只返回有该可验证类型题型卡片的知识点")

    p = sub.add_parser("kp", help="知识点详情（定位、前置、题型）；参数为 ID 或名称/别名")
    p.add_argument("ref")

    p = sub.add_parser("chain", help="前置链/后续链")
    p.add_argument("kp_id")
    p.add_argument("--depth", type=int, default=2, help="最大深度，0 表示不限")
    p.add_argument("--dependents", action="store_true", help="查后续（依赖它的知识点），默认查前置")
    p.add_argument("--types", default="prerequisite", help="逗号分隔：prerequisite,builds_on,extends,related,confusable")
    p.add_argument("--implied", action="store_true", help="包含被传递约简的隐含边")

    p = sub.add_parser("learned", help="某课时之前已学的知识点")
    p.add_argument("lesson_id")
    p.add_argument("--inclusive", action="store_true", help="含该课时")
    p.add_argument("--domain", choices=["na", "gg", "sp", "ip"])
    p.add_argument("--grade", type=int)

    p = sub.add_parser("archetypes", help="题型卡片")
    p.add_argument("kp_id", nargs="?")
    p.add_argument("--domain", choices=["na", "gg", "sp", "ip"])
    p.add_argument("--grade", type=int)
    p.add_argument("--verifiable", choices=["program", "rule", "human"])
    p.add_argument("--form")
    p.add_argument("--examples", action="store_true", help="同时打印改写示例")

    p = sub.add_parser("contexts", help="情境库")
    p.add_argument("--grade", type=int)
    p.add_argument("--text")

    p = sub.add_parser("glossary", help="表述规范（术语、记号、题干措辞）")
    p.add_argument("term")

    p = sub.add_parser("boundary", help="某课时（含）之前的能力边界")
    p.add_argument("lesson_id")

    p = sub.add_parser("instantiate", help="从题型卡片按种子实例化题目（program 类含答案）")
    p.add_argument("archetype_id")
    p.add_argument("-n", type=int, default=1, help="题数（种子从 --seed 起依次取）")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--lesson", help="课时 ID：同时给出该课时能力边界下的判定")
    p.add_argument("--in-bounds", action="store_true", help="只接受边界内（in）的参数，需要 --lesson")

    args = ap.parse_args(argv)
    cur = Curriculum()
    out: object
    lines: list[str] = []

    if args.cmd == "search":
        hits = cur.search(args.query, args.k, grade=args.grade, domain=args.domain, verifiable_type=args.verifiable)
        out = [h.__dict__ for h in hits]
        for i, h in enumerate(hits, 1):
            lines.append(f"{i:2d}. {h.kp_id}  {h.name}  [{h.domain}] {h.grade}年级{'上' if h.semester == 'a' else '下'} {h.unit_title} · {h.lesson_title}  ({h.score:.2f})")
    elif args.cmd == "kp":
        kps = [cur.kp(args.ref)] if args.ref in cur.knowledge_points else cur.find_kp(args.ref)
        if not kps:
            print(f"未找到知识点：{args.ref}", file=sys.stderr)
            return 1
        out = []
        for k in kps:
            loc = cur.locate(k.id)
            arch = cur.archetypes(k.id)
            pre = cur.prerequisites(k.id, depth=1, edge_types=("prerequisite", "builds_on"))
            out.append({**k.model_dump(mode="json"), "location": loc.__dict__, "archetype_ids": [a.id for a in arch], "prerequisites": [e.__dict__ for e in pre]})
            lines += [f"{k.id}  {k.name}", f"  别名：{'、'.join(k.aliases) or '—'}", f"  定位：{loc.label}（{loc.lesson_id}）", f"  领域/主线：{k.domain.value} / {k.thread}", f"  描述：{k.description}",
                      f"  前置（含递进）：{'、'.join(cur.kp(e.kp_id).name + '(' + e.edge_type + ')' for e in pre) or '—'}", f"  题型卡片：{len(arch)} 张"]
    elif args.cmd == "chain":
        types = tuple(t for t in args.types.split(",") if t)
        es = cur.chain(args.kp_id, direction="dependents" if args.dependents else "prerequisite", depth=args.depth or None, edge_types=types, include_implied=args.implied)
        out = [e.__dict__ for e in es]
        for e in es:
            lines.append(f"{'  ' * (e.depth - 1)}{e.depth} {e.kp_id}  {cur.kp(e.kp_id).name}  ({e.edge_type}{', 隐含' if e.implied else ''}) {_loc(cur, e.kp_id)}")
    elif args.cmd == "learned":
        ids = cur.learned_before(args.lesson_id, inclusive=args.inclusive, domain=args.domain, grade=args.grade)
        ordered = sorted(ids, key=lambda k: cur.locate(k).position)
        out = ordered
        lines.append(f"共 {len(ordered)} 个知识点")
        lines += [f"{k}  {cur.kp(k).name}" for k in ordered]
    elif args.cmd == "archetypes":
        arch = cur.archetypes(args.kp_id, domain=args.domain, grade=args.grade, verifiable_type=args.verifiable, item_form=args.form)
        out = [a.model_dump(mode="json") for a in arch]
        for a in arch:
            lines.append(f"{a.id}  [{a.verifiable_type.value} / 难度{a.difficulty} / {a.item_form.value}]  {a.template}")
            if args.examples:
                for ex in a.rewritten_examples:
                    lines.append(f"    例：{ex.problem}  答：{ex.answer}")
    elif args.cmd == "contexts":
        cs = cur.contexts(grade=args.grade, text=args.text)
        out = [c.model_dump(mode="json") for c in cs]
        lines += [f"{c.id}  适用年级 {c.applicable_grades}" for c in cs]
    elif args.cmd == "glossary":
        g = cur.glossary(args.term)
        out = g.model_dump(mode="json") if g else None
        lines.append(json.dumps(out, ensure_ascii=False, indent=1) if g else f"术语表无：{args.term}")
    elif args.cmd == "instantiate":
        ps = cur.instantiate_many(args.archetype_id, args.n, args.seed, args.lesson, only_in_bounds=args.in_bounds)
        out = [p.to_dict() for p in ps]
        for p in ps:
            lines.append(f"[seed={p.seed}] {p.problem}")
            lines.append(f"    答案：{p.answer}" + (f"    边界：{p.verdict} {p.violated_dimensions or ''}" if p.lesson_id else ""))
            lines += [f"    警告：{w}" for w in p.warnings]
    else:  # boundary
        out = cur.boundary(args.lesson_id).model_dump(mode="json")
        lines.append(json.dumps(out, ensure_ascii=False, indent=1))

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    else:
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
