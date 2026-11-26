#!/usr/bin/env python3
"""作品集 PDF：合并 + 选择性栅格化压缩 + 加书签，并向目标体积收敛。

用法:
    python3 build_pdf.py --in <单页PDF目录> --out final.pdf \
        --raster 4,7,8,9,10,11,12,13 --target-mb 10 --titles titles.txt

原理:
    作品集体积几乎全部来自「截图页」。纯矢量页很轻（每页 <0.5MB），要保留矢量以保证文字锐利；
    截图页本身已是位图，按「页面原生宽度」用高质量 JPEG 重编码，视觉几乎无损但体积能降 5–8 倍。

    实测参考：1920×3917 的截图长页 6.8MB → 1920 宽 JPEG q92 → 0.87MB，
    与原图平均像素差 0.73/255（差异 >12 的像素仅 0.62%）。

依赖:
    pypdf  (+ 系统自带 sips，用于 PDF→JPEG→PDF 的重编码)
    --titles 文件：每行一个书签标题，顺序对应合并顺序；不给则用「第 N 页」
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    sys.exit("缺少 pypdf：python3 -m pip install --target .pylibs pypdf")


def natural_key(name):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", name)]


def find_pages(src):
    if os.path.isdir(src):
        files = [f for f in os.listdir(src) if f.lower().endswith(".pdf")]
        return [os.path.join(src, f) for f in sorted(files, key=natural_key)]
    return [src]


def page_index_of(path, fallback):
    """从文件名里抽数字当页序号，如 第7页.pdf -> 7"""
    m = re.search(r"(\d+)", os.path.basename(path))
    return int(m.group(1)) if m else fallback


def reencode(pdf_path, out_pdf, width, quality, tmpdir):
    """PDF 页 -> JPEG(指定宽度/质量) -> PDF。返回是否成功。"""
    stem = os.path.splitext(os.path.basename(out_pdf))[0]
    jpg = os.path.join(tmpdir, stem + ".jpg")
    try:
        subprocess.run(
            ["sips", "-s", "format", "jpeg", "-s", "formatOptions", str(quality),
             "--resampleWidth", str(width), pdf_path, "--out", jpg],
            check=True, capture_output=True)
        subprocess.run(["sips", "-s", "format", "pdf", jpg, "--out", out_pdf],
                       check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"  ⚠ sips 失败 {os.path.basename(pdf_path)}: {e}", file=sys.stderr)
        return False


def build(pages, raster_idx, width, quality, titles, out, tmpdir):
    w = PdfWriter()
    for i, p in enumerate(pages):
        idx = page_index_of(p, i)
        src = p
        if idx in raster_idx:
            dst = os.path.join(tmpdir, f"r{idx}.pdf")
            if reencode(p, dst, width, quality, tmpdir):
                src = dst
        r = PdfReader(src)
        w.add_page(r.pages[0])
        title = titles[i] if i < len(titles) else f"第 {i + 1} 页"
        w.add_outline_item(title, i)
    with open(out, "wb") as f:
        w.write(f)
    return os.path.getsize(out) / 1024 / 1024


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True, help="单页 PDF 目录 或 单个 PDF")
    ap.add_argument("--out", required=True)
    ap.add_argument("--raster", default="", help="要栅格化的页序号，逗号分隔（对应文件名里的数字）")
    ap.add_argument("--width", type=int, default=1920, help="栅格化宽度，默认页面原生宽度")
    ap.add_argument("--quality", type=int, default=92, help="起始 JPEG 质量")
    ap.add_argument("--target-mb", type=float, default=10.0)
    ap.add_argument("--titles", default=None, help="书签标题文件，每行一条")
    args = ap.parse_args()

    pages = find_pages(args.src)
    if not pages:
        sys.exit("没找到 PDF")

    raster_idx = {int(x) for x in args.raster.split(",") if x.strip().isdigit()}
    titles = []
    if args.titles and os.path.exists(args.titles):
        titles = [ln.strip() for ln in open(args.titles, encoding="utf-8") if ln.strip()]

    print(f"共 {len(pages)} 页；栅格化 {sorted(raster_idx) or '无'}")
    ladder = [args.quality]
    for q in (86, 80, 74):
        if q < args.quality:
            ladder.append(q)

    with tempfile.TemporaryDirectory() as tmpdir:
        for q in ladder:
            mb = build(pages, raster_idx, args.width, q, titles, args.out, tmpdir)
            print(f"  质量 {q} → {mb:.2f} MB")
            if mb <= args.target_mb:
                break
        n_pages = len(PdfReader(args.out).pages)
        print(f"\n✅ {args.out}")
        print(f"   {mb:.2f} MB / {n_pages} 页 / 书签 {len(titles) or n_pages} 条")
        if mb > args.target_mb:
            print(f"   ⚠ 仍未达到 {args.target_mb} MB 目标："
                  f"可把更多页加入 --raster，或把 --width 降到 1600 / 1440")


if __name__ == "__main__":
    main()
