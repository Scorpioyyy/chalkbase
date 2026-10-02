"""`python -m curriculum.stage5 [命令]`

无参数：确定性修复（不调用模型，幂等）。其余命令是需要调用模型的步骤，结果落盘后由确定性修复读取（命中 .cache/ 时重跑免费）：
  discover    缺口发现（逐知识点）
  standard    课标覆盖：pre（教材来源知识点）或 post（补全后）
  adjudicate  课标终审（post 轮未完整覆盖的条目）并生成 data/curriculum_coverage.json
  triage      逆序前置边分诊
  consolidate 缺口汇总（线索 → 缺口知识点）
  curate      缺口审查（与现有知识点重复 / 彼此重叠 / 非数学的剔除或合并）
  link        缺口知识点与现有知识点的前置边
  review      对 reconciled 条目做双模型审阅 + 仲裁
  difficulty  按当前引入位置重算题型难度（只改 data/archetypes.json 的难度字段）
  report      生成 reports/editions.md
"""
from __future__ import annotations

import json
import sys


def main(argv: list[str]) -> None:
    cmd = argv[0] if argv else "build"
    if cmd == "build":
        from curriculum.stage5.build import run

        print(json.dumps(run(), ensure_ascii=False, indent=1))
        return
    if cmd == "difficulty":
        from curriculum.stage5.difficulty import recompute

        print(json.dumps(recompute(write="--write" in argv), ensure_ascii=False, indent=1))
        return
    if cmd == "report":
        from curriculum.stage5.report import write_report

        write_report()
        return
    from curriculum.annotate.client import AnnotationClient

    client = AnnotationClient(max_workers=32, timeout=300)
    if cmd == "discover":
        from curriculum.stage5.discover import discover

        discover(client)
    elif cmd == "standard":
        from curriculum.stage5.standard import run_coverage

        run_coverage(client, argv[1])
    elif cmd == "adjudicate":
        from curriculum.stage5.standard import adjudicate, final_mapping

        for j in adjudicate(client):
            print(j["decision"], j["item_id_std"], j["reason"])
        print({k: v for k, v in final_mapping().items() if k != "items"})
    elif cmd == "triage":
        from curriculum.common import DATA_DIR, read_json
        from curriculum.stage5.conflicts import order_conflicts, triage

        kps = {k["id"]: k for k in read_json(DATA_DIR / "knowledge_points.json")}
        edges = read_json(DATA_DIR / "edges_relations.json")
        conf = order_conflicts(kps, edges)
        print(len(conf), "条逆序前置边")
        for j in triage(client, kps, conf):
            print(j["decision"], j["category"], j["confidence"], j["input_summary"], "|", j["reasoning"])
    elif cmd == "consolidate":
        from curriculum.stage5.gaps import consolidate

        consolidate(client)
    elif cmd == "curate":
        from curriculum.stage5.gaps import curate

        print(curate(client))
    elif cmd == "link":
        from curriculum.stage5.gaps import link

        print(link(client))
    elif cmd == "review":
        from curriculum.stage5.review import run_review

        print(json.dumps(run_review(client), ensure_ascii=False, indent=1))
    else:
        raise SystemExit(f"未知命令 {cmd}")


if __name__ == "__main__":
    main(sys.argv[1:])
