#!/usr/bin/env python3
"""
elk_core.py — 自包含零依赖核心（纯 Python 标准库）

设计目标
--------
本文件是 ELK 英语学习教练专家包的自包含能力层。它刻意只依赖 Python 标准库
（json / sqlite3 / re / argparse / pathlib / hashlib），因此：

    - 任何装有 Python 3.10+ 的机器都能直接运行，无需 pip install、无需 .venv
    - 所有资产路径基于 __file__ 相对定位，与所在机器/目录完全解耦
    - 复制整个专家包 = 完整可用的能力，可无限分发

架构哲学与 ELK 一致：把可复现的（schema 契约、rubric、prompt 模板、检索索引）
做厚，把不可复现的（具体题目）做薄，用可执行检查焊死。

命令
----
    elk_core.py check                — 5 项门禁自检（资产完整性 + 数据可解析）
    elk_core.py index                — 扫描 assets/packs 建 sqlite FTS5 检索表
    elk_core.py search <关键词>       — FTS5 全文检索（找题用 SQL、用题才交 LLM）
    elk_core.py features <题文件> <作文文件> — 计算评分客观锚点
    elk_core.py render <rubric> <题文件> <作文文件> [--features JSON] — 编译评分 prompt
    elk_core.py prompts              — 列出全部 prompt 模板
    elk_core.py validate <数据文件> <schema名> — 用内置 schema 校验数据
    elk_core.py paths                — 打印解析到的资产路径（排查用）
    elk_core.py items                — 列出全部阅读题 id
    elk_core.py render-reading <题目id> [--out 文件.html] — 渲染米色护眼阅读练习页

评分输出的四维 band_range 由调用方（LLM）按 rubric 判断；本脚本只负责
提供客观锚点和契约校验，不编造分数。
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sqlite3
import sys
from pathlib import Path

# --------------------------------------------------------------------------- 路径
# 本文件位于 <skill>/scripts/elk_core.py，资产在 <skill>/assets/
SCRIPTS = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS.parent
ASSETS = SKILL_DIR / "assets"
SCHEMAS = ASSETS / "schemas"
RUBRICS = ASSETS / "rubrics"
PROMPTS = ASSETS / "prompts"
PACKS = ASSETS / "packs"
STATE = SKILL_DIR / "state"
DB = STATE / "elk.db"
BODY_LIMIT = 3000


def _paths() -> dict[str, str]:
    return {
        "skill_dir": str(SKILL_DIR),
        "assets": str(ASSETS),
        "schemas": str(SCHEMAS),
        "rubrics": str(RUBRICS),
        "prompts": str(PROMPTS),
        "packs": str(PACKS),
        "state/db": str(DB),
    }


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


# --------------------------------------------------------------------------- 资产读取
def load_json(p: Path) -> dict | list | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        _log(f"  [跳过] {p.name}: JSON 解析失败 {e}")
        return None


def list_packs() -> list[Path]:
    return sorted(PACKS.glob("*/pack.json"))


def list_data_files(pack_dir: Path) -> list[Path]:
    return sorted((pack_dir / "data").rglob("*.json")) + sorted(
        (pack_dir / "data").rglob("*.jsonl")
    )


def reading_items() -> list[dict]:
    """扫描所有数据包中的 reading-test JSON，产出索引条目。"""
    out: list[dict] = []
    for pack in list_packs():
        for f in sorted((pack.parent / "data").rglob("*.json")):
            d = load_json(f)
            if not isinstance(d, dict):
                continue
            if "passage" not in d or "question_groups" not in d:
                continue
            types, nq, nev = [], 0, 0
            for g in d["question_groups"]:
                t = g["type"] + ("/" + g["subtype"] if g.get("subtype") else "")
                types.append(t)
                nq += len(g["questions"])
                nev += sum(1 for q in g["questions"] if q.get("evidence"))
            paras = d["passage"].get("paragraphs") or []
            body = " ".join(p.get("text", "") for p in paras)[:BODY_LIMIT]
            title = d["passage"].get("title") or ""
            out.append({
                "id": d["id"],
                "kind": "reading-test",
                "module": d.get("module"),
                "set_name": d.get("set_name"),
                "title": title,
                "subtitle": d["passage"].get("subtitle"),
                "types": sorted(set(types)),
                "q_count": nq,
                "word_count": d["passage"].get("word_count", 0),
                "evidence_ratio": round(nev / nq, 2) if nq else 0.0,
                "quality_status": d.get("meta", {}).get("quality_status"),
                "_body": (title + " " + (d["passage"].get("subtitle") or "") + " " + body),
            })
    # 按 id 去重（后加载的覆盖先加载的）
    seen: dict[str, dict] = {}
    for item in out:
        seen[item["id"]] = item
    return list(seen.values())


def speaking_items() -> list[dict]:
    """扫描所有数据包中的口语 JSONL（2026 口语题库快照）。"""
    out: list[dict] = []
    for pack in list_packs():
        for f in sorted((pack.parent / "data").rglob("*.jsonl")):
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(d, dict):
                        continue
                    body = json.dumps(d, ensure_ascii=False)[:BODY_LIMIT]
                    out.append({
                        "id": d.get("id") or hashlib.md5(body.encode()).hexdigest()[:12],
                        "kind": "speaking-topic",
                        "season": d.get("season"),
                        "part": d.get("part"),
                        "topic": d.get("topic") or d.get("question") or "",
                        "_body": body,
                    })
    return out


# --------------------------------------------------------------------------- 索引（FTS5，纯标准库 sqlite3）
def cmd_index(_: argparse.Namespace) -> int:
    STATE.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    cur = con.cursor()

    cur.execute("DROP TABLE IF EXISTS items")
    cur.execute("DROP TABLE IF EXISTS search")
    cur.execute(
        "CREATE VIRTUAL TABLE search USING fts5(id UNINDEXED, kind UNINDEXED, "
        "title, body, topic, season UNINDEXED, part UNINDEXED)"
    )
    cur.execute(
        "CREATE TABLE items (id TEXT PRIMARY KEY, kind TEXT, module TEXT, set_name TEXT, "
        "title TEXT, subtitle TEXT, types TEXT, q_count INTEGER, word_count INTEGER, "
        "evidence_ratio REAL, quality_status TEXT)"
    )

    n_read, n_speak = 0, 0
    for it in reading_items():
        cur.execute(
            "INSERT OR REPLACE INTO items VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (it["id"], it["kind"], it.get("module"), it.get("set_name"), it.get("title"),
             it.get("subtitle"), json.dumps(it.get("types")), it.get("q_count"),
             it.get("word_count"), it.get("evidence_ratio"), it.get("quality_status")),
        )
        cur.execute(
            "INSERT INTO search (id, kind, title, body, topic, season, part) "
            "VALUES (?,?,?,?,?,?,?)",
            (it["id"], it["kind"], it.get("title") or "", it["_body"], "", "", ""),
        )
        n_read += 1
    for it in speaking_items():
        cur.execute(
            "INSERT INTO search (id, kind, title, body, topic, season, part) "
            "VALUES (?,?,?,?,?,?,?)",
            (it["id"], it["kind"], it.get("topic") or "", it["_body"],
             it.get("topic") or "", it.get("season") or "", it.get("part") or ""),
        )
        n_speak += 1

    con.commit()
    _log(f"索引完成：reading {n_read} 篇，speaking {n_speak} 条 → {DB}")
    return 0


def cmd_search(ns: argparse.Namespace) -> int:
    if not DB.exists():
        _log("索引不存在，先运行 elk_core.py index")
        return 1
    con = sqlite3.connect(DB)
    cur = con.cursor()
    rows = cur.execute(
        "SELECT id, kind, title, topic, season, part FROM search "
        "WHERE search MATCH ? LIMIT ?",
        (ns.query, ns.limit),
    ).fetchall()
    if not rows:
        print("（无命中）")
        return 0
    for rid, kind, title, topic, season, part in rows:
        name = topic or title or rid
        extra = f" [season {season}]" if season else ""
        print(f"- {kind} | {name}{extra} | id={rid}")
    return 0


# --------------------------------------------------------------------------- 评分客观锚点（纯计算）
_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")
_SENT = re.compile(r"(?<=[.!?])\s+")
_BULLET_NOISE = re.compile(r"^\s*[-•*·]\s*", re.M)
_CONCEPT_STOP = set(
    """a an the and or but if of to in on at for with as by is are was were be been being
this that these those it its their them they he she we you i my our your his her from
not no do does did have has had will would can could should may might must about into
over under more most some any all each other others than then so such very much many
there here what which who whom whose when where why how also however although though
because while whereas both whether give gives given own opinion essay discuss views view
people person thing things today nowadays some think thinks agree disagree extent
""".split()
)


def _words(text: str) -> list[str]:
    return _WORD.findall(text or "")


def _normalize_for_ngram(text: str) -> list[str]:
    t = _BULLET_NOISE.sub("", text or "").lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return t.split()


def _ngrams(tokens: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


def longest_common_span(a: list[str], b: list[str]) -> tuple[int, int]:
    """两个 token 序列的最长公共连续片段，返回 (长度, 在 b 中的起始下标)。"""
    if not a or not b:
        return 0, -1
    prev = [0] * (len(b) + 1)
    best_len, best_pos = 0, -1
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                v = prev[j - 1] + 1
                cur[j] = v
                if v > best_len:
                    best_len, best_pos = v, j - v
        prev = cur
    return best_len, best_pos


def concept_hit_rate(prompt_text: str, essay_text: str) -> float | None:
    q = [w for w in _WORD.findall((prompt_text or "").lower())
         if len(w) >= 4 and w not in _CONCEPT_STOP]
    if not q:
        return None
    stems = {w.lower()[:5] for w in _WORD.findall(essay_text or "")}
    return round(sum(1 for w in q if w.lower()[:5] in stems) / len(q), 4)


def compute_features(prompt_text: str, essay_text: str, ngram: int = 8) -> dict:
    words = _words(essay_text)
    wc = len(words)
    paragraphs = [p for p in re.split(r"\n\s*\n", (essay_text or "").strip()) if p.strip()]
    if len(paragraphs) == 1:
        paragraphs = [p for p in re.split(r"\n(?=\s{2,}|(?:\s*)\t)", essay_text) if p.strip()]
    sentences = [s for s in _SENT.split((essay_text or "").strip()) if s.strip()]
    sc = max(len(sentences), 1)
    e_tok = _normalize_for_ngram(essay_text)
    p_tok = _normalize_for_ngram(prompt_text)
    overlap = 0.0
    e_ng = _ngrams(e_tok, ngram)
    if e_ng and len(p_tok) >= ngram:
        p_ng = _ngrams(p_tok, ngram)
        overlap = len(e_ng & p_ng) / len(e_ng)
    span_len, _ = longest_common_span(e_tok, p_tok)
    stems = [w.lower()[:6] for w in words]
    ttr = len(set(stems)) / len(stems) if stems else 0.0
    return {
        "word_count": wc,
        "paragraph_count": max(len(paragraphs), 1),
        "sentence_count": max(len(sentences), 1),
        "avg_sentence_words": round(wc / sc, 1),
        "rubric_overlap": round(overlap, 4),
        "max_copied_span": span_len,
        "copied_span_share": round(span_len / wc, 4) if wc else 0.0,
        "concept_hit": concept_hit_rate(prompt_text, essay_text),
        "type_token_ratio": round(ttr, 4),
    }


# --------------------------------------------------------------------------- Prompt 编译
def _load_rubric(name: str) -> dict:
    p = RUBRICS / f"{name}.json"
    if not p.exists():
        raise SystemExit(f"rubric 不存在: {name}（可用: writing-task2.v1, speaking.v1）")
    return load_json(p)


def render_writing_score(rubric: dict, prompt_text: str, essay_text: str,
                         features: dict | None = None) -> str:
    feats = features if features is not None else compute_features(prompt_text, essay_text)
    lines: list[str] = []
    lines.append("You are scoring an IELTS Writing Task 2 response.")
    lines.append(f"Rubric version: {rubric['rubric_version']}  "
                 f"(you MUST echo this exact string in your output).")
    lines.append("")
    lines.append("## Pre-computed objective measurements")
    lines.append("These are already calculated for you. Do NOT recount — use them directly.")
    for a in rubric.get("objective_anchors", []):
        val = feats.get(a["id"])
        if val is not None:
            lines.append(f"- {a['id']}: {val}")
    lines.append("")
    lines.append("## Scoring criteria")
    lines.append("For EACH criterion, find the highest band whose observable checks are ALL met,")
    lines.append("then apply the half-band rule below.")
    lines.append("")
    for c in rubric["criteria"]:
        lines.append(f"### {c['id']} — {c['name']}")
        lines.append(f"Question to answer: {c['question']}")
        for band in sorted(c["bands"].keys(), reverse=True):
            b = c["bands"][band]
            lines.append(f"  Band {band}: {b['summary']}")
            for obs in b["observable"]:
                lines.append(f"    - {obs}")
        lines.append("")
    hb = rubric.get("half_band_rule", {})
    lines.append("## Half-band rule")
    lines.append(hb.get("logic", "").strip())
    lines.append("")
    lines.append("## Hard caps (applied regardless of your judgement)")
    for r in hb.get("cap_rules", []):
        lines.append(f"- {r['id']}: if {r['condition']} -> {r['effect']}")
    lines.append("")
    lines.append("## Task prompt")
    lines.append(prompt_text.strip())
    lines.append("")
    lines.append("## Candidate response")
    lines.append(essay_text.strip())
    lines.append("")
    oc = rubric.get("output_contract", {})
    lines.append("## Output")
    lines.append("Return ONLY a JSON object, no prose, matching this shape:")
    lines.append(json.dumps({
        "rubric_version": rubric["rubric_version"],
        "criteria": [
            {"id": cid, "band": 0.0, "matched_anchor": "...", "rationale": "..."}
            for cid in rubric["criteria_ids"]
        ],
        "overall_band": 0.0,
    }, indent=2))
    lines.append("")
    lines.append("Constraints:")
    for k, v in oc.get("field_notes", {}).items():
        lines.append(f"- {k}: {v}")
    for f in oc.get("forbidden", []):
        lines.append(f"- {f}")
    return "\n".join(lines)


def render_speaking_score(rubric: dict, topic_text: str, answer_text: str,
                          features: dict | None = None) -> str:
    lines: list[str] = []
    lines.append("You are scoring an IELTS Speaking response.")
    lines.append(f"Rubric version: {rubric['rubric_version']}  "
                 f"(you MUST echo this exact string in your output).")
    lines.append("")
    if features:
        lines.append("## Pre-computed objective measurements")
        for k, v in features.items():
            lines.append(f"- {k}: {v}")
        lines.append("")
    for c in rubric["criteria"]:
        lines.append(f"### {c['id']} — {c['name']}")
        for band in sorted(c["bands"].keys(), reverse=True):
            b = c["bands"][band]
            lines.append(f"  Band {band}: {b['summary']}")
            for obs in b["observable"]:
                lines.append(f"    - {obs}")
        lines.append("")
    lines.append("## Topic")
    lines.append(topic_text.strip())
    lines.append("")
    lines.append("## Candidate response")
    lines.append(answer_text.strip())
    lines.append("")
    oc = rubric.get("output_contract", {})
    lines.append("## Output")
    lines.append("Return ONLY a JSON object, no prose:")
    lines.append(json.dumps({
        "rubric_version": rubric["rubric_version"],
        "criteria": [
            {"id": cid, "band": 0.0, "matched_anchor": "...", "rationale": "..."}
            for cid in rubric["criteria_ids"]
        ],
        "overall_band": 0.0,
        "fluency": "unavailable", "pronunciation": "unavailable",
    }, indent=2))
    return "\n".join(lines)


# --------------------------------------------------------------------------- 门禁自检
def cmd_check(_: argparse.Namespace) -> int:
    print("ELK 自包含核心自检")
    print("=" * 46)
    ok = True

    # 1. 资产完整性
    print("资产完整性      ", end="")
    missing = []
    for p in [
        SCHEMAS / "pack.schema.json", SCHEMAS / "reading-test.schema.json",
        SCHEMAS / "writing-essay.schema.json", SCHEMAS / "speaking-topic.schema.json",
        SCHEMAS / "score-result.schema.json",
        RUBRICS / "writing-task2.v1.json", RUBRICS / "speaking.v1.json",
        PROMPTS / "prompts.json",
    ]:
        if not p.exists():
            missing.append(str(p.relative_to(ASSETS)))
    for pack in list_packs():
        pass
    if missing:
        ok = False
        print("失败 ❌")
        for m in missing:
            print(f"    缺失: {m}")
    else:
        print("通过 ✓")

    # 2. 数据可解析
    print("数据可解析      ", end="")
    n_read, n_speak = len(reading_items()), len(speaking_items())
    if n_read == 0 and n_speak == 0:
        ok = False
        print("失败 ❌（无数据包内容）")
    else:
        print(f"通过 ✓（reading {n_read} 篇 / speaking {n_speak} 条）")

    # 3. prompt 模板完整性
    print("prompt 模板     ", end="")
    prompts = load_json(PROMPTS / "prompts.json") or []
    if len(prompts) >= 4:
        print(f"通过 ✓（{len(prompts)} 个模板）")
    else:
        ok = False
        print(f"失败 ❌（仅 {len(prompts)} 个）")

    # 4. rubric 结构
    print("rubric 结构     ", end="")
    try:
        w = _load_rubric("writing-task2.v1")
        s = _load_rubric("speaking.v1")
        if "criteria" in w and "criteria_ids" in w and "half_band_rule" in w:
            print("通过 ✓")
        else:
            ok = False
            print("失败 ❌")
    except SystemExit as e:
        ok = False
        print(f"失败 ❌（{e}）")

    # 5. 索引状态
    print("索引状态        ", end="")
    if DB.exists():
        con = sqlite3.connect(DB)
        n = con.execute("SELECT COUNT(*) FROM search").fetchone()[0]
        con.close()
        print(f"通过 ✓（{n} 条；如需重建运行 elk_core.py index）")
    else:
        print("未建（首次使用运行 elk_core.py index）")

    print("=" * 46)
    print("全部通过 ✓" if ok else "存在失败项，请修复")
    return 0 if ok else 1


# --------------------------------------------------------------------------- 输出校验
def validate_score_output(obj: dict, rubric: dict) -> list[str]:
    """校验 LLM 输出是否符合 rubric 的 output_contract。空列表=通过。"""
    errs: list[str] = []
    if obj.get("rubric_version") != rubric["rubric_version"]:
        errs.append(f"rubric_version 不符: 期望 {rubric['rubric_version']}, "
                    f"实得 {obj.get('rubric_version')!r}")
    crit = obj.get("criteria")
    if not isinstance(crit, list):
        return ["criteria 缺失或不是数组"]
    got = {c.get("id"): c for c in crit if isinstance(c, dict)}
    for cid in rubric["criteria_ids"]:
        if cid not in got:
            errs.append(f"缺少维度 {cid}")
            continue
        c = got[cid]
        band = c.get("band")
        if not isinstance(band, (int, float)):
            errs.append(f"{cid}.band 不是数字: {band!r}")
        elif not (0.0 <= band <= 9.0):
            errs.append(f"{cid}.band 越界: {band}")
        if not (c.get("matched_anchor") or "").strip():
            errs.append(f"{cid}.matched_anchor 为空")
        if not (c.get("rationale") or "").strip():
            errs.append(f"{cid}.rationale 为空")
    overall = obj.get("overall_band")
    if not isinstance(overall, (int, float)):
        errs.append(f"overall_band 不是数字: {overall!r}")
    vals = [got[c]["band"] for c in rubric["criteria_ids"]
            if c in got and isinstance(got[c].get("band"), (int, float))]
    if len(vals) == 4 and isinstance(overall, (int, float)):
        expect = round(sum(vals) / 4 * 2) / 2
        if abs(overall - expect) > 1e-9:
            errs.append(f"overall_band={overall} 与四维均值取整 {expect} 不符")
    return errs


def cmd_validate(ns: argparse.Namespace) -> int:
    d = load_json(Path(ns.file))
    if d is None:
        return 1
    # 简化契约校验：检查必备键（完整 JSON Schema 校验超出零依赖范围）
    errs: list[str] = []
    if ns.schema in ("reading-test", "writing-essay", "speaking-topic", "pack"):
        if not isinstance(d, dict):
            errs.append("顶层不是对象")
        else:
            for key in {"pack": ["pack_id", "modules"],
                        "reading-test": ["passage", "question_groups"],
                        "writing-essay": ["prompt", "essay", "scores"],
                        "speaking-topic": ["part", "question"]}.get(ns.schema, []):
                if key not in d:
                    errs.append(f"缺少 {key}")
    if errs:
        print("校验失败：")
        for e in errs:
            print(f"  · {e}")
        return 1
    print("校验通过 ✓")
    return 0


# --------------------------------------------------------------------------- CLI
def cmd_features(ns: argparse.Namespace) -> int:
    prompt_text = Path(ns.prompt_file).read_text(encoding="utf-8")
    essay_text = Path(ns.essay_file).read_text(encoding="utf-8")
    print(json.dumps(compute_features(prompt_text, essay_text), indent=2, ensure_ascii=False))
    return 0


def cmd_render(ns: argparse.Namespace) -> int:
    rubric = _load_rubric(ns.rubric)
    prompt_text = Path(ns.prompt_file).read_text(encoding="utf-8")
    essay_text = Path(ns.essay_file).read_text(encoding="utf-8")
    features = None
    if ns.features:
        features = json.loads(ns.features)
    if ns.rubric.startswith("writing"):
        print(render_writing_score(rubric, prompt_text, essay_text, features))
    elif ns.rubric.startswith("speaking"):
        print(render_speaking_score(rubric, prompt_text, essay_text, features))
    else:
        print(render_writing_score(rubric, prompt_text, essay_text, features))
    return 0


def cmd_prompts(_: argparse.Namespace) -> int:
    prompts = load_json(PROMPTS / "prompts.json") or []
    for p in prompts:
        meta = p.get("meta", {})
        print(f"- {p['file']}  |  version={meta.get('version','?')}  |  {meta.get('purpose','')[:40]}")
    return 0


def cmd_paths(_: argparse.Namespace) -> int:
    for k, v in _paths().items():
        print(f"{k}: {v}")
    return 0


# --------------------------------------------------------------------------- 阅读页渲染
TEMPLATE = SKILL_DIR / "templates" / "reading.html"


def _esc(s) -> str:
    return html.escape(str(s or ""), quote=True).replace("\n", "<br>")


def _find_item(item_id: str) -> dict | None:
    """按 id 在内置数据包里找阅读题。"""
    for pack in list_packs():
        for f in sorted((pack.parent / "data").rglob("*.json")):
            d = load_json(f)
            if isinstance(d, dict) and d.get("id") == item_id:
                return d
    return None


def _render_passage(d: dict) -> str:
    parts = []
    for p in d.get("passage", {}).get("paragraphs", []):
        parts.append(
            f'<div class="para"><div class="para-label">{_esc(p.get("label") or "")}</div>'
            f'<div class="para-text">{_esc(p.get("text", ""))}</div></div>'
        )
    return "\n".join(parts) if parts else '<p style="color:var(--ink-faint)">（无正文）</p>'


# 题型 → 展示名
TYPE_LABEL = {
    "multiple_choice": "Multiple Choice",
    "identifying_information": "True / False / Not Given",
    "identifying_writers_views": "Yes / No / Not Given",
    "matching_information": "Matching Information",
    "matching_headings": "Matching Headings",
    "matching_features": "Matching Features",
    "matching_sentence_endings": "Matching Sentence Endings",
    "sentence_completion": "Sentence Completion",
    "summary_completion": "Summary Completion",
    "diagram_label_completion": "Diagram Label Completion",
    "short_answer": "Short Answer",
}

# 需要直接输入文字的题型（其余走选项）
FILL_TYPES = {"sentence_completion", "summary_completion",
              "diagram_label_completion", "short_answer"}


def _render_questions(d: dict) -> str:
    blocks = []
    for g in d.get("question_groups", []):
        gtype = g.get("type", "")
        subtype = g.get("subtype")
        label = TYPE_LABEL.get(gtype, gtype.replace("_", " ").title())
        if subtype:
            label += f" · {subtype}"

        rng = g.get("question_range", {})
        rng_txt = f"Q{rng.get('from', '?')}–{rng.get('to', '?')}" if rng else ""

        out = [
            '<div class="qgroup">',
            '<div class="qg-head">',
            f'<span class="qg-type">{_esc(label)}</span>',
            f'<span class="qg-range">{_esc(rng_txt)}</span>',
            '</div>',
        ]

        if g.get("instruction"):
            out.append(f'<div class="qg-instruction">{_esc(g["instruction"])}</div>')
        if g.get("word_limit"):
            out.append(f'<div class="qg-limit">{_esc(g["word_limit"])}</div>')

        # 共享选项池
        opts = g.get("options") or []
        if opts:
            out.append('<div class="options">')
            out.append('<div class="options-title">Options</div>')
            for o in opts:
                if isinstance(o, dict):
                    out.append(
                        f'<div class="opt"><span class="opt-key">{_esc(o.get("key"))}</span>'
                        f'<span>{_esc(o.get("text"))}</span></div>'
                    )
                else:
                    out.append(f'<div class="opt"><span>{_esc(o)}</span></div>')
            out.append('</div>')

        # 逐题
        for q in g.get("questions", []):
            num = q.get("number", "?")
            ans = q.get("answer")
            ans_str = "|".join(ans) if isinstance(ans, list) else str(ans or "")
            multi = isinstance(ans, list) and len(ans) > 1

            out.append(
                f'<div class="q" data-correct="{_esc(ans_str)}">'
                f'<div class="q-stem"><span class="q-num">{_esc(num)}</span>'
                f'<span class="q-stem-text">{_esc(q.get("stem", ""))}</span></div>'
            )

            if gtype in FILL_TYPES or not opts:
                out.append('<div class="fill"><input type="text" placeholder="Your answer"'
                           ' autocomplete="off" spellcheck="false"></div>')
            elif gtype == "multiple_choice":
                inp_type = "checkbox" if multi else "radio"
                choices = [
                    f'<label class="choice"><input type="{inp_type}" name="q{num}" '
                    f'value="{_esc(o.get("key") if isinstance(o, dict) else o)}">'
                    f'<span><b>{_esc(o.get("key") if isinstance(o, dict) else o)}</b> '
                    f'{_esc(o.get("text") if isinstance(o, dict) else o)}</span></label>'
                    for o in opts
                ]
                out.append(f'<div class="choices">{"".join(choices)}</div>')
            else:
                # matching_* / boolean3 等：选项只显示 key
                choices = [
                    f'<label class="choice"><input type="radio" name="q{num}" '
                    f'value="{_esc(o.get("key") if isinstance(o, dict) else o)}">'
                    f'<span><b>{_esc(o.get("key") if isinstance(o, dict) else o)}</b></span></label>'
                    for o in opts
                ]
                out.append(f'<div class="choices">{"".join(choices)}</div>')

            # 解析（默认隐藏，提交后显示）
            ev = (q.get("evidence") or [{}])[0]
            if ev:
                quote = _esc(ev.get("quote", ""))
                loc = ev.get("paragraph_label")
                loc_txt = f' — Paragraph {_esc(loc)}' if loc else ""
                paraphrase = ev.get("paraphrase")
                para_html = f'<div>{_esc(paraphrase)}</div>' if paraphrase else ""
                out.append(
                    f'<div class="reveal" style="display:none">'
                    f'<b>Correct answer:</b> {_esc(ans_str.replace("|", " / "))}'
                    f'<span class="quote">{quote}</span>{para_html}'
                    f'<div class="loc">Evidence{loc_txt}</div></div>'
                )
            out.append('</div>')

        out.append('</div>')
        blocks.append("\n".join(out))
    return "\n".join(blocks) if blocks else '<p style="color:var(--ink-faint)">（无题目）</p>'


def cmd_render_reading(ns: argparse.Namespace) -> int:
    """把一道阅读题渲染成米色护眼的自包含 HTML 练习页。"""
    d = _find_item(ns.item_id)
    if d is None:
        print(f"未找到题目 id: {ns.item_id}", file=sys.stderr)
        print("提示：用 `search <关键词>` 找题，或用 `items` 看全部 id", file=sys.stderr)
        return 1
    if not TEMPLATE.exists():
        print(f"模板缺失: {TEMPLATE}", file=sys.stderr)
        return 1

    tpl = TEMPLATE.read_text(encoding="utf-8")
    passage = d.get("passage", {})
    src = d.get("source", {})
    wc = passage.get("word_count") or sum(
        len((p.get("text") or "").split()) for p in passage.get("paragraphs", [])
    )
    nq = sum(len(g.get("questions", [])) for g in d.get("question_groups", []))

    meta_bits = [f"{wc} words", f"{nq} questions", f"module: {d.get('module', '—')}"]
    if src.get("origin"):
        meta_bits.append(f"source: {src['origin']}")

    subtitle = passage.get("subtitle")
    out = (tpl
           .replace("{{TITLE}}", _esc(passage.get("title") or d.get("id", "Reading")))
           .replace("{{SUBTITLE_HTML}}",
                    f'<div class="subtitle">{_esc(subtitle)}</div>' if subtitle else "")
           .replace("{{META_LINE}}", _esc(" · ".join(meta_bits)))
           .replace("{{PASSAGE_HTML}}", _render_passage(d))
           .replace("{{QUESTIONS_HTML}}", _render_questions(d))
           .replace("{{FOOTER_HTML}}",
                    f'Item id: {_esc(d.get("id", ""))} · '
                    f'Generated by ELK self-contained core · '
                    f'Licence: {_esc(src.get("licence") or "see pack.json")}'))

    if ns.out:
        Path(ns.out).write_text(out, encoding="utf-8")
        print(f"阅读页已生成：{ns.out}", file=sys.stderr)
    else:
        print(out)
    return 0


def cmd_list_items(_: argparse.Namespace) -> int:
    """列出全部阅读题 id，方便配合 render-reading 使用。"""
    for it in sorted(reading_items(), key=lambda x: x["id"]):
        print(f"- {it['id']}  ({it['q_count']} 题, {it['word_count']} 词) "
              f"[{', '.join(it['types'][:3])}]")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="elk_core", description="ELK 自包含核心（零依赖）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="5 项门禁自检").set_defaults(func=cmd_check)
    sub.add_parser("index", help="重建 FTS5 检索索引").set_defaults(func=cmd_index)
    p = sub.add_parser("search", help="全文检索")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("features", help="计算评分客观锚点")
    p.add_argument("prompt_file")
    p.add_argument("essay_file")
    p.set_defaults(func=cmd_features)

    p = sub.add_parser("render", help="编译评分 prompt")
    p.add_argument("rubric", choices=["writing-task2.v1", "speaking.v1"])
    p.add_argument("prompt_file")
    p.add_argument("essay_file")
    p.add_argument("--features", help="预计算特征 JSON 字符串")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("validate", help="校验数据")
    p.add_argument("file")
    p.add_argument("schema", choices=["pack", "reading-test", "writing-essay", "speaking-topic"])
    p.set_defaults(func=cmd_validate)

    sub.add_parser("prompts", help="列出 prompt 模板").set_defaults(func=cmd_prompts)
    sub.add_parser("paths", help="打印路径").set_defaults(func=cmd_paths)

    p = sub.add_parser("items", help="列出全部阅读题 id").set_defaults(func=cmd_list_items)
    p = sub.add_parser("render-reading", help="渲染阅读练习页 HTML（米色护眼）")
    p.add_argument("item_id")
    p.add_argument("--out", help="输出 HTML 文件路径（默认打印到 stdout）")
    p.set_defaults(func=cmd_render_reading)

    ns = ap.parse_args()
    return ns.func(ns)


if __name__ == "__main__":
    raise SystemExit(main())
