"""打包冒烟：构建 wheel，在仓库之外的全新 venv 里非 editable 安装，验证不依赖仓库根目录也能工作。

检查：wheel 内容（带 chalkbase/data，不含 work/ eval/ textbook/）、`python -m chalkbase --version`、CLI 检索、
`Curriculum().instantiate(...)`、`boundary()`、数据目录确实来自安装包、manifest 与文件一致、没有在工作目录留下缓存。
检索按「没有 DASHSCOPE_API_KEY」运行，验证降级为词法检索并给出警告。

用法：python scripts/smoke_wheel.py [--keep] [--no-build-isolation]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CHECK = r'''
import json, sys, warnings
from pathlib import Path
import chalkbase
from chalkbase import Curriculum
from chalkbase.manifest import verify_manifest

cur = Curriculum()
assert "site-packages" in str(cur.data_dir), cur.data_dir
assert Path(chalkbase.__file__).parent == cur.data_dir.parent
assert not verify_manifest(cur.data_dir), verify_manifest(cur.data_dir)
assert cur.manifest["data_version"] == chalkbase.__version__
with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    hits = cur.search("三年级学乘法分配律的应用题", k=3)
assert hits and any("降级" in str(x.message) for x in w), (hits, [str(x.message) for x in w])
prog = [a for a in cur.archetypes_by_id.values() if a.verifiable_type.value == "program"]
a = prog[0]
lesson = cur.archetype_lessons(a.id)[-1]
p = cur.instantiate(a.id, seed=1, lesson_id=lesson)
assert p.problem and p.answer is not None and p.verdict in ("in", "borderline", "out")
assert p.to_dict() == cur.instantiate(a.id, seed=1, lesson_id=lesson).to_dict()
assert len(cur.instantiate_many(a.id, 3, only_in_bounds=True, lesson_id=lesson)) >= 1
b = cur.boundary(lesson)
assert b.lesson_id == lesson and b.integer_domain_max
rule = next(x for x in cur.archetypes_by_id.values() if x.verifiable_type.value == "rule")
assert cur.instantiate(rule.id).answer is None
print(json.dumps({"version": chalkbase.__version__, "data_dir": str(cur.data_dir), "problem": p.problem[:40], "answer": p.answer,
                  "verdict": p.verdict, "archetypes": len(cur.archetypes_by_id), "kps": len(cur.knowledge_points)}, ensure_ascii=False))
'''


def run(cmd, **kw) -> subprocess.CompletedProcess:
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("encoding", "utf-8")
    r = subprocess.run(cmd, **kw)
    if r.returncode != 0:
        sys.exit(f"失败：{' '.join(map(str, cmd))}\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
    return r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保留临时目录")
    ap.add_argument("--no-build-isolation", action="store_true", help="用当前环境里的构建后端（需已安装 hatchling）")
    args = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="chalkbase_wheel_"))
    try:
        dist = work / "dist"
        cmd = [sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps", "-w", str(dist), "-q"]
        if args.no_build_isolation:
            cmd.append("--no-build-isolation")
        run(cmd, cwd=work)
        wheel = next(dist.glob("chalkbase-*.whl"))
        with zipfile.ZipFile(wheel) as z:
            names = z.namelist()
        top = {n.split("/")[0] for n in names}
        assert "chalkbase" in top and not ({"work", "eval", "textbook", "tmp", ".cache", "viz"} & top), top
        assert "chalkbase/data/knowledge_points.json" in names and "chalkbase/data/manifest.json" in names
        assert not any(n.startswith("chalkbase/data/work") for n in names)
        print(f"wheel: {wheel.name}  {wheel.stat().st_size / 1e6:.2f} MB  {len(names)} 个文件")

        venv = work / "venv"
        run([sys.executable, "-m", "venv", str(venv)])
        py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run([str(py), "-m", "pip", "install", "-q", str(wheel)])
        installed = run([str(py), "-m", "pip", "list", "--format=json"]).stdout
        pkgs = {p["name"].lower() for p in json.loads(installed)}
        extra = pkgs - {"chalkbase", "pydantic", "pydantic-core", "pydantic_core", "numpy", "annotated-types", "typing-extensions", "typing_extensions", "typing-inspection", "pip"}
        print("运行时依赖闭包：", ", ".join(sorted(pkgs - {"pip"})), f"（多出：{sorted(extra) or '无'}）")

        cwd = work / "elsewhere"  # 不在仓库目录下
        cwd.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DASHSCOPE", "CHALKBASE", "PYTHON"))}
        env["PYTHONUTF8"] = "1"
        print(run([str(py), "-m", "chalkbase", "--version"], cwd=cwd, env=env).stdout.strip())
        out = run([str(py), "-m", "chalkbase", "--json", "search", "三年级学乘法分配律的应用题", "-k", "3"], cwd=cwd, env=env)
        hits = json.loads(out.stdout)
        assert hits, out.stdout
        print("CLI search:", hits[0]["kp_id"], hits[0]["name"], "| stderr:", out.stderr.strip()[:80])
        arch = run([str(py), "-m", "chalkbase", "--json", "archetypes", hits[0]["kp_id"]], cwd=cwd, env=env)
        aid = next((a["id"] for a in json.loads(arch.stdout) if a["verifiable_type"] == "program"), None)
        if aid:
            print(run([str(py), "-m", "chalkbase", "instantiate", aid, "-n", "2"], cwd=cwd, env=env).stdout.strip())
        print(run([str(py), "-m", "chalkbase", "boundary", "g4a.u3.l01"], cwd=cwd, env=env).stdout[:60].replace("\n", " "), "...")
        res = run([str(py), "-c", CHECK], cwd=cwd, env=env)
        print("API 检查通过：", res.stdout.strip()[:300])
        leftovers = [p.name for p in cwd.iterdir()]
        assert not leftovers, f"工作目录留下了文件：{leftovers}"
        print("OK：wheel 在仓库之外的全新 venv 中独立工作")
    finally:
        if args.keep:
            print("保留：", work)
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
