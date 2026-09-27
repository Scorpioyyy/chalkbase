"""检查单本教材 PDF 的指定页：有文字层则打印文字，否则渲染为图片存入 tmp/renders/。

用法：
  python scripts/inspect_book.py "<pdf path>" --pages 1-8
  python scripts/inspect_book.py "<pdf path>" --pages 1-8 --force-render
  python scripts/inspect_book.py "<pdf path>" --pages 40 --render-out-dir tmp/renders/g1b

页码为 1-indexed 的 PDF 页序（与 PageRange.pdf_start/pdf_end 一致）。
"""
import argparse
import sys
from pathlib import Path

import pymupdf

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def parse_pages(spec: str, n_pages: int) -> list[int]:
    result = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            result.extend(range(int(a), int(b) + 1))
        else:
            result.append(int(part))
    return [p for p in result if 1 <= p <= n_pages]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf_path")
    ap.add_argument("--pages", required=True, help="如 1-8 或 1,3,5")
    ap.add_argument("--force-render", action="store_true", help="即使有文字层也渲染图片")
    ap.add_argument("--render-out-dir", default=None, help="默认 tmp/renders/<pdf stem>")
    ap.add_argument("--dpi", type=int, default=150)
    args = ap.parse_args()

    doc = pymupdf.open(args.pdf_path)
    n = len(doc)
    pages = parse_pages(args.pages, n)

    sample_text_len = len(doc[min(1, n - 1)].get_text().strip()) + len(doc[min(n // 2, n - 1)].get_text().strip())
    has_text_layer = sample_text_len > 20

    print(f"# pdf={args.pdf_path} total_pages={n} has_text_layer={has_text_layer}", file=sys.stderr)

    out_dir = Path(args.render_out_dir) if args.render_out_dir else Path("tmp/renders") / Path(args.pdf_path).stem
    if not has_text_layer or args.force_render:
        out_dir.mkdir(parents=True, exist_ok=True)
        mat = pymupdf.Matrix(args.dpi / 72, args.dpi / 72)
        for p in pages:
            page = doc[p - 1]
            pix = page.get_pixmap(matrix=mat)
            out_path = out_dir / f"p{p:03d}.png"
            pix.save(str(out_path))
            print(f"rendered {out_path}")
    else:
        for p in pages:
            page = doc[p - 1]
            print(f"===== PDF page {p} (0-indexed {p - 1}) =====")
            print(page.get_text())

    doc.close()


if __name__ == "__main__":
    main()
