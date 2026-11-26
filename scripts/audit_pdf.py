#!/usr/bin/env python3
"""作品集 PDF 技术审计：尺寸 / 出血 / 文字层 / 图片 / 书签。

用法:
    python3 audit_pdf.py <pdf> [--expect-width 1920]

为什么需要它:
    作品集最常见的 6 个交付问题，肉眼都不容易发现：
      1. 某一页画布比别的页宽（内容是出血，导出时没裁）
      2. 整份没有文字层（文字被转成轮廓）
      3. 体积超标（截图按原始分辨率嵌入）
      4. 页面尺寸与设计稿不一致
      5. 没有书签
      6. 有页面缺页眉（编号/项目名）

依赖: pypdf  (pip install --target .pylibs pypdf)
"""
import argparse
import os
import sys
from collections import Counter

try:
    from pypdf import PdfReader
except ImportError:
    sys.exit("缺少 pypdf：python3 -m pip install --target .pylibs pypdf\n"
             "然后设 PYTHONPATH=.pylibs 或在脚本里 sys.path.insert(0, '.pylibs')")


def _stream_size(o):
    """图片流的存储字节数。/Length 常常读不到（对象流压缩），退化用 get_data()。"""
    try:
        n = int(o.get("/Length", 0) or 0)
    except (TypeError, ValueError):
        n = 0
    if n > 0:
        return n
    try:
        return len(o.get_data())
    except Exception:
        return 0

def page_images(pg):
    """返回 [(w, h, bytes)]，同时深入 Form XObject 一层。"""
    found = []
    res = pg.get("/Resources")
    res = res.get_object() if res else {}
    xo = res.get("/XObject")
    if not xo:
        return found
    xo = xo.get_object()
    for name in list(xo.keys()):
        o = xo[name]
        o = o.get_object() if hasattr(o, "get_object") else o
        sub = o.get("/Subtype")
        if sub == "/Image":
            found.append((int(o.get("/Width", 0)), int(o.get("/Height", 0)), _stream_size(o)))
        elif sub == "/Form":
            fres = o.get("/Resources")
            fres = fres.get_object() if fres else {}
            fxo = fres.get("/XObject")
            if fxo:
                fxo = fxo.get_object()
                for n2 in list(fxo.keys()):
                    o2 = fxo[n2]
                    o2 = o2.get_object() if hasattr(o2, "get_object") else o2
                    if o2.get("/Subtype") == "/Image":
                        found.append((int(o2.get("/Width", 0)), int(o2.get("/Height", 0)), _stream_size(o2)))
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--expect-width", type=float, default=None,
                    help="期望页宽（如 1920）；不给则用出现最多的宽度当基准")
    args = ap.parse_args()

    if not os.path.exists(args.pdf):
        sys.exit(f"文件不存在: {args.pdf}")

    size_mb = os.path.getsize(args.pdf) / 1024 / 1024
    r = PdfReader(args.pdf)
    n = len(r.pages)

    widths = [float(p.mediabox.width) for p in r.pages]
    base_w = args.expect_width or Counter(round(w) for w in widths).most_common(1)[0][0]

    print(f"=== {os.path.basename(args.pdf)} ===")
    print(f"体积 {size_mb:.2f} MB / {n} 页 / 基准宽度 {base_w:.0f}\n")

    print(f"{'页':<5}{'尺寸':<16}{'宽':<10}{'文字层':<8}{'图片':<6}{'图片数据':<10}备注")
    problems = []
    total_img = 0
    for i, pg in enumerate(r.pages, 1):
        w, h = float(pg.mediabox.width), float(pg.mediabox.height)
        try:
            txt = (pg.extract_text() or "").strip()
        except Exception:
            txt = ""
        imgs = page_images(pg)
        isum = sum(b for _, _, b in imgs)
        total_img += isum
        notes = []
        if abs(w - base_w) > 0.5:
            notes.append(f"⚠ 比其他页宽 {w - base_w:+.0f}px（疑似出血）")
            problems.append(f"p{i} 宽度 {w:.0f} ≠ {base_w:.0f}")
        big = [(iw, ih) for iw, ih, _ in imgs if iw > w * 2]
        if big:
            notes.append(f"最大图 {big[0][0]}x{big[0][1]}（≥2× 页宽，可能过采样）")
        print(f"p{i:<4}{f'{w:.0f} x {h:.0f}':<16}{w:<10.0f}"
              f"{'有' if txt else '无':<8}{len(imgs):<6}{isum/1024/1024:>5.2f} MB  {'; '.join(notes)}")

    print(f"\n图片数据合计 {total_img/1024/1024:.2f} MB（占文件 {total_img/1024/1024/max(size_mb,0.01)*100:.0f}%）")

    # 文字层整体判断
    with_text = sum(1 for pg in r.pages if (pg.extract_text() or "").strip())
    print(f"文字层：{with_text}/{n} 页有文字")
    if with_text == 0:
        print("  ⚠ 整份无文字层（文字已转轮廓或为截图）")
        print("    → 面试人工翻看可接受；要投递平台/可搜索，需导 SVG 或另出带文字层版本")

    # 书签
    print("\n=== 书签 ===")
    try:
        ol = r.outline
    except Exception:
        ol = None
    if not ol:
        print("  ⚠ 无书签，建议每页加一条（build_pdf.py 可自动加）")
    else:
        cnt = [0]

        def walk(o, d=0):
            for it in o:
                if isinstance(it, list):
                    walk(it, d + 1)
                else:
                    print("  " * d + f"· {it.title}")
                    cnt[0] += 1
        walk(ol)
        print(f"  共 {cnt[0]} 条")

    # 体积结论
    print("\n=== 结论 ===")
    if size_mb > 20:
        print(f"  ⚠ {size_mb:.1f} MB 超过多数平台上限，建议压到 10 MB 内（scripts/build_pdf.py）")
    elif size_mb > 10:
        print(f"  △ {size_mb:.1f} MB 在 10–20 MB 之间，部分平台可能拒绝")
    else:
        print(f"  ✅ {size_mb:.1f} MB 体积合格")
    if problems:
        print("  需修：")
        for p in problems:
            print(f"    - {p}")
    else:
        print("  ✅ 所有页宽度一致")


if __name__ == "__main__":
    main()
