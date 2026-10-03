"""`python -m chalkbase.stage4`：运行 Stage 4 全流程（命中 .cache/ 时重跑免费且确定）。"""
import json

from chalkbase.stage4.build import run

if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1))
