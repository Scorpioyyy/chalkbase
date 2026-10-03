"""用 Playwright 驱动 viz/index.html：生成 README 引用的截图（reports/screenshots/）并做冒烟与版式断言。

断言（1440×900、1280×720、1920×1080 各跑一遍）：
- 无控制台错误；所有非 file 请求被拦截后页面仍可用；
- 路由 #/overview #/graph #/boundary #/quality（含二级标签）同一时刻只显示一页，整页无纵向滚动；
- URL hash 往返、键盘快捷键（/ 与 Esc）。

用法：python scripts/viz_screenshots.py [--all-sizes]
需要 `pip install playwright && python -m playwright install chromium`。默认只在 1440×900 下断言并出图，
`--all-sizes` 额外在 1280×720、1920×1080 下断言。
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
URL = (ROOT / "viz" / "index.html").as_uri()
OUT = ROOT / "reports" / "screenshots"
ROUTES = [
    "#/overview", "#/graph", "#/boundary", "#/boundary?t=oos",
    "#/quality", "#/quality?t=all", "#/quality?t=dist", "#/quality?t=fix", "#/quality?t=std", "#/quality?t=gold",
]


def open_page(browser, w, h):
    pg = browser.new_context(viewport={"width": w, "height": h}).new_page()
    errs, ext = [], []
    pg.on("console", lambda m: errs.append(m.text) if m.type in ("error", "warning") else None)
    pg.on("pageerror", lambda e: errs.append("PAGEERROR " + str(e)))

    def route(r):
        if r.request.url.startswith("file:"):
            r.continue_()
        else:
            ext.append(r.request.url)
            r.abort()

    pg.route("**/*", route)
    return pg, errs, ext


def check_layout(pg, w, h, dark=False):
    """每个路由：恰有一页可见，整页与该页都不产生纵向滚动。"""
    bad = []
    for r in ROUTES:
        pg.evaluate(f"location.hash='{r}'")
        pg.wait_for_timeout(1300)
        info = pg.evaluate(
            """(() => { const p = document.querySelector('.page.on'); const t = p.querySelector('.tabpane.on');
              return { n: document.querySelectorAll('.page.on').length, doc: document.documentElement.scrollHeight - innerHeight,
                       page: p.scrollHeight - p.clientHeight, tab: t ? t.scrollHeight - t.clientHeight : 0, id: p.id }; })()"""
        )
        if info["n"] != 1 or info["doc"] > 1 or info["page"] > 1 or info["tab"] > 2:
            bad.append((r, info))
    return bad


def main():
    all_sizes = "--all-sizes" in sys.argv
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        # ---- 版式断言
        for (w, h) in ([(1440, 900), (1280, 720), (1920, 1080)] if all_sizes else [(1440, 900)]):
            pg, errs, ext = open_page(b, w, h)
            pg.goto(URL)
            pg.wait_for_selector('html[data-ready="1"]')
            pg.wait_for_timeout(1200)
            bad = check_layout(pg, w, h)
            print(f"{w}x{h}: 版式问题 {bad or '无'}")
            assert not errs and not ext, (errs, ext)
            assert not bad, bad
            pg.context.close()

        # ---- README 截图（1440×900）
        pg, errs, ext = open_page(b, 1440, 900)
        pg.goto(URL + "#/overview")
        pg.wait_for_selector('html[data-ready="1"]')
        pg.wait_for_timeout(5200)
        pg.screenshot(path=str(OUT / "01-overview.png"))

        pg.evaluate("location.hash='#/graph'")
        pg.wait_for_timeout(1500)
        idx = pg.evaluate("VC.D.kps.find(k=>k.n==='两位数乘一位数的口算').i")
        pos = pg.evaluate(
            f"(()=>{{const p=VC.pan,n=p.nodes[{idx}];const r=p.cv.getBoundingClientRect();return [r.left+p.tr.x+p.tr.k*n.x,r.top+p.tr.y+p.tr.k*n.y]}})()"
        )
        pg.mouse.move(pos[0], pos[1])
        pg.wait_for_timeout(1000)
        pg.screenshot(path=str(OUT / "02-panorama.png"))
        pg.mouse.move(5, 300)
        pg.evaluate(f"VC.select({idx})")
        pg.wait_for_timeout(1800)
        pg.screenshot(path=str(OUT / "02b-panorama-selected.png"))
        pg.evaluate("VC.setView('focus')")
        pg.wait_for_timeout(1700)
        pg.evaluate("document.querySelector('#focus-svg .fedge-hit').dispatchEvent(new MouseEvent('click',{bubbles:true}))")
        pg.wait_for_timeout(700)
        pg.screenshot(path=str(OUT / "03-focus.png"))

        pg.evaluate("location.hash='#/boundary'")
        pg.wait_for_timeout(500)
        pg.evaluate("VC.Timeline.toggle(false); VC.Timeline.setLesson(250,true)")
        pg.wait_for_timeout(1500)
        pg.screenshot(path=str(OUT / "04-boundary.png"))
        pg.evaluate("location.hash='#/boundary?t=oos'")
        pg.wait_for_timeout(2200)
        pg.screenshot(path=str(OUT / "04b-boundary-oos.png"))

        pg.evaluate("location.hash='#/quality'")
        pg.wait_for_timeout(2600)
        pg.screenshot(path=str(OUT / "05-quality.png"))
        pg.evaluate("location.hash='#/quality?t=all'")
        pg.wait_for_timeout(2400)
        pg.screenshot(path=str(OUT / "05b-quality-metrics.png"))

        # ---- 深色模式一张
        pg.evaluate("VC.Timeline.off(); VC.setTheme('dark'); location.hash='#/graph'")
        pg.wait_for_timeout(1500)
        pg.evaluate("VC.select(null)")
        pg.wait_for_timeout(600)
        pg.screenshot(path=str(OUT / "06-graph-dark.png"))
        if all_sizes:
            print("深色模式版式问题", check_layout(pg, 1440, 900, dark=True) or "无")

        # ---- hash 往返与快捷键
        pg.evaluate("location.hash='#/graph'")
        pg.wait_for_timeout(1200)
        h = pg.evaluate("VC.select(%d); VC.setView('focus'); VC.hashSync(); new Promise(r=>setTimeout(()=>r(location.hash),400))" % idx)
        print("hash", h)
        pg2, errs2, ext2 = open_page(b, 1440, 900)
        pg2.goto(URL + h)
        pg2.wait_for_selector('html[data-ready="1"]')
        pg2.wait_for_timeout(900)
        got = pg2.evaluate("[VC.page, VC.S.view, VC.S.sel]")
        assert got == ["graph", "focus", idx], got
        pg2.keyboard.press("Escape")
        assert pg2.evaluate("VC.S.view") == "pan"
        pg2.keyboard.press("/")
        assert pg2.evaluate("document.activeElement.id") == "q"
        assert not errs and not ext and not errs2 and not ext2, (errs, ext, errs2, ext2)
        print("OK")
        b.close()


if __name__ == "__main__":
    main()
