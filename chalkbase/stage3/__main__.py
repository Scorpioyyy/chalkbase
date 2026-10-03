"""`python -m chalkbase.stage3`：运行 Stage 3 全流程（命中 .cache/ 时重跑免费且确定）。"""
import json

from chalkbase.stage3.build import run

if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
