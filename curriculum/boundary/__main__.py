"""Stage 6 命令行：python -m curriculum.boundary <enrich|build|apply|gen-probe|probe-build|probe-label|probe-eval>"""
from __future__ import annotations

import argparse
import json
import sys


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m curriculum.boundary")
    ap.add_argument("cmd", choices=["enrich", "build", "apply", "gen-probe", "probe-build", "probe-label", "probe-eval"])
    ap.add_argument("--split", default="val", choices=["val", "test"])
    args = ap.parse_args()
    if args.cmd == "enrich":
        from curriculum.boundary.enrich import run
        print(json.dumps(run(), ensure_ascii=False, indent=1, default=str))
    elif args.cmd == "build":
        from curriculum.boundary.build import build
        print(json.dumps(build(), ensure_ascii=False, indent=1, default=str))
    elif args.cmd == "apply":
        from curriculum.boundary.build import apply_to_knowledge_points
        print(json.dumps(apply_to_knowledge_points(), ensure_ascii=False, indent=1, default=str))
    elif args.cmd == "gen-probe":
        from curriculum.boundary.genprobe import run
        print(json.dumps(run(), ensure_ascii=False, indent=1, default=str))
    else:
        from curriculum.boundary import probe
        getattr(probe, args.cmd.replace("-", "_"))(args.split) if args.cmd == "probe-eval" else getattr(probe, args.cmd.replace("-", "_"))()


if __name__ == "__main__":
    main()
