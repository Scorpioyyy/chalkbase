"""可视化 viz/index.html：存在、由当前 data/ 构建、不含外部地址、内嵌载荷与数据一致、不泄露习题原文。"""
from __future__ import annotations

import base64
import gzip
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "viz" / "index.html"


def _build_module():
    spec = importlib.util.spec_from_file_location("build_viz", ROOT / "scripts" / "build_viz.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def html() -> str:
    assert HTML.exists(), "viz/index.html 不存在：先运行 `python scripts/build_viz.py`"
    return HTML.read_text(encoding="utf-8")


def _payload(html: str, tag: str) -> dict:
    m = re.search(rf'<script type="application/octet-stream" id="{tag}">([^<]+)</script>', html)
    assert m, f"缺少内嵌载荷 {tag}"
    return json.loads(gzip.decompress(base64.b64decode(m.group(1))))


def test_built_from_current_data(html):
    """内嵌的数据哈希与计数必须与 data/ 当前内容一致；数据变了而没有重建会在这里失败。"""
    mod = _build_module()
    m = re.search(r'name="chalkbase-data-sha256" content="([0-9a-f]{64})"', html)
    assert m, "页面缺少数据哈希"
    assert m.group(1) == mod.source_hash(), "data/ 已变化，请重新运行 `python scripts/build_viz.py`"
    c = re.search(r"name=\"chalkbase-counts\" content='([^']+)'", html)
    assert json.loads(c.group(1)) == mod.data_counts()


def test_payload_counts_match_data(html):
    mod = _build_module()
    core, arch = _payload(html, "d-core"), _payload(html, "d-arch")
    counts = mod.data_counts()
    assert len(core["kps"]) == counts["kps"]
    assert len(core["lessons"]) == counts["lessons"] == len(core["tl"]["im"])
    assert len(core["edges"]) == counts["edges"]
    assert sum(1 for e in core["edges"] if e["k"] == 0 and e["d"] == 1) == counts["direct_prerequisite"]
    assert sum(len(v) for v in arch["a"].values()) == counts["archetypes"]
    assert len(core["std"]) == counts["standard_items"]
    assert len(core["oos"]) >= 30


def test_no_exercise_text_embedded(html):
    """习题原文不入页面：随机抽若干条原文，不应出现在内嵌载荷里。"""
    ex = json.loads((ROOT / "data" / "exercises.json").read_text(encoding="utf-8"))
    blob = json.dumps(_payload(html, "d-core"), ensure_ascii=False) + json.dumps(_payload(html, "d-arch"), ensure_ascii=False)
    long_texts = [e["text"] for e in ex if len(e.get("text", "")) >= 40]
    assert long_texts
    hits = sum(1 for t in long_texts[:: max(1, len(long_texts) // 200)] if t in blob)
    assert hits == 0, "页面载荷里出现了习题原文"


def test_no_external_references(html):
    """离线可用：不引用任何外部资源；唯一允许的 http(s) 地址是 XML 命名空间。"""
    urls = re.findall(r"https?://[^\s\"'<>)]+", html)
    bad = [u for u in urls if not u.startswith("http://www.w3.org/")]
    assert not bad, f"页面含外部地址：{bad[:5]}"
    assert not re.search(r"<script[^>]+\ssrc=", html), "不应有外部 script"
    assert not re.search(r"<link[^>]+href=", html), "不应有外部 link"
    assert not re.search(r"@import|url\(\s*['\"]?https?:", html)
    assert "DASHSCOPE" not in html and "sk-" not in re.sub(r"[A-Za-z0-9+/=]{200,}", "", html)


def test_size_budget(html):
    assert len(html.encode("utf-8")) <= 15_000_000


def test_pages_declared(html):
    """多页结构：四个页面与 hash 路由入口都在，二级标签齐全。"""
    for page in ("overview", "graph", "boundary", "quality"):
        assert f'id="p-{page}"' in html and f'href="#/{page}"' in html
    for tab in ("replay", "oos", "core", "all", "dist", "fix", "std", "gold"):
        assert f'data-tab="{tab}"' in html


def _playwright_page(w, h):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    p = sync_playwright().start()
    try:
        b = p.chromium.launch()
    except Exception as e:  # 未安装 chromium：跳过
        p.stop()
        pytest.skip(f"chromium 不可用：{e}")
    pg = b.new_context(viewport={"width": w, "height": h}).new_page()
    errs, ext = [], []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.route("**/*", lambda r: r.continue_() if r.request.url.startswith("file:") else (ext.append(r.request.url), r.abort()))
    return p, b, pg, errs, ext


@pytest.mark.parametrize("size", [(1440, 900), (1280, 720)])
def test_routes_show_one_page_without_scroll(size):
    """每个路由同一时刻只显示一页，整页与当前标签页都不产生纵向滚动；离线（拦截全部外部请求）下无错误。"""
    p, b, pg, errs, ext = _playwright_page(*size)
    try:
        pg.goto(HTML.as_uri())
        pg.wait_for_selector('html[data-ready="1"]', timeout=30000)
        routes = ["#/overview", "#/graph", "#/boundary", "#/boundary?t=oos", "#/quality", "#/quality?t=all", "#/quality?t=dist",
                  "#/quality?t=fix", "#/quality?t=std", "#/quality?t=gold"]
        for r in routes:
            pg.evaluate(f"location.hash='{r}'")
            pg.wait_for_timeout(700)
            info = pg.evaluate(
                """(() => { const p = document.querySelector('.page.on'); const t = p.querySelector('.tabpane.on');
                  return { n: document.querySelectorAll('.page.on').length, id: p.id, doc: document.documentElement.scrollHeight - innerHeight,
                           page: p.scrollHeight - p.clientHeight, tab: t ? t.scrollHeight - t.clientHeight : 0, vis: p.getBoundingClientRect().height }; })()"""
            )
            want = "p-" + r[2:].split("?")[0]
            assert info["id"] == want and info["n"] == 1, (r, info)
            assert info["doc"] <= 1 and info["page"] <= 1 and info["tab"] <= 2, (r, info)
        # 页间状态保留：选中的知识点与课时回到图谱页后仍在
        pg.evaluate("location.hash='#/graph'")
        pg.wait_for_timeout(500)
        pg.evaluate("VC.select(10); VC.Timeline.setLesson(120, true)")
        pg.evaluate("location.hash='#/quality'")
        pg.wait_for_timeout(300)
        pg.evaluate("location.hash='#/graph'")
        pg.wait_for_timeout(500)
        assert pg.evaluate("[VC.S.sel, VC.S.lesson]") == [10, 120]
        assert not errs and not ext, (errs, ext)
    finally:
        b.close()
        p.stop()
