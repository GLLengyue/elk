#!/usr/bin/env python3
"""
build_index.py — 生成 index.jsonl 与 FTS5 检索表

为什么先做这个
--------------
原方案 §2 末尾那 5 条「为 LLM 高效消费而做的设计约束」里，最要紧的一条是：

    **state/ielts.db + FTS5 承担「找题」，LLM 只承担「用题」，不让模型扫文件**

149 道题现在还能靠遍历，到 1000 道时遍历就是在烧 token。
所以索引是所有能力的地基——prompt 层要拿"这一道题"，而不是"全部数据"。

两条产出
--------
1. `data/structured/index.jsonl` —— 每条约 100 token 的精简视图。
   SKILL 两步检索：先读 index 定位，再按需读单篇原文。
2. `state/ielts.db` 的 `search` FTS5 虚拟表 + `items` 明细表。
   支持 `MATCH` 全文检索与按 module/types/part 过滤。

设计取舍
--------
- index 条目**故意不含正文**——含了就失去"精简"的意义（单篇 6k token vs 索引 100 token）
- FTS5 的 body 字段**截断到 3000 字符**：够检索命中，不至于把库撑大
- 重建是幂等的：删表重建，不追加

用法
----
    ./.venv/bin/python scripts/build/build_index.py
    ./.venv/bin/python scripts/build/build_index.py --stats
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STRUCT = ROOT / "data" / "structured"
DB = ROOT / "state" / "ielts.db"
INDEX = STRUCT / "index.jsonl"

BODY_LIMIT = 3000


def reading_items() -> list[dict]:
    """扫描所有 reading-test JSON，产出索引条目。"""
    out = []
    for f in sorted(STRUCT.glob("reading/**/*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"  [跳过] {f.name}: JSON 解析失败 {e}", file=sys.stderr)
            continue
        # 只认 reading-test 结构（顶层有 passage + question_groups）
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
            "text_missing": bool(d.get("meta", {}).get("qc", {}).get("passage_text_missing")),
            "path": str(f.relative_to(ROOT)),
            "_body": (title + " " + (d["passage"].get("subtitle") or "") + " " + body),
        })
    return out


def _as_text(v) -> str:
    """把任意字段安全地转成可索引文本。

    口语 snapshot 里 cue_card 是 dict、questions 里也混着 dict 与 str，
    直接 " ".join 会抛 TypeError（实测踩到）。
    """
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float, bool)):
        return str(v)
    if isinstance(v, dict):
        # 优先取常见文本键，都没有就整体序列化
        for k in ("text", "question", "name", "content", "prompt"):
            if isinstance(v.get(k), str):
                return v[k]
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (list, tuple)):
        return " ".join(_as_text(x) for x in v)
    return str(v)


def speaking_items() -> list[dict]:
    """扫描口语 snapshot JSONL，按题组产出索引条目。"""
    out = []
    for f in sorted(STRUCT.glob("speaking/**/*.jsonl")):
        for ln in f.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                d = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if "topic_name" not in d:
                continue

            qs = d.get("questions") or []
            p3 = d.get("part3_questions") or []
            topic = _as_text(d.get("topic_name"))
            cue = _as_text(d.get("cue_card"))
            body = " ".join(
                [topic, cue] + [_as_text(q) for q in qs] + [_as_text(q) for q in p3]
            )[:BODY_LIMIT]

            out.append({
                "id": d["id"],
                "kind": "speaking-topic",
                "part": d.get("part"),
                "season": d.get("season"),
                "catalog": _as_text(d.get("catalog")) or None,
                "title": topic,
                "q_count": len(qs) + len(p3),
                "word_count": len(body.split()),
                "path": str(f.relative_to(ROOT)),
                "_body": body,
            })
    return out


def build_db(items: list[dict]) -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB))
    c = conn.cursor()

    # 幂等重建：不追加，避免重复行
    c.execute("DROP TABLE IF EXISTS items")
    c.execute("DROP TABLE IF EXISTS search")
    c.execute("""
        CREATE TABLE items (
            id       TEXT PRIMARY KEY,
            kind     TEXT,
            module   TEXT,
            part     INTEGER,
            season   TEXT,
            title    TEXT,
            types    TEXT,
            q_count  INTEGER,
            path     TEXT
        )
    """)
    c.execute("""
        CREATE VIRTUAL TABLE search USING fts5(
            id UNINDEXED, title, body, kind UNINDEXED, path UNINDEXED
        )
    """)

    for it in items:
        c.execute(
            "INSERT OR REPLACE INTO items VALUES (?,?,?,?,?,?,?,?,?)",
            (it["id"], it["kind"], it.get("module"), it.get("part"),
             it.get("season"), it.get("title"),
             json.dumps(it.get("types") or [], ensure_ascii=False),
             it.get("q_count", 0), it.get("path")),
        )
        c.execute(
            "INSERT INTO search (id, title, body, kind, path) VALUES (?,?,?,?,?)",
            (it["id"], it.get("title") or "", it.get("_body") or "",
             it["kind"], it.get("path")),
        )

    conn.commit()
    conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", action="store_true", help="只打印统计，不重建")
    args = ap.parse_args()

    items = reading_items() + speaking_items()
    if not items:
        sys.exit("没有可索引的结构化数据")

    # 索引条目不含 _body（正文留给单篇文件），但写库时需要，先分离
    for_db = items
    for_json = [{k: v for k, v in it.items() if not k.startswith("_")} for it in items]

    if args.stats:
        from collections import Counter
        c = Counter(i["kind"] for i in items)
        print(f"可索引条目 {len(items)}")
        for k, v in c.most_common():
            print(f"  {k:<18}{v}")
        return 0

    INDEX.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone(timedelta(hours=8))).isoformat()
    with INDEX.open("w", encoding="utf-8") as fh:
        for it in for_json:
            it["indexed_at"] = stamp
            fh.write(json.dumps(it, ensure_ascii=False) + "\n")

    build_db(for_db)

    rd = [i for i in items if i["kind"] == "reading-test"]
    sp = [i for i in items if i["kind"] == "speaking-topic"]
    print(f"索引条目 {len(items)} 条（阅读 {len(rd)} / 口语 {len(sp)}）")
    print(f"  阅读题目合计 {sum(i['q_count'] for i in rd)} 道")
    print(f"  口语题组合计 {len(sp)} 组 / {sum(i['q_count'] for i in sp)} 问")
    avg = sum(len(json.dumps(i, ensure_ascii=False)) for i in for_json) / len(for_json)
    print(f"  平均条目 {avg:.0f} 字符（约 {avg/4:.0f} token，原方案目标 ~100）")
    print(f"\n→ {INDEX.relative_to(ROOT)}")
    print(f"→ {DB.relative_to(ROOT)} (items + search FTS5)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
