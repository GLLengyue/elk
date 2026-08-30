#!/usr/bin/env python3
"""
parse_reading_taskbank.py — 解析 ielts.org 分题型阅读样题（46 页 task bank）

与无障碍版解析器的分工
----------------------
    无障碍版 (parse_reading_access.py) : 一套**完整真题**（3 篇 × 13-14 题 = 40 题）
                                         题型 7 种，文本长、结构完整
    本脚本 (task bank)                : 每种题型一个**独立样本**（短文 + 3-6 题）
                                         覆盖官方全部 11 种题型，用于补齐题型覆盖

两者不是替代关系：真题提供"完整考试体验"，task bank 提供"题型格式样本"。
合成题生成器需要的是后者——它得知道每种题型长什么样。

版面结构（实测）
----------------
    题目页  : "Academic Reading Sample Task – <题型>"
              [Note: ...] / 版权行 / Questions 1 – 4 / instruction / 选项池 / 题目
    答案页  : "Academic Reading Sample Task – <题型> (Answers)"
              "1 iii How a concept from one field of study was applied in another"

答案格式与无障碍版**不同**：这里是 `题号 答案 答案文本`，无障碍版是 `题号. 答案`。

输出粒度
--------
每个题型样本是一段短文（非完整 passage），因此按 task-bank 的原生粒度
存为一个 reading-test 文档，`set_name` 标为 task bank。
题量与官方一致（每样本 3-6 题），不要指望它凑出 40 题一套。

用法
----
    ./.venv/bin/python scripts/parse/parse_reading_taskbank.py
    ./.venv/bin/python scripts/parse/parse_reading_taskbank.py --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "parse"))

from parse_reading_access import (              # noqa: E402
    TYPE_RULES, detect_word_limit, clean_lines, dedupe_paragraphs,
)

RAW_DIR = ROOT / "data" / "raw" / "reading" / "ielts-official"
OUT_DIR = ROOT / "data" / "structured" / "reading" / "official" / "taskbank"

# 两个来源的版面结构完全一致（封面 / Contents / "XXX Reading Sample Task – 题型"），
# 所以复用同一套解析逻辑，只换文件、id 前缀与 module。
# 培训类版还含学术版缺失的 Matching Information 题型。
SOURCES = [
    {
        "pdf": RAW_DIR / "academic-reading-sample-tasks-2023.pdf",
        "id_prefix": "ar-tb-2023-",
        "module": "academic",
        "set_name": "ielts-academic-reading-sample-tasks-2023-taskbank",
        "url": "https://ielts.org/cdn/Sample-tests/ielts-academic-reading-sample-tasks-2023.pdf",
        "source_id": "ielts-academic-reading-sample-tasks-2023.pdf",
    },
    {
        "pdf": RAW_DIR / "general-reading-sample-tasks-2023.pdf",
        "id_prefix": "gt-tb-2023-",
        "module": "general_training",
        "set_name": "ielts-general-training-reading-sample-tasks-2023-taskbank",
        "url": "https://ielts.org/cdn/Sample-tests/ielts-general-reading-sample-tasks-2023.pdf",
        "source_id": "ielts-general-reading-sample-tasks-2023.pdf",
    },
]
PDF = SOURCES[0]["pdf"]
SET_NAME = SOURCES[0]["set_name"]
SOURCE_URL = SOURCES[0]["url"]

RE_TASK_PREFIX = re.compile(
    r"^\s*(?:Academic|General Training)\s+Reading Sample Task\s*[–\-]\s*", re.I)
RE_MODULE_HINT = re.compile(r"^\s*(Academic|General Training)\s+Reading", re.I)


def extract_head(text: str) -> tuple[str, bool] | None:
    """从页面开头提取章节标题 → (题型名, 是否答案页)。

    两个实测坑：
    1. **标题跨行**。PDF 里是
           Academic Reading Sample Task – Flow-chart Completion: selecting words from the
           text
       只取单行会得到 "Flow-chart"（实测），必须按行块收集。
    2. **"(Answers)" 常独占一行**，所以不能要求它与题型名同行。
       更不能用单个正则 `(?P<name>.+?)\s*(?P<is_answer>\(Answers?\))?\s*$`——
       非贪婪 name 配可选组会直接把 "(Answers)" 吞掉。
    """
    lines = [ln.strip() for ln in (text or "").split("\n")]
    idx = None
    for i, ln in enumerate(lines[:6]):
        if RE_TASK_PREFIX.match(ln):
            idx = i
            break
    if idx is None:
        return None

    parts: list[str] = []
    for ln in lines[idx:idx + 4]:
        if not ln:
            if parts:
                break
            continue
        if RE_COPYRIGHT.search(ln) or re.match(r"^Questions?\s+\d", ln, re.I):
            break
        # 全大写行 = 文章标题，不是题型名的一部分。
        # GT 的 Flow-chart 页是
        #     "General Training Reading Sample Task – Flow-chart"
        #     "Completion"
        #     "ROBOTS AT WORK"          <- 文章标题
        # 收进去会让 section 名变成 "Flow-chart Completion ROBOTS AT WORK"，
        # 与答案页的 "Flow-chart Completion" 匹配不上 → 该 section 找不到答案。
        if parts and re.fullmatch(r"[A-Z][A-Z\s\-'/\d]{3,}", ln):
            break
        parts.append(ln)
        # 含 "(Answers)" 的行是标题末行，其后紧跟答案正文。
        # GT 的 Flow-chart 答案页是
        #     "General Training Reading Sample Task – Flow-chart"
        #     "Completion (Answers)"
        #     "33 transmitted (electronically)"      <- 答案正文
        # 不在这里停，name 会变成
        # "Flow-chart Completion 33 transmitted (electronically) ..."，
        # 与题目页的 "Flow-chart Completion" 对不上，
        # 题目页与答案页被拆成两个 section、各自缺一半 → "未找到答案"被跳过。
        if re.search(r"\(answers?\)", ln, re.I):
            break
        if len(" ".join(parts)) > 110:
            break

    joined = " ".join(parts)
    rest = RE_TASK_PREFIX.sub("", joined).strip()
    is_ans = bool(re.search(r"\(answers?\)", rest, re.I))
    name = re.sub(r"\(answers?\)", "", rest, flags=re.I).strip()
    name = re.sub(r"\.{2,}.*$", "", name).strip()      # 目录页的点号+页码
    # 归一空白：PDF 提取时同一题型名的空格数可能不同
    # （"Summary Completion: productive" vs "Summary Completion:  productive"），
    # 不归一会让题目页与答案页被判成两个不同 section。
    name = re.sub(r"\s+", " ", name).strip()
    return name, is_ans
RE_COPYRIGHT = re.compile(
    r"(©|All rights reserved|Distributed by|^\[Note:|as first published)", re.I)
# 答案行两种写法：
#   "33 transmitted (electronically)"   学术版，题号后直接跟空白
#   "1. experience"                      培训类版，题号后带点
# 第一版只认前者，GT 的 Note / Summary / True-False 三个 section 全部解析失败。
RE_ANSWER_ROW = re.compile(r"^\s*(\d{1,2})\s*[.)]?\s+(.+?)\s*$")
RE_QNUM_HEAD = re.compile(r"^(\d{1,2})\s+(.*)$")
RE_OPTION_I = re.compile(r"^(i{1,3}v?|iv|vi{0,3}|ix|x)\s+(.+)$", re.I)   # 罗马数字选项
RE_OPTION_A = re.compile(r"^([A-H])[\s.)]\s*(.+)$")
RE_PAGE_MARK = re.compile(r"^Page \d+ of \d+")


def _slug(name: str, limit: int = 40) -> str:
    """生成 id 片段。

    schema 对 id 的约束是 ^[a-z0-9][a-z0-9-]{2,63}$，含前缀总长不能超 64。
    题型名如 "Summary Completion: selecting from a list of words or phrases"
    转成 slug 有 59 字符，加前缀必超限，所以截断到 limit。
    """
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (s or "unknown")[:limit].rstrip("-")


def _detect(name: str, instruction: str) -> tuple[str, str | None]:
    """先按章节名定类型（更可靠），失败再回退到 instruction 关键词。"""
    low = name.lower()
    table = [
        ("matching_headings", None, "matching headings"),
        ("matching_sentence_endings", None, "matching sentence endings"),
        ("matching_features", None, "matching features"),
        ("matching_information", None, "matching information"),
        ("sentence_completion", None, "sentence completion"),
        ("summary_completion", "note", "note completion"),
        ("summary_completion", "table", "table completion"),
        ("summary_completion", "flowchart", "flow-chart"),
        ("summary_completion", "summary", "summary completion"),
        ("identifying_information", None, "identifying information"),
        ("identifying_writers_views", None, "identifying writer"),
        ("diagram_label_completion", None, "diagram label"),
        ("short_answer", None, "short-answer"),
        ("multiple_choice", "multi_answer", "more than one answer"),
        ("multiple_choice", "single_answer", "one answer"),
    ]
    for t, sub, key in table:
        if key in low:
            return t, sub
    for t, sub, pat in TYPE_RULES:
        if pat.search(instruction):
            return t, sub
    return "short_answer", None


RE_GROUP_HEAD = re.compile(r"^\s*(\d+(?:\s*&\s*\d+)+)\s*(IN EITHER ORDER)?\s*$", re.I)
RE_OPT_ROW = re.compile(r"^\s*([A-H])\b")
# 答案行两种写法：
#   "33 transmitted (electronically)"   学术版，题号后直接跟空白
#   "1. experience"                      培训类版，题号后带点
# 第一版只认前者，GT 的 Note / Summary / True-False 三个 section 全部解析失败。
RE_ANSWER_ROW = re.compile(r"^\s*(\d{1,2})\s*[.)]?\s+(.+?)\s*$")


def parse_answers(lines: list[str]) -> dict[int, object]:
    """答案页解析，支持两种版面：

    逐题式  : "1 iii How a concept from one field ..."   → {1: "iii"}
    分组式  : "1&2 IN EITHER ORDER" + 后续 " B ■ ..." " G ■ ..."
                                                          → {1: ["B","G"], 2: ["B","G"]}

    第二版才补的分组式：Multiple Choice: more than one answer 用的就是它，
    漏了会导致整个 section 因"未找到答案"被跳过。
    """
    out: dict[int, object] = {}
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = RE_GROUP_HEAD.match(ln)
        if m:
            nums = [int(x) for x in re.findall(r"\d+", m.group(1))]
            vals: list[str] = []
            j = i + 1
            while j < len(lines):
                m2 = RE_OPT_ROW.match(lines[j])
                if not m2:
                    break
                vals.append(m2.group(1))
                j += 1
            if vals:
                for n in nums:
                    out[n] = vals
                i = j
                continue

        m3 = RE_ANSWER_ROW.match(ln)
        if m3:
            body = m3.group(2).strip()
            tok = body.split()[0] if body.split() else ""
            out[int(m3.group(1))] = tok.rstrip(".")
        i += 1
    return out


def process_source(src: dict, dry_run: bool = False) -> tuple[list, list]:
    """处理一个来源 PDF，返回 (已写入样本列表, 跳过说明列表)。"""
    if not src["pdf"].exists():
        return [], [f"{src['source_id']}: 文件缺失，先跑 scripts/fetch/fetch_official.py"]

    reader = PdfReader(str(src["pdf"]))
    pages = [p.extract_text() or "" for p in reader.pages]

    # 按 "Academic Reading Sample Task – XXX" 切分；带 (Answers) 的是答案页
    sections: list[dict] = []
    cur: dict | None = None
    for pno, text in enumerate(pages, 1):
        if pno == 2:            # p2 是 Contents，里面的标题行带页码，不能当 section
            continue
        head = extract_head(text)
        if not head:
            # 续页（无标题）：归属取决于当前 section 是否已进入答案区。
            # 题目页常跨多页（p3-4、p15-17），答案页通常单页；
            # 漏掉这段会让每个 section 只剩第一页，短文被截断。
            if cur is not None:
                if cur["a_pages"]:
                    cur["a_pages"].append(pno)
                else:
                    cur["q_pages"].append(pno)
            continue

        name, is_ans = head
        if cur and cur["name"] == name:
            # 同一题型的后续页：按 is_ans 决定归到题目页还是答案页。
            # 必须按**页**记录归属，不能只记 section 级标志——
            # 否则合并后 answers=True 会让所有页的内容都被当成答案行。
            (cur["a_pages"] if is_ans else cur["q_pages"]).append(pno)
            if is_ans:
                cur["answers"] = True
            continue

        cur = {"name": name, "answers": is_ans,
               "q_pages": [] if is_ans else [pno],
               "a_pages": [pno] if is_ans else [],
               "q_lines": [], "a_lines": []}
        sections.append(cur)

    # 收集每个 section 的正文行（按页归属分别收集）
    for sec in sections:
        for pno, bucket in [(p, "q_lines") for p in sec["q_pages"]] + \
                           [(p, "a_lines") for p in sec["a_pages"]]:
            for ln in (pages[pno - 1] or "").split("\n"):
                s = ln.strip()
                if not s or RE_PAGE_MARK.match(s) or RE_COPYRIGHT.search(s):
                    continue
                if RE_TASK_PREFIX.match(s) or extract_head(pages[pno - 1]):
                    if RE_TASK_PREFIX.match(s):
                        continue
                sec[bucket].append(s)

    if dry_run:
        for s in sections:
            print(f"  {s['name'][:56]:<56} 题目页{s['q_pages']} 答案页{s['a_pages']}")
        return [], []

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written, skipped = [], []

    # 题目 section 与紧随其后的答案 section 配对
    for i, sec in enumerate(sections):
        # 题目页为空的说明是孤立的答案页，跳过；
        # 不能用 sec["answers"] 过滤——合并后它恒为 True，会把全部 section 滤掉。
        if not sec["q_pages"]:
            continue
        answers = parse_answers(sec["a_lines"])
        if not answers:
            skipped.append(f"{sec['name']}: 未找到答案")
            continue

        nums = sorted(answers)
        gtype, subtype = _detect(sec["name"], " ".join(sec["q_lines"]))

        # 选项池：罗马数字（headings）或字母
        options = []
        for ln in sec["q_lines"]:
            m = RE_OPTION_I.match(ln) or RE_OPTION_A.match(ln)
            if m and len(m.group(2)) > 3:
                options.append({"key": m.group(1), "text": m.group(2).strip()})

        # 题干：优先取 "N Section X" 这类行，否则退回共享 stem
        stems: dict[int, str] = {}
        for ln in sec["q_lines"]:
            m = RE_QNUM_HEAD.match(ln)
            if m and int(m.group(1)) in nums:
                stems[int(m.group(1))] = m.group(2).strip()
        shared = ""
        for ln in reversed(sec["q_lines"]):
            if ln.endswith("?") and len(ln) > 15:
                shared = ln
                break

        questions = [{
            "number": n,
            "stem": stems.get(n) or shared or f"Question {n}",
            "answer": answers[n],
        } for n in nums]

        # 短文：去掉 instruction / 选项 / 题干行后的剩余内容
        body_lines = [ln for ln in sec["q_lines"]
                      if not RE_OPTION_I.match(ln) and not RE_OPTION_A.match(ln)
                      and not RE_QNUM_HEAD.match(ln)
                      and not re.match(r"^(Questions?\s+\d|Reading Passage|List of Headings|"
                                       r"Write the correct|Choose the correct)", ln, re.I)]
        paras = dedupe_paragraphs(body_lines) or [" ".join(body_lines)]

        now = datetime.now(timezone(timedelta(hours=8))).isoformat()
        slug = _slug(sec["name"])
        wc_tmp = len(re.findall(r"[A-Za-z][A-Za-z\'\-]*", " ".join(paras)))
        # 正文过短 = 文本是图片。GT 的 Matching Information 就是如此：
        # 题目在 p10，广告 A-E 在 p11 但整页只有 25 字符 + 2 张图片。
        # 这类样本不能当正常题用（evidence 无处可定位），必须显式标记。
        text_missing = wc_tmp < 100
        doc = {
            "schema_version": "1.0.0",
            "id": f"{src['id_prefix']}{slug}",
            "module": src["module"],
            "set_name": src["set_name"],
            "source": {
                "source_type": "official_sample",
                "source_id": src["source_id"],
                "source_url": src["url"],
                "licence": {
                    "name": "IELTS Partners",
                    "redistributable": False,
                    "notes": "官方免费样题。task bank 中的短文可能含第三方版权声明"
                             "（如 © The Atlantic），整段原文不得再分发。",
                },
                "retrieved_at": now,
                "verified_on": "2026-08-29",
                "page_refs": sorted(sec["q_pages"] + sec["a_pages"]),
                "derivation": "从分题型 task bank PDF 自动解析",
            },
            "passage": {
                "id": f"{src['id_prefix']}{slug}-text",
                "title": sec["name"],
                "paragraphs": [{"label": chr(ord("A") + i), "text": t}
                               for i, t in enumerate(paras)],
                "word_count": len(re.findall(r"[A-Za-z][A-Za-z'\-]*", " ".join(paras))),
                "has_diagram": "diagram" in sec["name"].lower(),
            },
            "question_groups": [{
                "id": f"{slug}-q{nums[0]}-{nums[-1]}",
                "type": gtype,
                **({"subtype": subtype} if subtype else {}),
                "instruction": " ".join(sec["q_lines"])[:600],
                **({"word_limit": detect_word_limit(" ".join(sec["q_lines"]))}
                   if detect_word_limit(" ".join(sec["q_lines"])) else {}),
                "question_range": {"from": nums[0], "to": nums[-1]},
                "ordered": True,
                **({"options": options} if options else {}),
                "questions": questions,
            }],
            "meta": {
                "quality_status": "parsed_unverified",
                "qc": {"evidence_missing": True, "paraphrase_missing": True,
                       "question_count": len(questions),
                       "answers_matched": sum(1 for q in questions if q["answer"]),
                       **({"passage_text_missing": True,
                          "note": "正文为图片，文本无法提取；不可用于 evidence 定位"}
                         if text_missing else {})},
                "build_version": "parse_reading_taskbank.py@1.0",
                "content_hash": hashlib.sha256(
                    " ".join(paras).encode("utf-8")).hexdigest()[:16],
                "created_at": now,
                "not_official": True,
            },
        }
        if text_missing:
            # 正文是图片 → 无法定位 evidence、无法练习，不产出文件。
            # 否则会卡在 schema 的 word_count 下限（GT Matching Information 仅 21 词）。
            skipped.append(f"{sec['name']}: 正文为图片（{wc_tmp} 词），不可用")
            continue
        out = OUT_DIR / f"{src['id_prefix']}{slug}.json"
        out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append((sec["name"], gtype, subtype, len(questions), len(options),
                        wc_tmp))
        if text_missing:
            print(f"  [!] {sec['name']}: 正文仅 {wc_tmp} 词，疑为图片，已标记 "
                  f"passage_text_missing")

    return written, skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_written, all_skipped = [], []
    for src in SOURCES:
        print(f"\n=== {src['source_id']} ===")
        w, s = process_source(src, args.dry_run)
        all_written.extend([(src["module"], *x) for x in w])
        all_skipped.extend(s)

    if args.dry_run:
        return 0

    print()
    print(f"{'题型样本':<44}{'类别':<12}{'type':<28}{'题':<4}{'选项':<6}{'词'}")
    print("-" * 104)
    for module, name, gt, sub, nq, nopt, wc in all_written:
        full = gt + ("/" + sub if sub else "")
        print(f"{name[:42]:<44}{module[:10]:<12}{full:<28}{nq:<4}{nopt:<6}{wc}")
    print("-" * 104)
    print(f"产出 {len(all_written)} 个题型样本 / {sum(w[4] for w in all_written)} 题")
    types = sorted({(w[0], w[2] + ("/" + w[3] if w[3] else "")) for w in all_written})
    print(f"覆盖 {len(types)} 种（含 module 区分）:")
    for m, t in types:
        print(f"  {m:<18}{t}")
    for s in all_skipped:
        print(f"  [跳过] {s}")
    print(f"\n→ {OUT_DIR.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
