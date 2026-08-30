#!/usr/bin/env python3
"""
ocr_official_scripts.py — macOS Vision OCR（适用印刷体；官方 sample 是手写体故暂停）

工程完成状态
------------
- ✅ 整页 Quartz 渲染（_create_bitmap_context 自动探测可用 alpha 实参）
- ✅ macOS Vision OCR（精确模式 + 关闭语言纠正，避免把考生语法错误"修正"成对的）
- ✅ 页眉/页脚噪声过滤
- ✅ 行尾 hyphen 接词
- ✅ 正文与 Examiner comment 同页时，用 word-level LCS 反向切除混入的 comment
- ❌ 官方 sample scripts 是**手写扫描件**（676×709 像素，JPEG 2000 格式），
       Vision 在手写体上识别率与置信度都不达标（conf 恒为 1.00 是 Vision
       的固有不靠谱信号，与实际质量不挂钩）。这条线在官方源上**暂停**。

未来如何复活
------------
- 若改用其他源（印刷体 PDF/图片）：本脚本可直接复用，无需改动。
- 若必须 OCR 手写体：路径是商业 OCR（Google Cloud Vision / Azure Read API），
  但 23 篇涉及版权与跨境数据流，**不要上**，留作人工转录评估性价比的判断。
- 若考虑人工转录：23 篇 × ~250 词 ≈ 6,000 词手写转录，2-3h 工作量，
  但 Task 2 仅 10 例，统计功效极弱（n=10 的 QWK 置信区间宽 ±0.2-0.3），
  官方只给 overall 不给四维 —— 投入产出比偏低，建议放弃。

历史教训（已写进跨项目记忆）
----------------------------
- **Vision 的 confidence 不可信**（手写体上恒为 1.00）
- 验证 OCR 质量**必须看实际输出**，不能只信 confidence
- 决策前要**先看样本**，再选技术路径（用户选「macOS Vision」时没人知道素材是手写体）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import Vision
import Quartz
from Foundation import NSURL
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "writing" / "official-sample"

PDFS = {
    "academic-2023": RAW / "academic-writing-sample-tasks-2023.pdf",
    "general-training-2023": RAW / "general-writing-sample-tasks-2023.pdf",
}

# CGPDFBox 常量：0 = kCGPDFMediaBox
K_CGPDF_MEDIA_BOX = 0

# CGBitmapInfo 的 alpha 取值**必须运行时探测，不能硬编码**。
# 实测（macOS + pyobjc 12.2）：alpha=3（kCGImageAlphaPremultipliedLast）会让
# CGBitmapContextCreate 直接返回 None，而 5/2/1/6 可用。
# 不同 macOS 版本可用的组合并不一致，所以按优先级逐个试，并把实际生效值记下来。
_ALPHA_CANDIDATES = (5, 2, 1, 6, 3, 4, 0, 7)   # 5=kCGImageAlphaNoneSkipLast 优先（无 alpha 通道）


def _create_bitmap_context(w: int, h: int, cs):
    """按候选顺序探测可用的 CGBitmapInfo，返回 (ctx, alpha) 或 (None, None)。"""
    for alpha in _ALPHA_CANDIDATES:
        try:
            ctx = Quartz.CGBitmapContextCreate(None, w, h, 8, 0, cs, alpha)
        except Exception:                       # noqa: BLE001
            continue
        if ctx is not None:
            return ctx, alpha
    return None, None

# 页眉页脚噪声：OCR 会把它们一起读出来，必须滤掉
NOISE_PATTERNS = [
    re.compile(r"^Page\s+\d+\s+of\s+\d+", re.I),
    re.compile(r"^IELTS\.org\s*$", re.I),
    re.compile(r"^(Academic|General Training)\s+Writing\s+Sample\s+Task", re.I),
    re.compile(r"^Sample\s*Script\s*[A-D]\s*$", re.I),
    re.compile(r"^Examiner\s+comment\s*$", re.I),
    re.compile(r"^Band\s*\d(\.\d)?\s*$", re.I),
]


def render_page(pdf_path: str, page_index: int, scale: float = 3.0):
    """把 PDF 第 page_index 页（0-based）渲染成 CGImage。

    scale 是关键参数：官方 PDF 正文约 10pt，1x 渲染后 OCR 会明显掉字，
    3x 起稳定。再往上收益递减但内存和时间线性上升，4x 以上不推荐。
    """
    url = NSURL.fileURLWithPath_(str(pdf_path))
    doc = Quartz.CGPDFDocumentCreateWithURL(url)
    if doc is None:
        raise RuntimeError(f"无法打开 PDF: {pdf_path}")
    page = Quartz.CGPDFDocumentGetPage(doc, page_index + 1)   # Quartz 是 1-based
    if page is None:
        raise RuntimeError(f"无法取到第 {page_index + 1} 页")

    rect = Quartz.CGPDFPageGetBoxRect(page, K_CGPDF_MEDIA_BOX)
    w = max(int(rect.size.width * scale), 1)
    h = max(int(rect.size.height * scale), 1)

    cs = Quartz.CGColorSpaceCreateDeviceRGB()
    ctx, alpha = _create_bitmap_context(w, h, cs)
    if ctx is None:
        raise RuntimeError(
            f"创建位图上下文失败：所有 CGBitmapInfo 候选 {_ALPHA_CANDIDATES} 均不可用 "
            f"（{w}x{h}）")

    # 白底：PDF 透明区域渲染成黑底会让 OCR 完全失效
    white = Quartz.CGColorCreateGenericRGB(1.0, 1.0, 1.0, 1.0)
    Quartz.CGContextSetFillColorWithColor(ctx, white)
    Quartz.CGContextFillRect(ctx, Quartz.CGRectMake(0, 0, w, h))

    Quartz.CGContextScaleCTM(ctx, scale, scale)
    Quartz.CGContextDrawPDFPage(ctx, page)
    return Quartz.CGBitmapContextCreateImage(ctx), alpha


def ocr_cgimage(cg_image) -> tuple[str, float]:
    """对单张 CGImage 做 OCR，返回 (文本, 平均置信度)。"""
    ci_image = Quartz.CIImage.imageWithCGImage_(cg_image)
    handler = Vision.VNImageRequestHandler.alloc().initWithCIImage_options_(ci_image, None)

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(0)      # 0 = accurate（1 = fast，别用，会掉字）
    request.setUsesLanguageCorrection_(False)   # 关掉语言纠正：它会"修正"考生的语法错误，
                                               # 而我们正是要靠这些错误评分
    ok, err = handler.performRequests_error_([request], None)
    if not ok:
        raise RuntimeError(f"Vision 请求失败: {err}")

    lines, confs = [], []
    for obs in (request.results() or []):
        cands = obs.topCandidates_(1)
        if cands and len(cands) > 0:
            top = cands[0]
            lines.append(top.string())
            confs.append(top.confidence())
    return "\n".join(lines), (sum(confs) / len(confs) if confs else 0.0)


def _norm_words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def strip_comment_block(ocr_text: str, comment_text: str, min_span: int = 25):
    """把整页 OCR 结果里混入的 Examiner comment 砍掉，返回 (正文, 切除长度)。

    为什么用连续重合而不是相似度：正文与评语是两份独立文本拼接，
    整体相似度会被正文稀释；而**交界处必然有一段连续重合**（评语第一行起）。
    找到这段连续串，从它出现的位置往后全砍即可。

    min_span=25 是保守值：低于它可能是巧合重合，宁可留着让人工核。
    """
    o = _norm_words(ocr_text)
    c = _norm_words(comment_text)
    if not o or not c:
        return ocr_text, 0

    # 词级最长公共子串（dp 用滚动数组，量级几千，毫秒级）
    prev = [0] * (len(c) + 1)
    best_len, best_o = 0, -1
    for i in range(1, len(o) + 1):
        cur = [0] * (len(c) + 1)
        oi = o[i - 1]
        for j in range(1, len(c) + 1):
            if oi == c[j - 1]:
                v = prev[j - 1] + 1
                cur[j] = v
                if v > best_len:
                    best_len, best_o = v, i - v
        prev = cur

    if best_len < min_span:
        return ocr_text, 0

    # 把"词下标"映射回"字符下标"：跳过 best_o 个词
    count = 0
    for m in re.finditer(r"[A-Za-z0-9]+", ocr_text):
        count += 1
        if count > best_o:
            return ocr_text[: m.start()].rstrip(), best_len
    return ocr_text, 0


def is_noise(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    return any(p.match(s) for p in NOISE_PATTERNS)


def clean_ocr(text: str) -> str:
    """去页眉页脚，并把 OCR 常见的断行错误接回去。"""
    lines = [l.rstrip() for l in text.split("\n")]
    lines = [l for l in lines if not is_noise(l)]

    # OCR 常在行尾断词（hyphen），以及把段落强行断行。
    # 这里只处理行尾连字符，其余断行保留 —— 因为段落边界本身是有用的信号。
    out, i = [], 0
    while i < len(lines):
        cur = lines[i]
        if (cur.endswith("-") and i + 1 < len(lines)
                and lines[i + 1][:1].islower()):
            out.append(cur[:-1] + lines[i + 1])
            i += 2
        else:
            out.append(cur)
            i += 1
    return "\n".join(out).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=float, default=3.0, help="渲染放大倍数，默认 3.0")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个 script（调试用）")
    ap.add_argument("--dry-run", action="store_true", help="只列出待处理页，不做 OCR")
    args = ap.parse_args()

    manifest_path = RAW / "official-sources.json"
    if not manifest_path.exists():
        sys.exit(f"[fatal] 缺少 {manifest_path}")
    cases = json.loads(manifest_path.read_text(encoding="utf-8"))["cases"]

    comments = {}
    cpath = RAW / "comments.jsonl"
    if cpath.exists():
        for line in cpath.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                comments[r["id"]] = r

    if args.limit:
        cases = cases[: args.limit]

    out_path = RAW / "scripts-ocr.jsonl"
    results, errors = [], []

    for case in cases:
        pdf = PDFS.get(case["source_id"])
        if pdf is None or not pdf.exists():
            errors.append(f"{case['id']}: PDF 不存在")
            continue

        # 作文正文在 pdf_pages 中**不含 Examiner comment 的那些页**。
        # 注意 comments.jsonl 里的字段名是 comment_page（单数）。
        cm = comments.get(case["id"])
        comment_page = (cm or {}).get("comment_page")
        comment_text = (cm or {}).get("comment", "")

        script_pages = [p for p in case["pdf_pages"] if p != comment_page]
        same_page = not script_pages          # 正文与 comment 挤在同一页（学术版 script-a 常见）
        if same_page:
            script_pages = list(case["pdf_pages"])
        if not script_pages:
            errors.append(f"{case['id']}: 无可用正文页")
            continue

        if args.dry_run:
            print(f"{case['id']:<40} 正文页 {script_pages}  band {case['official_band']}")
            continue

        texts, confs = [], []
        for pno in script_pages:
            try:
                img, _alpha = render_page(str(pdf), pno - 1, args.scale)
                t, c = ocr_cgimage(img)
                if t.strip():
                    texts.append(t)
                    confs.append(c)
            except Exception as e:                      # noqa: BLE001
                errors.append(f"{case['id']} p{pno}: {str(e)[:100]}")
                continue

        if not texts:
            errors.append(f"{case['id']}: OCR 无输出")
            continue

        body = clean_ocr("\n".join(texts))

        # 正文与 comment 同页时，整页 OCR 会把 comment 一起读进来。
        # 用「最长连续重合片段」把 comment 文本从结果里剔除：
        # 只要 OCR 文本与 comment 有 >=25 词的连续重合，就砍掉重合部分及其之后的内容
        # （comment 通常排在正文之后）。
        removed_span = 0
        if same_page and comment_text:
            body, removed_span = strip_comment_block(body, comment_text)

        words = len(re.findall(r"[A-Za-z][A-Za-z'\-]*", body))
        if words < 30:
            errors.append(f"{case['id']}: OCR 仅得 {words} 词，疑似失败（页 {script_pages}）")
        if same_page and removed_span == 0:
            errors.append(f"{case['id']}: 同页但未检出 comment 重合，"
                          f"正文可能混入评语，需人工核对")

        results.append({
            "id": case["id"],
            "test_type": case["test_type"],
            "task": case["task"],
            "task_code": case["task_code"],
            "script": case["script"],
            "official_band": case["official_band"],
            "script_pages": script_pages,
            "ocr_engine": "macos-vision",
            "render_scale": args.scale,
            "avg_confidence": round(sum(confs) / len(confs), 4) if confs else None,
            "word_count": words,
            "essay_text": body,
            "reviewed_by_human": False,
        })
        print(f"{case['id']:<40} 页{str(script_pages):<10} {words:>4} 词  "
              f"conf={sum(confs)/len(confs):.2f}" if confs else
              f"{case['id']:<40} 页{str(script_pages):<10} {words:>4} 词")

    if args.dry_run:
        return 0

    stamp = datetime.now(timezone(timedelta(hours=8))).isoformat()
    with out_path.open("w", encoding="utf-8") as fh:
        for r in results:
            r["extracted_at"] = stamp
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    t2 = [r for r in results if r["task"] == "task-2"]
    print("-" * 72)
    print(f"OCR 完成 {len(results)} 篇（Task1 {len(results)-len(t2)} / Task2 {len(t2)}）"
          f"，平均 {sum(r['word_count'] for r in results)/max(len(results),1):.0f} 词")
    if errors:
        print(f"\n[问题 {len(errors)} 条]")
        for e in errors:
            print(f"  · {e}")
    print(f"\n→ {out_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
