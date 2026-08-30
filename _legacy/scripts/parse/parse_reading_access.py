#!/usr/bin/env python3
"""
parse_reading_access.py — 解析 ielts.org 无障碍大字版阅读样题

为什么先做这一套
----------------
无障碍版把 **text / question / answer 三件套拆成三个独立 PDF**，
省掉了从混合排版里切分正文与题目这一步——这是解析链路里最容易出错的地方。
先在这里把链路跑通，再去啃常规排版版（46 页 academic-2023）会省很多事。

一套真题含 3 篇 passage，但 reading-test.schema.json 顶层是单 passage，
所以按 passage 拆成 3 个文件，用 `set_name` 关联回同一套。

版面结构（实测，三种题组标题形式都要支持）
------------------------------------------
    QUESTIONS 1 – 6        → 范围（第一版只支持这种，漏了 15 题）
    QUESTIONS 19 and 20    → 两题并列
    QUESTION 40            → 单题

题目编号的两种形态
------------------
    行首式  : "1 The natural world is often the first place..."      （判断/选择/简答）
    填空式  : "find 23                        equally valuable."    （填空，编号嵌句中，
                                                                      后跟 >=2 空格）
第一版只处理了行首式，导致 q7-10、q23-26 这类填空题全部丢失。

答案 key 的四种形态
------------------
    1. FALSE                              → 单题单答案
    9. mineralisation / mineralization    → 同题多可接受答案
    19&20. IN EITHER ORDER: D, E          → 多题共享答案池且无序
    24&25. IN EITHER ORDER: - a  - b      → 同上，答案为多词短语

    "IN EITHER ORDER" 是 QC 的黄金信号：直接给出 ordered=false，
    不需要我们猜题号与原文顺序是否一致。

不足
----
官方 PDF 不含 evidence（答案在原文中的定位）与 paraphrase（同义替换）。
这两个字段是 QC 与学习者反馈的关键，需 LLM 后处理补，本脚本留 null
并在 meta.qc 里标记 evidence_missing。

用法
----
    ./.venv/bin/python scripts/parse/parse_reading_access.py
    ./.venv/bin/python scripts/parse/parse_reading_access.py --dry-run

输出
----
    data/structured/reading/official/*-p{1,2,3}.json
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

RAW = ROOT / "data" / "raw" / "reading" / "ielts-official" / "access"
OUT_DIR = ROOT / "data" / "structured" / "reading" / "official"

TEXT_PDF = RAW / "reading-text-booklet.pdf"
QUESTION_PDF = RAW / "reading-question-booklet.pdf"
ANSWER_PDF = RAW / "reading-answer-key.pdf"

SET_NAME = "ielts-academic-reading-sample-2023-modified-large-print"
SOURCE_URL = ("https://ielts.org/cdn/ielts-access-arrangements-sample-tests/"
              "ielts-modified-large-print/")

# --------------------------------------------------------------------------- 正则
RE_PASSAGE = re.compile(r"^\s*READING PASSAGE\s+(\d+)\s*$", re.I)
RE_PAGE_NUM = re.compile(r"^\s*(\d{1,2})\s*$")
# 正文里段落标签单独成行（A / B / C …），是段落边界而非内容
RE_PARA_LABEL = re.compile(r"^[A-H]$")

# 题组标题：范围 / and 并列 / 单题，三种都要支持
RE_QHEADER = re.compile(
    r"^\s*QUESTIONS?\s+(\d+)\s*(?:\s*[–\-—]\s*(\d+)\s*|\s+and\s+(\d+)\s*)?$", re.I)

RE_ANSWER_LINE = re.compile(
    r"^\s*(?P<nums>\d+(?:\s*&\s*\d+)*)\s*[.)]\s*(?P<body>.+?)\s*$")

# 填空式题号：数字后跟 >=2 空格（PDF 里填空位被撑开）
RE_BLANK = re.compile(r"(?<![\d.,])(\d{1,2})(\s{2,})")
# 行首式题号
RE_QNUM_HEAD = re.compile(r"^(\d{1,2})\s+(.*)$")
# 选项：A / A. / A) 开头
RE_OPTION = re.compile(r"^([A-H])\s*[.)]?\s+(.+)$")

RE_BOILER = re.compile(
    r"(READ THIS BOOKLET FIRST|INSTRUCTIONS TO CANDIDATES|INFORMATION FOR CANDIDATES|"
    r"Write your (?:two )?answers? on your answer sheet|DO NOT TURN OVER|"
    r"MODIFIED LARGE PRINT|INTERNATIONAL ENGLISH|TEXT BOOKLET|QUESTION BOOKLET|"
    r"ANSWER KEY|SAMPLE TEST|TIME:|Page \d+ of \d+|IELTS\.org)", re.I)
RE_INSTR_NOISE = re.compile(
    r"^(?:Answer questions? .* by referring to|Reading Passage \d+ has|"
    r"Read the following|Write your|For question)", re.I)

# 题型识别：特异性从高到低，命中即止。
# note/table/flow-chart 在官方术语里是 summary_completion 的变体，用 subtype 区分。
TYPE_RULES: list[tuple[str, str | None, re.Pattern]] = [
    ("identifying_writers_views", None,
     re.compile(r"Do the following statements agree with (?:the (?:views|claims)|the writer)", re.I)),
    ("identifying_information", None,
     re.compile(r"Do the following statements agree with the information", re.I)),
    ("matching_sentence_endings", None,
     re.compile(r"Complete each sentence with the correct ending", re.I)),
    ("multiple_choice", "multi_answer",
     re.compile(r"(?:Choose|choose)\s+(?:TWO|THREE|FOUR)\s+letters", re.I)),
    ("multiple_choice", "single_answer",
     re.compile(r"(?:choose the correct letter|Which of the following is the most suitable)", re.I)),
    ("matching_headings", None,
     re.compile(r"(?:Choose|Match).{0,40}heading", re.I)),
    ("matching_features", None,
     re.compile(r"Match (?:each statement|the people|each).{0,60}with", re.I)),
    ("matching_information", None,
     re.compile(r"(?:Which|In which) (?:paragraph|section)", re.I)),
    ("diagram_label_completion", None,
     re.compile(r"(?:Label|Complete) the diagram", re.I)),
    ("summary_completion", "note",
     re.compile(r"Complete the notes?\b", re.I)),
    ("summary_completion", "table",
     re.compile(r"Complete the table", re.I)),
    ("summary_completion", "flowchart",
     re.compile(r"flow[- ]?chart", re.I)),
    ("summary_completion", "summary",
     re.compile(r"Complete the summary", re.I)),
    ("sentence_completion", None,
     re.compile(r"Complete the sentences?\b", re.I)),
    ("short_answer", None,
     re.compile(r"Choose (?:ONE WORD|NO MORE THAN)", re.I)),
]

WORD_LIMIT_RULES = [
    (re.compile(r"NO MORE THAN (\w+) WORDS?", re.I), "NO MORE THAN {0} WORDS"),
    (re.compile(r"Choose ONE WORD ONLY", re.I), "ONE WORD ONLY"),
]


# --------------------------------------------------------------------------- 工具
def clean_lines(text: str, keep_blank: bool = False) -> list[str]:
    """去掉页眉页脚与考试指令套话。

    keep_blank=True 时保留空字符串作为**段落边界信号**——正文切段需要用，
    题目解析不需要（题目之间是连续行）。
    """
    out = []
    for ln in (text or "").split("\n"):
        s = ln.strip()
        if not s:
            if keep_blank:
                out.append("")
            continue
        if RE_PAGE_NUM.match(s) or RE_BOILER.search(s):
            continue
        out.append(s)
    return out


def dedupe_paragraphs(lines: list[str]) -> list[str]:
    """按空行切段。

    第一版用"行 >60 字符且以句号结尾"切段，结果 753 词的 passage 只切出 1 段——
    无障碍大字版每行都很短（实测 20-40 字符），条件几乎不触发。

    实测空行分布：**单空行 = 段落边界**（34 处），连续 >=3 空行 = 页面/章节分隔。
    所以：遇到第一个空行就收段，连续空行只在第一次生效。

    段落结构对雅思阅读是硬需求 —— Matching Headings 依赖段落标签 A/B/C，
    evidence 也要标 paragraph_label。切不出段落等于废掉一批题型。
    """
    paras: list[str] = []
    buf: list[str] = []
    in_blank = False
    for ln in lines:
        if not ln:
            if not in_blank and buf:
                paras.append(" ".join(buf))
                buf = []
            in_blank = True
            continue
        in_blank = False
        buf.append(ln)
    if buf:
        paras.append(" ".join(buf))
    paras = [p.strip() for p in paras if p.strip()]

    # 合并碎片段：PDF 分页处常残留 5-15 词的半截行
    # （passage 3 曾解析出末尾 8 词 + 7 词两段碎片）。
    merged: list[str] = []
    for p in paras:
        if merged and len(p.split()) < 15:
            merged[-1] = merged[-1] + " " + p
        else:
            merged.append(p)
    return merged


def detect_type(instruction: str) -> tuple[str, str | None]:
    for t, sub, pat in TYPE_RULES:
        if pat.search(instruction):
            return t, sub
    return "short_answer", None


def detect_word_limit(instruction: str) -> str | None:
    for pat, fmt in WORD_LIMIT_RULES:
        m = pat.search(instruction)
        if m:
            return fmt.format(m.group(1).upper()) if m.groups() else fmt
    return None


# --------------------------------------------------------------------------- 答案
def parse_answer_key(path: Path) -> dict[int, dict]:
    reader = PdfReader(str(path))
    raw = "\n".join((p.extract_text() or "") for p in reader.pages)

    answers: dict[int, dict] = {}
    for line in raw.split("\n"):
        s = line.strip()
        if not s or s.upper().startswith("READING PASSAGE"):
            continue
        m = RE_ANSWER_LINE.match(s)
        if not m:
            continue
        nums = [int(n) for n in re.findall(r"\d+", m.group("nums"))]
        body = m.group("body").strip()

        unordered = bool(re.search(r"IN EITHER ORDER", body, re.I))
        body = re.sub(r"^IN EITHER ORDER\s*:?\s*", "", body, flags=re.I).strip()

        items = [x.strip(" .-–") for x in re.split(r"\s+[-–]\s+|,\s*", body)
                 if x.strip(" .-–")]
        acceptable: list[str] = []
        final: list[str] = []
        for it in items:
            if "/" in it:
                variants = [v.strip() for v in it.split("/") if v.strip()]
                acceptable.extend(variants)
                final.append(variants[0])
            else:
                final.append(it)

        for n in nums:
            answers[n] = {
                "answer": final[0] if len(final) == 1 else final,
                "acceptable_answers": acceptable or None,
                "ordered": not unordered,
            }
    return answers


# --------------------------------------------------------------------------- 题目
def _qheader_nums(line: str) -> list[int] | None:
    m = RE_QHEADER.match(line)
    if not m:
        return None
    a = int(m.group(1))
    b = m.group(2) or m.group(3)
    if b:
        return list(range(a, int(b) + 1))
    return [a]


def _shared_stem(instruction: list[str]) -> str:
    """选择题/配对题的共享题干：取 instruction 里最后一个问句。

    这类题组（如 "Which TWO of the following statements ... ?" + 选项 A-E）
    没有逐题编号行，编号只在题组标题里出现，所有题共用同一个题干。
    """
    joined = " ".join(instruction)
    sents = re.split(r"(?<=[?])\s+", joined)
    for s in reversed(sents):
        s = s.strip()
        if s.endswith("?") and len(s) > 15:
            return s
    return joined.strip()[-200:]


def _fill_blanks(g: dict) -> None:
    """补全填空式题号（编号嵌在句中、后跟 >=2 空格的形态）。

    行首式匹配不到的题号在这里补。用"数字 + 2 个以上空格"定位空位，
    取前后各 60 字符作为 stem，并在空位处标 `__[n]__` 便于人工/QC 定位。
    """
    found = {q["number"] for q in g["questions"]}
    missing = [n for n in g["expected"] if n not in found]
    if not missing:
        return

    # 注意顺序：必须**先**在保留原始空白的文本上定位空位，**再**压缩空白生成 stem。
    # 曾经反过来做（先 re.sub(r"\s+", " ")），结果 \s{2,} 永远匹配不到，
    # q7-10 / q23-26 这类填空题全部丢失。
    raw = "\n".join(g["body"])
    for n in missing:
        m = re.search(rf"(?<![\d.,]){n}(\s{{2,}})", raw)
        if not m:
            continue
        left = re.sub(r"\s+", " ", raw[max(0, m.start() - 80):m.start()])
        right = re.sub(r"\s+", " ", raw[m.end():min(len(raw), m.end() + 80)])
        frag = (left + f"__[{n}]__" + right).strip()
        g["questions"].append({"number": n, "stem": frag, "_cont": False})
    g["questions"].sort(key=lambda q: q["number"])

    # 仍缺失的编号（选择题/配对题：编号不在正文里，题干由 instruction 给出）
    still = [n for n in missing if n not in {q["number"] for q in g["questions"]}]
    if still and g["options"]:
        shared = _shared_stem(g["instruction"])
        for n in still:
            g["questions"].append({"number": n, "stem": shared, "_cont": False})
        g["questions"].sort(key=lambda q: q["number"])


def parse_question_booklet(path: Path) -> dict[int, list[dict]]:
    reader = PdfReader(str(path))
    lines: list[str] = []
    for pg in reader.pages:
        lines.extend(clean_lines(pg.extract_text() or ""))

    by_passage: dict[int, list[dict]] = {}
    cur_passage: int | None = None
    cur: dict | None = None

    def flush():
        nonlocal cur
        if cur:
            # 必须先补填空式题号再判断有无题目。填空题（q7-10、q23-26）的编号
            # 嵌在句中（"find 23        equally valuable"），行首式匹配不到；
            # 若在这里就因 questions 为空而丢弃，整个题组会丢失。
            _fill_blanks(cur)
            if not cur["questions"]:
                cur = None
                return
            instr = " ".join(cur["instruction"]).strip()
            gtype, subtype = detect_type(instr)
            nums = [q["number"] for q in cur["questions"]]
            cur["type"], cur["subtype"] = gtype, subtype
            cur["instruction_text"] = instr
            cur["range"] = (min(nums), max(nums))
            by_passage.setdefault(cur_passage, []).append(cur)
        cur = None

    for ln in lines:
        m_p = RE_PASSAGE.match(ln)
        if m_p:
            flush()
            cur_passage = int(m_p.group(1))
            continue

        nums = _qheader_nums(ln)
        if nums:
            flush()
            cur = {"expected": nums, "instruction": [], "questions": [],
                   "options": [], "body": []}
            continue

        if cur is None:
            continue

        # 选项行：A / A. 开头，且字母在题组范围内（避免把正文当选项）
        m_opt = RE_OPTION.match(ln)
        if m_opt and cur["expected"] and len(m_opt.group(2)) > 2:
            cur["options"].append({"key": m_opt.group(1).upper(),
                                   "text": m_opt.group(2).strip()})
            continue

        # 行首式题号
        m_num = RE_QNUM_HEAD.match(ln)
        if m_num and int(m_num.group(1)) in cur["expected"]:
            cur["questions"].append({"number": int(m_num.group(1)),
                                     "stem": m_num.group(2).strip(),
                                     "_cont": True})
            continue

        # 续行：接到上一个 question 的 stem
        if cur["questions"] and cur["questions"][-1].get("_cont"):
            cur["questions"][-1]["stem"] += " " + ln
            continue

        # 其余算 instruction（过滤掉指引用套话）
        if not RE_INSTR_NOISE.match(ln):
            cur["instruction"].append(ln)
        cur["body"].append(ln)

    flush()
    return by_passage


# --------------------------------------------------------------------------- 正文
def parse_text_booklet(path: Path) -> dict[int, dict]:
    reader = PdfReader(str(path))
    lines: list[str] = []
    for pg in reader.pages:
        for ln in clean_lines(pg.extract_text() or "", keep_blank=True):
            # 段落标签单独成行（A/B/C…）→ 转成段落边界。
            # 否则它会被当成正文内容，导致段落数虚高
            # （passage 3 官方是 8 段 A-H，误算成 10 段）。
            lines.append("" if RE_PARA_LABEL.match(ln) else ln)

    passages: dict[int, dict] = {}
    cur: dict | None = None
    buf: list[str] = []
    stage = "none"

    def flush_body():
        if cur is not None and buf:
            cur["_lines"].extend(buf)
        buf.clear()

    for ln in lines:
        m_p = RE_PASSAGE.match(ln)
        if m_p:
            flush_body()
            if cur:
                passages[cur["_no"]] = cur
            cur = {"_no": int(m_p.group(1)), "title": None, "subtitle": None,
                   "_lines": []}
            stage = "title"
            continue
        if cur is None:
            continue
        if RE_QHEADER.match(ln):
            continue
        # keep_blank 之后，题组标题与正文标题之间会夹空行；
        # 若不跳过，空行会被当成 title，结果所有 passage 都叫 "Reading Passage N"。
        if stage in ("title", "subtitle") and not ln:
            continue
        if stage == "title" and not cur["title"]:
            cur["title"] = ln; stage = "subtitle"; continue
        if stage == "subtitle" and not cur["subtitle"]:
            # 单字母不是副标题——passage 3 的段落标签 "A" 曾被误当副标题
            if RE_PARA_LABEL.match(ln) or len(ln) < 3:
                stage = "body"
                continue
            cur["subtitle"] = ln; stage = "body"; continue
        buf.append(ln)

    flush_body()
    if cur:
        passages[cur["_no"]] = cur

    out = {}
    for no, p in passages.items():
        paras = dedupe_paragraphs(p["_lines"])
        text = " ".join(paras)
        out[no] = {
            "title": p["title"] or f"Reading Passage {no}",
            "subtitle": p["subtitle"],
            "paragraphs": paras,
            "word_count": len(re.findall(r"[A-Za-z][A-Za-z'\-]*", text)),
        }
    return out


# --------------------------------------------------------------------------- 组装
def build(passage_no: int, passage: dict, groups: list[dict],
          answers: dict[int, dict]) -> dict:
    total_q = 0
    matched = 0
    for g in groups:
        flags = [answers.get(q["number"], {}).get("ordered", True)
                 for q in g["questions"]]
        qs = []
        for q in g["questions"]:
            a = answers.get(q["number"], {})
            ans = a.get("answer")
            if ans not in (None, ""):
                matched += 1
            # 空字段**不输出**而不是输出 null：schema 里这些是 array/object，
            # 显式 null 会直接校验失败。留待 LLM 后处理阶段再补。
            item = {
                "number": q["number"],
                "stem": q["stem"],
                "answer": ans if ans is not None else "",
            }
            if a.get("acceptable_answers"):
                item["acceptable_answers"] = a["acceptable_answers"]
            qs.append(item)
        total_q += len(qs)
        g["questions"] = qs
        g["ordered"] = all(flags)
        # 空值一律不输出：schema 里是 string / enum，显式 null 会校验失败
        wl = detect_word_limit(g["instruction_text"])
        if wl:
            g["word_limit"] = wl
        if g.get("subtype"):
            pass
        else:
            g.pop("subtype", None)
        g["id"] = f"p{passage_no}-q{g['range'][0]}-{g['range'][1]}"
        g["instruction"] = g["instruction_text"]
        g["question_range"] = {"from": g["range"][0], "to": g["range"][1]}

    body = " ".join(passage["paragraphs"])
    now = datetime.now(timezone(timedelta(hours=8))).isoformat()

    for g in groups:
        for k in ("expected", "instruction_text", "range", "body"):
            g.pop(k, None)

    # paragraphs 必须是 {label, text} 对象数组——Matching Headings 依赖段落标签
    # A/B/C。第一版传的是字符串数组，直接校验失败。
    paragraphs = [
        {"label": chr(ord("A") + i), "text": t}
        for i, t in enumerate(passage["paragraphs"])
    ]

    return {
        "schema_version": "1.0.0",      # schema 里是 const '1.0.0'，不是自定义版本号
        "id": f"{SET_NAME}-p{passage_no}",
        "module": "academic",          # 考试类别，不是科目（科目由目录承载）
        "set_name": SET_NAME,
        "source": {
            "source_type": "official_sample",
            "source_id": "ielts-academic-reading-modified-large-print",
            "source_url": SOURCE_URL,
            "licence": {
                "name": "IELTS Partners",
                "redistributable": False,
                "notes": "官方免费公开样题。题目结构、答案与位置属事实性信息可保留；"
                         "原文全文不随数据集外发，仅存 data/raw/ 供本地解析。",
            },
            "retrieved_at": now,
            "verified_on": "2026-08-29",
            "derivation": "从 text/question/answer 三件套 PDF 自动解析",
        },
        "passage": {
            "id": f"{SET_NAME}-p{passage_no}-text",
            "title": passage["title"],
            "subtitle": passage["subtitle"],
            "paragraphs": paragraphs,
            "word_count": passage["word_count"],
            "has_diagram": False,
        },
        "question_groups": groups,
        "meta": {
            "quality_status": "parsed_unverified",
            "qc": {
                "evidence_missing": True,
                "paraphrase_missing": True,
                "question_count": total_q,
                "answers_matched": matched,
            },
            "build_version": "parse_reading_access.py@1.2",
            "content_hash": hashlib.sha256(body.encode("utf-8")).hexdigest()[:16],
            "created_at": now,
            # 合规硬约束：虽然素材来自官方免费样题，但这份**结构化解析产物**
            # 不是官方发布物，展示面必须标注。schema 里是 const: true。
            "not_official": True,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    for p in (TEXT_PDF, QUESTION_PDF, ANSWER_PDF):
        if not p.exists():
            sys.exit(f"缺少 {p} —— 先跑 scripts/fetch/fetch_official.py")

    answers = parse_answer_key(ANSWER_PDF)
    groups_by_p = parse_question_booklet(QUESTION_PDF)
    passages = parse_text_booklet(TEXT_PDF)

    print(f"答案 {len(answers)} 条 | 题组 {sum(len(v) for v in groups_by_p.values())} 个 "
          f"| 正文 {len(passages)} 篇")
    for no in sorted(passages):
        gs = groups_by_p.get(no, [])
        nq = sum(len(g["questions"]) for g in gs)
        print(f"\n  passage {no}: {passages[no]['word_count']} 词  "
              f"{len(gs)} 题组 / {nq} 题  | {passages[no]['title'][:44]}")
        for g in gs:
            qn = [q["number"] for q in g["questions"]]
            opt = f"  选项{len(g['options'])}" if g["options"] else ""
            # ordered 由 build() 计算；这里直接查 answer key，避免时序依赖
            ordered = all(answers.get(n, {}).get("ordered", True) for n in qn)
            print(f"      {qn[0]:>2}-{qn[-1]:<2} {g['type']}"
                  f"{'/'+g['subtype'] if g.get('subtype') else ''}{opt}"
                  f"   ordered={ordered}")

    if args.dry_run:
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print()
    for no in sorted(passages):
        doc = build(no, passages[no], groups_by_p.get(no, []), answers)
        out = OUT_DIR / f"{SET_NAME}-p{no}.json"
        out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        missing = [q["number"] for g in doc["question_groups"] for q in g["questions"]
                   if q["answer"] == ""]
        nq = sum(len(g["questions"]) for g in doc["question_groups"])
        print(f"  [{'OK' if not missing else '缺答案 ' + str(missing)}] "
              f"{out.name}: {len(doc['question_groups'])} 题组 / {nq} 题")
    print(f"\n→ {OUT_DIR.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
