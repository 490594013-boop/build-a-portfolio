#!/usr/bin/env python3
"""从一批 SVG 里按阅读顺序提取文字，并检测重复长句。

用法:
    python3 extract_svg_text.py <目录或单个svg> [--out all-text.md] [--dup-only] [--min-dup 14]

为什么需要它:
    Figma 导出的 PDF 常常把文字转成轮廓（无文字层），无法用 PDF 工具提取。
    但导出的 SVG 通常保留 <text>，所以「SVG 提文字」是审查作品集文案最可靠的路径。

输出:
    - 按 y 分行、行内按 x 排序后的全文（Markdown）
    - 重复长句清单（同页出现 ≥2 次 = 疑似复制粘贴错误）
"""
import argparse
import html
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

SVG_NS = "{http://www.w3.org/2000/svg}"
LINE_TOLERANCE = 8.0   # 同一行允许的 y 偏差

# 代码纹理 / 页眉这类底纹文字，参与重复检测只会制造噪音
CODE_HINT = re.compile(r"(=>|\bconst \b|\bfunction\b|\bexport\b|\.slot\[|;\s*$|\{\s*|\}\s*|await\s|\.filter\()")


def is_decorative(text):
    """判断是否像代码纹理 / 底纹（避免误报为重复文案）。"""
    if CODE_HINT.search(text):
        return True
    # 纯 ASCII（无中文）且较长 → 多半是代码行
    if len(text) >= 14 and not re.search(r"[\u4e00-\u9fff]", text):
        return True
    return False


def _num(el, key, default):
    v = el.get(key)
    if v is None:
        return default
    try:
        return float(re.sub(r"[^\d.\-]", "", v))
    except ValueError:
        return default


def extract_page(svg_path):
    """返回 (viewBox, [行文本...])"""
    tree = ET.parse(svg_path)
    root = tree.getroot()
    vb = root.get("viewBox", "")
    items = []  # (y, x, text)

    for t in root.iter(SVG_NS + "text"):
        tx, ty = _num(t, "x", 0.0), _num(t, "y", 0.0)
        segs = []
        for sp in t.iter(SVG_NS + "tspan"):
            sx = _num(sp, "x", tx)
            sy = _num(sp, "y", ty)
            txt = "".join(sp.itertext()).strip()
            if txt and _num(sp, "font-size", 1) != 0:
                segs.append((sy, sx, txt))
        if not segs:
            txt = "".join(t.itertext()).strip()
            if txt and _num(t, "font-size", 1) != 0:
                segs.append((ty, tx, txt))
        items.extend(segs)

    items.sort(key=lambda v: (v[0], v[1]))
    lines = []  # [[y, [(x, text)]], ...]
    for y, x, txt in items:
        if lines and abs(y - lines[-1][0]) <= LINE_TOLERANCE:
            lines[-1][1].append((x, txt))
        else:
            lines.append([y, [(x, txt)]])

    out = []
    for _, seg in lines:
        seg.sort(key=lambda v: v[0])
        out.append("".join(s for _, s in seg).strip())
    return vb, [ln for ln in out if ln]


def collect(paths):
    pages = []
    for p in paths:
        try:
            vb, lines = extract_page(p)
        except ET.ParseError as e:
            print(f"  ⚠ 解析失败 {os.path.basename(p)}: {e}", file=sys.stderr)
            continue
        pages.append((p, vb, lines))
    return pages


def find_dups(pages, min_len, boilerplate_pages=3):
    """返回 (同页重复, 跨页重复, 视为底纹而忽略的条目)。

    同页重复 = 一句话在同一页出现 ≥2 次 → 几乎必然是复制粘贴错误（高置信）。
    跨页重复 = 出现在 2 页 → 可能是"机会点在下一页重述"这类合理重复（需人工判断）。
    出现在 ≥boilerplate_pages 页 = 底纹 / 页眉 / 代码纹理，忽略。
    """
    within, across = {}, defaultdict(set)
    for path, _, lines in pages:
        name = os.path.basename(path)
        keys = [re.sub(r"\s+", "", ln) for ln in lines]
        cnt = Counter(k for k in keys if len(k) >= min_len and not is_decorative(k))
        for k, c in cnt.items():
            if c >= 2:
                within[k] = (name, c)
            across[k].add(name)

    boiler = {k: sorted(v) for k, v in across.items() if len(v) >= boilerplate_pages}
    cross = {k: sorted(v) for k, v in across.items()
             if 2 <= len(v) < boilerplate_pages and k not in within}
    return within, cross, boiler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="SVG 目录或单个 SVG 文件")
    ap.add_argument("--out", default=None, help="输出 Markdown 文件")
    ap.add_argument("--dup-only", action="store_true", help="只报告重复长句")
    ap.add_argument("--min-dup", type=int, default=14, help="判定重复的最小字数")
    args = ap.parse_args()

    if os.path.isdir(args.target):
        files = sorted(
            (os.path.join(args.target, f) for f in os.listdir(args.target) if f.lower().endswith(".svg")),
            key=lambda p: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", os.path.basename(p))],
        )
    else:
        files = [args.target]

    if not files:
        sys.exit("没有找到 SVG 文件")

    pages = collect(files)
    within, cross, boiler = find_dups(pages, args.min_dup)

    if args.dup_only:
        print(f"=== ① 同页重复（高置信，通常是复制粘贴错误）===")
        if not within:
            print("  ✅ 未发现")
        for txt, (page, c) in within.items():
            print(f"  ⚠ {page} ×{c}：{txt[:100]}")
        print(f"\n=== ② 跨页重复（可能是合理的重述，请人工判断）===")
        if not cross:
            print("  ✅ 未发现")
        for txt, where in cross.items():
            print(f"  · {where}：{txt[:100]}")
        print(f"\n（{len(boiler)} 条出现在 ≥3 页，视为页眉/底纹，已忽略）")
        return

    lines_out = []
    for path, vb, lines in pages:
        lines_out.append(f"\n\n{'=' * 72}\n## {os.path.basename(path)}   [{vb}]\n{'=' * 72}\n")
        lines_out.extend(ln + "\n" for ln in lines)

    lines_out.append(f"\n\n{'=' * 72}\n## 重复检测（≥{args.min_dup} 字）\n{'=' * 72}\n")
    lines_out.append("\n### ① 同页重复（高置信）\n")
    if within:
        for txt, (page, c) in within.items():
            lines_out.append(f"- ⚠ {page} ×{c}：{txt[:120]}\n")
    else:
        lines_out.append("✅ 未发现\n")
    lines_out.append("\n### ② 跨页重复（请人工判断是否合理）\n")
    if cross:
        for txt, where in cross.items():
            lines_out.append(f"- · {where}：{txt[:120]}\n")
    else:
        lines_out.append("✅ 未发现\n")
    lines_out.append(f"\n（另有 {len(boiler)} 条出现在 ≥3 页，视为页眉/底纹，已忽略）\n")

    text = "".join(lines_out)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"✅ 已写出 {args.out}")
        print(f"   共 {len(pages)} 页；同页重复 {len(within)} 条，跨页重复 {len(cross)} 条")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
