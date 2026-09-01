#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build.py — 把紧凑 DSL 稿编译成符合 reading-test.schema.json 的 JSON。

为什么要有这一层：
  直接手写 JSON 有大量语法噪音（转义、缩进、重复 key），手写 90 篇必然出错。
  DSL 只保留内容本身，结构、词数、evidence 偏移、题号、题组 range 全部由脚本推导。

用法:
  python3 build.py <dsl_file.txt> [--out <dir>]
  默认输出到 packs/reading-news-2026-08/data/reading/news/<id>.json

DSL 语法:
  ID: news-xxx-2026
  TITLE: The Cost of Buried Carbon
  TAGS: climate,energy
  URL: https://...
  NOTES: 事实溯源（写入 derivation）

  --- A
  段落 A 正文（可多行，段内换行会被合并成一个空格）
  --- B
  段落 B 正文

  ### GROUP <type> [subtype]
  INSTR: 题组指令（英文）
  ORDERED: true|false
  WORD_LIMIT: ONE WORD ONLY          # 仅填空/简答需要
  OPTIONS:                            # matching_headings / multiple_choice / matching_features
    i | 标题文本
    ii | 标题文本

  Q<num> <题干文本>
  ANS: <答案>
  FORM: <answer_form>                 # 可省略，脚本按 type 推导
  ACC: 变体1 ||| 变体2                # acceptable_answers，可省略
  EV: <段落标签> | <原文逐字子串>
  EV: <段落标签> | <第二条证据>
  PARA: 题干说法 >>> 原文说法 ||| kind # paraphrase 映射，可省略，可多条
  SKILL: inference,detail             # 可省略，默认 detail
  EXP: 解释文本
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---- 路径：全部基于 __file__ 推导，不含任何机器相关硬编码 --------------
# 本文件位于 <repo>/capability/scripts/pack_authoring/build.py
_HERE = Path(__file__).resolve().parent
CAPABILITY = _HERE.parents[1]                       # <repo>/capability
DEFAULT_PACK = CAPABILITY / "packs" / "reading-news-2026-08"
DEFAULT_OUT = DEFAULT_PACK / "data" / "reading" / "news"

TZ = timezone(timedelta(hours=8))
VERIFIED_ON = "2026-09-01"

DEFAULT_FORM = {
    "identifying_information": "boolean3",
    "identifying_writers_views": "boolean3",
    "matching_headings": "option_key",
    "matching_information": "option_key",
    "matching_features": "option_key",
    "multiple_choice": "option_key",
    "sentence_completion": "free_text",
    "summary_completion": "free_text",
    "short_answer": "free_text",
}

DEFAULT_INSTR = {
    "identifying_information": "Do the following statements agree with the information given in the reading passage? In boxes 1-5 on your answer sheet, write TRUE, FALSE or NOT GIVEN.",
    "identifying_writers_views": "Do the following statements agree with the claims of the writer? In boxes on your answer sheet, write YES, NO or NOT GIVEN.",
    "matching_headings": "The reading passage has several paragraphs. Choose the correct heading for each paragraph from the list of headings below.",
    "matching_information": "Look at the following statements. Match each statement with the paragraph in which the information can be found.",
    "matching_features": "Match each statement with the correct person, organisation or feature.",
    "multiple_choice": "Choose the correct letter, A, B, C or D.",
    "sentence_completion": "Complete each sentence with the correct ending / word from the passage.",
    "summary_completion": "Complete the summary using words from the passage.",
    "short_answer": "Answer the questions using words from the passage.",
}


class DSLError(Exception):
    pass


def parse(path: Path) -> dict:
    lines = path.read_text(encoding="utf-8").splitlines()

    head = {}
    paras = []          # [(label, text)]
    groups = []

    cur_para = None
    cur_group = None
    cur_q = None
    mode = "head"       # head | para

    for raw in lines:
        line = raw.rstrip()

        # --- 段落分隔
        m = re.match(r"^---\s+([A-Z])\s*$", line)
        if m:
            if cur_para:
                paras.append(cur_para)
            cur_para = [m.group(1), []]
            mode = "para"
            continue

        # ### 题组
        m = re.match(r"^###\s+GROUP\s+([\w]+)(?:\s+([\w]+))?\s*$", line)
        if m:
            if cur_para:
                paras.append(cur_para)
                cur_para = None
            if cur_group:
                groups.append(cur_group)
            mode = "group"
            cur_group = {
                "type": m.group(1),
                "subtype": m.group(2),
                "instruction": DEFAULT_INSTR.get(m.group(1), ""),
                "ordered": None,
                "word_limit": None,
                "options": [],
                "questions": [],
            }
            cur_q = None
            continue

        if mode == "para":
            if line.strip():
                cur_para[1].append(line.strip())
            continue

        # 题组内的 key: value
        m = re.match(r"^(ID|TITLE|TAGS|URL|NOTES|INSTR|ORDERED|WORD_LIMIT)\s*:\s*(.*)$", line)
        if m:
            k, v = m.group(1), m.group(2).strip()
            if mode == "head":
                head[k] = v
            else:
                if k == "INSTR":
                    cur_group["instruction"] = v
                elif k == "ORDERED":
                    cur_group["ordered"] = v.lower() == "true"
                elif k == "WORD_LIMIT":
                    cur_group["word_limit"] = v
            continue

        # 选项块
        if line.strip() == "OPTIONS:":
            continue
        m = re.match(r"^\s{2,}([A-Za-z0-9]+(?:\s\.\.\.)?|[ivxlc]+)\s\|\s(.+)$", line)
        if m and cur_group is not None and not line.startswith("Q"):
            cur_group["options"].append({"key": m.group(1).strip(), "text": m.group(2).strip()})
            continue

        # 题目起始
        m = re.match(r"^Q(\d+)\s+(.*)$", line)
        if m:
            cur_q = {
                "number": int(m.group(1)),
                "stem": m.group(2).strip(),
                "answer": None,
                "answer_form": None,
                "acceptable_answers": [],
                "evidence": [],
                "paraphrase": [],
                "explanation": "",
                "skill_tag": ["detail"],
            }
            cur_group["questions"].append(cur_q)
            continue

        if cur_q is None:
            if line.strip() and not line.startswith("#"):
                # 题干续行（多行 stem）
                pass
            continue

        m = re.match(r"^(ANS|FORM|ACC|EV|PARA|SKILL|EXP)\s*:\s*(.*)$", line)
        if m:
            k, v = m.group(1), m.group(2).strip()
            if k == "ANS":
                cur_q["answer"] = v
            elif k == "FORM":
                cur_q["answer_form"] = v
            elif k == "ACC":
                cur_q["acceptable_answers"] = [x.strip() for x in v.split("|||") if x.strip()]
            elif k == "EV":
                label, _, quote = v.partition("|")
                cur_q["evidence"].append({
                    "quote": quote.strip(),
                    "start": 0,
                    "end": 0,
                    "paragraph_label": label.strip(),
                    "is_core": True,
                })
            elif k == "PARA":
                left, _, right = v.partition(">>>")
                kind = "synonym"
                if "|||" in right:
                    right, _, kind = right.partition("|||")
                cur_q["paraphrase"].append({
                    "from": left.strip(), "to": right.strip(), "kind": kind.strip()
                })
            elif k == "SKILL":
                cur_q["skill_tag"] = [x.strip() for x in v.split(",") if x.strip()]
            elif k == "EXP":
                cur_q["explanation"] = v
            continue

        # 续行：追加到上一个字段（stem 或 EXP）
        if line.strip() and cur_q is not None:
            if cur_q["evidence"]:
                cur_q["evidence"][-1]["quote"] += " " + line.strip()
            elif cur_q["explanation"]:
                cur_q["explanation"] += " " + line.strip()
            else:
                cur_q["stem"] += " " + line.strip()

    if cur_para:
        paras.append(cur_para)
    if cur_group:
        groups.append(cur_group)

    if not head.get("ID"):
        raise DSLError("缺少 ID:")
    if not paras:
        raise DSLError("没有任何段落（需要 --- A 之类的分隔）")
    if not groups:
        raise DSLError("没有任何题组（需要 ### GROUP）")

    return {"head": head, "paras": paras, "groups": groups}


def build(parsed: dict) -> dict:
    head = parsed["head"]
    pid = head["ID"]
    now = datetime.now(TZ).replace(microsecond=0).isoformat()

    paragraphs = [{"label": lb, "text": " ".join(chunks)} for lb, chunks in parsed["paras"]]
    word_count = sum(len(p["text"].split()) for p in paragraphs)

    # 段落正文拼接（与 verify.py 的约定一致：\n 连接）
    full = "\n".join(p["text"] for p in paragraphs)
    labels = {p["label"] for p in paragraphs}

    # 题组
    qg = []
    all_nums = []
    for i, g in enumerate(parsed["groups"], 1):
        gtype = g["type"]
        questions = []
        for q in g["questions"]:
            if not q["answer"]:
                raise DSLError(f"第 {q['number']} 题缺少 ANS:")
            if not q["evidence"]:
                raise DSLError(f"第 {q['number']} 题缺少 EV:")
            form = q["answer_form"] or DEFAULT_FORM.get(gtype, "free_text")
            item = {
                "number": q["number"],
                "stem": q["stem"],
                "answer": q["answer"],
                "answer_form": form,
            }
            # 规范答案本身即「可接受答案」：始终并入 acceptable_answers（去重，答案置首）
            acc = list(q["acceptable_answers"])
            if q["answer"] not in acc:
                acc.insert(0, q["answer"])
            item["acceptable_answers"] = acc
            # 注意：word_limit 只在题组层，题目层加会被 schema 的 additionalProperties=false 拦下

            # evidence 偏移重算
            evs = []
            for ev in q["evidence"]:
                lb = ev["paragraph_label"]
                if lb not in labels:
                    raise DSLError(f"第 {q['number']} 题 evidence 段落标签 {lb!r} 不存在")
                quote = ev["quote"]
                # 先在全文里找，再在该段内找（避免跨段误命中）
                idx = full.find(quote)
                if idx < 0:
                    ptext = next(p["text"] for p in paragraphs if p["label"] == lb)
                    j = ptext.find(quote)
                    if j < 0:
                        raise DSLError(
                            f"第 {q['number']} 题 evidence 不是段落 {lb} 的逐字子串:\n  {quote[:120]}"
                        )
                    offset_base = 0
                    for p in paragraphs:
                        if p["label"] == lb:
                            break
                        offset_base += len(p["text"]) + 1
                    idx = offset_base + j
                evs.append({
                    "quote": quote,
                    "start": idx,
                    "end": idx + len(quote),
                    "paragraph_label": lb,
                    "is_core": True,
                })
            item["evidence"] = evs

            if q["paraphrase"]:
                item["paraphrase"] = q["paraphrase"]
            if q["explanation"]:
                item["explanation"] = q["explanation"]
            item["skill_tag"] = q["skill_tag"]
            questions.append(item)

        ordered = g["ordered"]
        if ordered is None:
            ordered = gtype not in ("matching_headings", "matching_information", "matching_features")

        grp = {
            "id": f"{pid}-g{i}",
            "type": gtype,
            "instruction": g["instruction"],
            "question_range": {
                "from": min(x["number"] for x in questions),
                "to": max(x["number"] for x in questions),
            },
            "ordered": ordered,
            "options": g["options"],
            "questions": questions,
        }
        if g["subtype"]:
            grp["subtype"] = g["subtype"]
        if g.get("word_limit"):
            grp["word_limit"] = g["word_limit"]
        all_nums += [x["number"] for x in questions]
        qg.append(grp)

    # 题号连续性
    if sorted(all_nums) != list(range(1, len(all_nums) + 1)):
        raise DSLError(f"题号不连续: {sorted(all_nums)}")

    doc = {
        "schema_version": "1.0.0",
        "id": pid,
        "module": "academic",
        "set_name": "reading-news-2026-08",
        "source": {
            "source_type": "synthetic",
            "source_id": pid,
            "source_url": head.get("URL", ""),
            "licence": {
                "name": "Original paraphrase, factual grounding from cited news source",
                "redistributable": True,
                "notes": "文章为基于公开新闻事实的原创改写，不含源文连续原文。URL 仅作事实溯源。",
            },
            "retrieved_at": now,
            "verified_on": VERIFIED_ON,
            "derivation": head.get("NOTES", "基于公开新闻事实由模型改写成雅思学术体并命题"),
        },
        "passage": {
            "id": f"{pid}-text",
            "title": head.get("TITLE", ""),
            "subtitle": None,
            "paragraphs": paragraphs,
            "word_count": word_count,
            "topic_tags": [x.strip() for x in head.get("TAGS", "").split(",") if x.strip()],
            "cefr": "B2-C1",
            "has_diagram": False,
        },
        "question_groups": qg,
        "meta": {
            "quality_status": "synthetic_passed",
            "build_version": "news-to-ielts@1.1",
            "created_at": now,
            "reviewed_by": "auto",
            "review_note": "模型命题初稿，结构/偏移由脚本构建；答案未经人工复核。",
            "not_official": True,
        },
    }
    return doc


def main():
    ap = argparse.ArgumentParser(description="把紧凑 DSL 稿编译成 reading-test JSON")
    ap.add_argument("dsl")
    ap.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help=f"输出目录（默认 {DEFAULT_OUT}）",
    )
    a = ap.parse_args()

    dsl = Path(a.dsl)
    try:
        doc = build(parse(dsl))
    except DSLError as e:
        print(f"✗ {dsl.name}: {e}", file=sys.stderr)
        sys.exit(1)

    out = Path(a.out) / f"{doc['id']}.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    wc = doc["passage"]["word_count"]
    nq = sum(len(g["questions"]) for g in doc["question_groups"])
    print(f"✓ {doc['id']}  {wc} 词 · {nq} 题 → {out.name}")


if __name__ == "__main__":
    main()
