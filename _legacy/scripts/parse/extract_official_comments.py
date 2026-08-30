#!/usr/bin/env python3
"""
extract_official_comments.py — 从官方写作样题 PDF 提取 Examiner comment

设计要点
--------
**以 manifest 为基准，PDF 解析为校验**，而不是反过来靠正则去猜。
manifest（official-sources.json）给出了每个 case 的 `official_band` 与 `pdf_pages`，
于是可以做一条**强校验**：从 PDF 里解析出的 Band 必须等于 manifest 的 official_band。
一旦版面错位、页码漂移、正则漏匹配，band 对不上就会被立刻抓出来 ——
这比"提取完肉眼看一遍"可靠得多。

版面结构（实测）
----------------
    学术版 : "Academic Writing Sample Task – 2A – Sample Script A"   （Task 后有 dash）
    培训类 : "General Training Writing Sample Task 1A – Sample Script A"（Task 后无 dash）

两种格式不同，正则里 dash 必须写成可选，否则培训类全漏。
（这是第一版的 bug，已修。）

    作文正文  : 渲染成图片，**无文本层**，要拿必须 OCR
    Examiner comment : 有文本层，直接可提

关于官方公布了什么
------------------
官方样题**只公布 overall band，不公布 TR/CC/LR/GRA 四维分数**。
所以这批材料的价值排序是：

    1. Examiner comment → rubric v1 的锚点素材（官方考官的具体评判语言）
    2. 作文正文（需 OCR）→ 评分器回归样本，但 Task 2 仅 10 例，统计功效弱

本脚本只做第 1 项。23 例中 Task 2 仅 10 例，这是硬约束，做验收设计时别忽略。

合规边界
--------
Examiner comment 属 IELTS Partners 版权材料，输出**仅存于 data/raw/**，
不进版本库、不随数据集分发。用途是**指导我们自写 paraphrase 版 rubric**，
不是直接复制 —— rubrics/ 下必须是自己的表述 + 操作化定义。

用法
----
    ./.venv/bin/python scripts/parse/extract_official_comments.py
    ./.venv/bin/python scripts/parse/extract_official_comments.py --task 2

输出
----
    data/raw/writing/official-sample/comments.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "writing" / "official-sample"
MANIFEST = RAW / "official-sources.json"

# source_id -> 本地文件名
LOCAL_PDF = {
    "academic-2023": "academic-writing-sample-tasks-2023.pdf",
    "general-training-2023": "general-writing-sample-tasks-2023.pdf",
}

# dash 必须可选：学术版 "Task – 2A –"，培训类 "Task 1A –"
HDR_SCRIPT = re.compile(
    r"Sample\s+Task\s*[–\-]?\s*(?P<code>[12][A-Z])\s*[–\-]\s*Sample\s*Script\s*(?P<script>[A-Z])",
    re.I,
)
HDR_BAND = re.compile(r"Band\s*(?P<band>\d(?:\.5)?)\b", re.I)
PAGE_FOOTER = re.compile(r"^Page\s+\d+\s+of\s+\d+\s+IELTS\.org\s*$", re.M)

# comment 的分维度信号词，仅用于人工审阅时快速定位，不作真值
DIM_HINTS = {
    "TR": ("task", "requirement", "position", "idea", "argument", "overview", "develop",
           "conclusion", "topic", "view", "opinion", "key feature", "bullet point", "purpose"),
    "CC": ("cohesion", "coherent", "progression", "paragraph", "linking", "linker",
           "referencing", "reference", "sequenc", "organis", "organiz", "easy to read"),
    "LR": ("vocabulary", "word choice", "collocation", "lexical", "spelling", "paraphras"),
    "GRA": ("grammar", "grammatical", "structure", "punctuation", "sentence", "clause",
            "subordination", "tense", "article"),
}


def clean(text: str) -> str:
    t = PAGE_FOOTER.sub("", text)
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()


def split_sentences(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text) if p.strip()]


def tag_dimensions(comment: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {k: [] for k in DIM_HINTS}
    for sent in split_sentences(comment):
        low = sent.lower()
        scores = {d: sum(1 for w in ws if w in low) for d, ws in DIM_HINTS.items()}
        best = max(scores, key=lambda k: scores[k])
        if scores[best] > 0:
            out[best].append(sent)
    return out


def load_manifest() -> list[dict]:
    if not MANIFEST.exists():
        sys.exit(f"[fatal] 缺少 manifest: {MANIFEST}\n"
                 f"  先下载: curl -o {MANIFEST} "
                 f"https://raw.githubusercontent.com/AustinWang668/ielts-writing-scorer/"
                 f"main/evals/official-sources.json")
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["cases"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", choices=["1", "2"], help="只输出某一 task（不传=全部）")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cases = load_manifest()
    if args.task:
        cases = [c for c in cases if c["task"] == f"task-{args.task}"]

    # 按 source 分组，每个 PDF 只打开一次
    readers: dict[str, PdfReader] = {}
    pages_cache: dict[str, list[str]] = {}
    for src, fname in LOCAL_PDF.items():
        path = RAW / fname
        if not path.exists():
            print(f"[warn] 未找到 {fname}，跳过其全部 case（先跑 fetch_official.py）")
            continue
        readers[src] = PdfReader(str(path))
        pages_cache[src] = [clean(p.extract_text() or "") for p in readers[src].pages]

    records, problems = [], []

    for case in cases:
        src = case["source_id"]
        if src not in pages_cache:
            continue
        pages = pages_cache[src]
        fname = LOCAL_PDF[src]

        # 在 manifest 指定页范围内定位 comment；comment 总在最后一页
        found = None
        for pno in case["pdf_pages"]:
            if pno < 1 or pno > len(pages):
                problems.append(f"{case['id']}: 页码 {pno} 越界（PDF 共 {len(pages)} 页）")
                continue
            m = HDR_BAND.search(pages[pno - 1])
            if m and abs(float(m.group("band")) - case["official_band"]) < 1e-9:
                found = (pno, m)
        if found is None:
            problems.append(
                f"{case['id']}: 在页 {case['pdf_pages']} 未找到与 official_band="
                f"{case['official_band']} 匹配的 Band 行")
            continue

        pno, m_band = found
        body = pages[pno - 1][m_band.end():]
        body = re.sub(r"^\s*[-–—]\s*", "", body).strip()
        body = re.sub(r"\s*\n\s*", " ", body).strip()
        if len(body) < 120:
            problems.append(f"{case['id']}: comment 过短（{len(body)} 字符），疑似解析错位")
            continue

        # 用 script 头做交叉验证（允许缺失，缺失不报错但记下来）
        hdr = None
        for p2 in case["pdf_pages"]:
            hdr = HDR_SCRIPT.search(pages[p2 - 1]) or hdr
        if hdr:
            if hdr.group("code").upper() != case["task_code"]:
                problems.append(f"{case['id']}: task_code 不符 "
                                f"(PDF={hdr.group('code')} manifest={case['task_code']})")
            if hdr.group("script").upper() != case["script"]:
                problems.append(f"{case['id']}: script 不符 "
                                f"(PDF={hdr.group('script')} manifest={case['script']})")
        else:
            problems.append(f"{case['id']}: 未匹配到 script 头（band 校验已通过，仅提示）")

        records.append({
            "id": case["id"],
            "test_type": case["test_type"],
            "task": case["task"],
            "task_code": case["task_code"],
            "script": case["script"],
            "official_band": case["official_band"],
            "band_verified": True,
            "pdf_pages": case["pdf_pages"],
            "comment_page": pno,
            "comment": body,
            "comment_word_count": len(body.split()),
            "dimension_hints": tag_dimensions(body),
            "source_pdf": fname,
            "source_url": None,
        })

    records.sort(key=lambda r: (r["test_type"], r["task"], r["task_code"], r["script"]))

    out_path = Path(args.out) if args.out else RAW / "comments.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone(timedelta(hours=8))).isoformat()
    with out_path.open("w", encoding="utf-8") as fh:
        for r in records:
            r["extracted_at"] = stamp
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"{'id':<40}{'band':<7}{'页':<8}词数")
    print("-" * 72)
    for r in records:
        print(f"{r['id']:<40}{r['official_band']:<7}{str(r['comment_page']):<8}{r['comment_word_count']}")

    t1 = sum(1 for r in records if r["task"] == "task-1")
    t2 = [r for r in records if r["task"] == "task-2"]
    print("-" * 72)
    print(f"提取 {len(records)} 条（Task1 {t1} / Task2 {len(t2)}）"
          f"，band 覆盖 {sorted({r['official_band'] for r in records})}")
    print(f"Task2 comment 平均词数 {sum(r['comment_word_count'] for r in t2) / max(len(t2),1):.0f}")

    if problems:
        print(f"\n[校验提示 {len(problems)} 条]")
        for p in problems:
            print(f"  · {p}")

    print(f"\n→ {out_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
