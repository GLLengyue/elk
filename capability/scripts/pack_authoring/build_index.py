#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_index.py — 由 JSON 自动生成练习页目录 index.html。

为什么不用手写 index.html：
  手工维护时标题 / 来源 URL 与条目会错位（首批 10 篇的旧 index.html 就是错位的：
  02 号卡片显示了古 DNA 的标题和 URL，href 却指向 urban-heat）。
  这里改为自动发现：渲染页正文里内嵌了条目 id，据此把 html 与 json 对齐，
  并额外校验「渲染页 <title> == JSON passage.title」，
  任一环节对不上就报错退出，不再让错位悄悄溜进目录页。

用法:
  python3 build_index.py [--pack <数据包目录>] [--practice <练习页目录>] [--out <输出 html>]

路径全部基于 __file__ 推导，不含机器相关硬编码：
  默认 pack     = <repo>/capability/packs/reading-news-2026-08
  默认 practice = 上述包下的 _practice/（产物目录，已 gitignore）
"""
import argparse
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
CAPABILITY = _HERE.parents[1]
DEFAULT_PACK = CAPABILITY / "packs" / "reading-news-2026-08"
DEFAULT_PRACTICE = DEFAULT_PACK / "_practice"
PACK_NEWS = DEFAULT_PACK / "data" / "reading" / "news"
PRACTICE = DEFAULT_PRACTICE
OUT = PRACTICE / "index.html"

TYPE_LABEL = {
    "identifying_information": "T/F/NG",
    "identifying_writers_views": "Y/N/NG",
    "matching_headings": "标题匹配",
    "matching_information": "段落匹配",
    "matching_features": "特征匹配",
    "multiple_choice": "单选",
    "sentence_completion": "句子填空",
    "short_answer": "简答",
}
SUMMARY_LABEL = {"note": "笔记填空", "table": "表格填空"}


BATCH_RE = re.compile(r"^##\s*第(.+?)批\s+(\d+)\s*-\s*(\d+)\s*(?:·\s*(.+))?", re.M)


def load_batch_names() -> dict:
    """从选题池解析「第 N 批」的展示名，避免目录页与选题池各写一份。"""
    pool = _HERE / "topic_pool.md"
    if not pool.exists():
        return {}
    out = {}
    for m in BATCH_RE.finditer(pool.read_text(encoding="utf-8")):
        cn, lo, hi, topic = m.group(1), int(m.group(2)), int(m.group(3)), (m.group(4) or "").strip()
        idx = lo // 10 + 1
        out[idx] = f"第{cn}批 {lo}-{hi}" + (f" · {topic}" if topic else "")
    return out


def group_label(g: dict) -> str:
    t = g["type"]
    if t == "summary_completion":
        return SUMMARY_LABEL.get(g.get("subtype", ""), "摘要填空")
    return TYPE_LABEL.get(t, t)


CSS = """
  :root {
    --bg:#FAF6EC; --text:#3A3226; --accent:#8A6D45; --card:#FFFFFF;
    --border:#E5DCC8; --muted:#8C8272; --ok:#4F7A52;
    --batch:#EFE6D2;
  }
  * { box-sizing:border-box; }
  body {
    background:var(--bg); color:var(--text); font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
    margin:0; padding:40px 24px 80px; line-height:1.6;
  }
  .wrap { max-width:760px; margin:0 auto; }
  h1 { font-size:26px; margin:0 0 6px; letter-spacing:.5px; }
  .sub { color:var(--muted); font-size:14px; margin-bottom:8px; }
  .note {
    background:#F1EAD8; border:1px solid var(--border); border-radius:10px;
    padding:12px 16px; font-size:13px; color:#6B6150; margin:16px 0 20px;
  }
  .batch-hd {
    font-size:13px; color:var(--accent); font-weight:700; letter-spacing:1px;
    margin:26px 0 10px; padding-bottom:6px; border-bottom:1px solid var(--border);
  }
  .card {
    display:flex; align-items:center; gap:16px; text-decoration:none; color:inherit;
    background:var(--card); border:1px solid var(--border); border-radius:12px;
    padding:16px 18px; margin-bottom:12px; transition:transform .08s, box-shadow .08s;
  }
  .card:hover { transform:translateY(-1px); box-shadow:0 4px 14px rgba(138,109,69,.12); }
  .num { font-size:20px; font-weight:700; color:var(--accent); min-width:40px; text-align:center; }
  .body { flex:1; }
  .title { font-size:16px; font-weight:600; }
  .tags { margin:6px 0 4px; }
  .chip {
    display:inline-block; background:#F1EAD8; color:var(--accent); border-radius:20px;
    font-size:12px; padding:2px 10px; margin-right:6px;
  }
  .types { font-size:12.5px; color:var(--muted); }
  .src { font-size:11px; color:#B5A98F; margin-top:4px; max-width:520px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .go { color:var(--ok); font-weight:600; font-size:14px; white-space:nowrap; }
  .foot { text-align:center; color:var(--muted); font-size:12px; margin-top:36px; }
"""


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def main():
    ap = argparse.ArgumentParser(description="由 JSON 自动生成练习页目录 index.html")
    ap.add_argument("--pack", default=str(DEFAULT_PACK), help="数据包目录")
    ap.add_argument("--practice", default=str(DEFAULT_PRACTICE), help="练习页 HTML 所在目录")
    ap.add_argument("--out", default=None, help="输出 html 路径（默认 <practice>/index.html）")
    a = ap.parse_args()

    global PACK_NEWS, PRACTICE, OUT
    PACK_NEWS = Path(a.pack) / "data" / "reading" / "news"
    PRACTICE = Path(a.practice)
    OUT = Path(a.out) if a.out else PRACTICE / "index.html"
    if not PACK_NEWS.is_dir():
        print(f"✗ 找不到数据目录：{PACK_NEWS}", file=sys.stderr)
        sys.exit(1)
    if not PRACTICE.is_dir():
        print(f"✗ 找不到练习页目录：{PRACTICE}（先渲染练习页，或传 --practice）", file=sys.stderr)
        sys.exit(1)

    docs = {}
    for p in sorted(PACK_NEWS.glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        docs[d["id"]] = d

    htmls = sorted(
        [p for p in PRACTICE.glob("*.html") if p.name != "index.html"],
        key=lambda p: (int(re.match(r"^(\d+)", p.name).group(1)), p.name),
    )

    rows = []
    errors = []
    for hp in htmls:
        raw = hp.read_text(encoding="utf-8")
        hits = [i for i in docs if i in raw]
        if len(hits) != 1:
            errors.append(f"{hp.name}: 在正文里匹配到 {len(hits)} 个条目 id {hits}，无法确定映射")
            continue
        pid = hits[0]
        d = docs[pid]

        m = re.search(r"<title>(.*?)</title>", raw, re.S)
        rendered_title = m.group(1).strip() if m else ""
        if rendered_title != d["passage"]["title"]:
            errors.append(
                f"{hp.name}: 标题不一致\n    渲染页: {rendered_title}\n    JSON  : {d['passage']['title']}"
            )
            continue

        nq = sum(len(g["questions"]) for g in d["question_groups"])
        rows.append({
            "num": re.match(r"^(\d+)", hp.name).group(1),
            "href": hp.name,
            "title": d["passage"]["title"],
            "wc": d["passage"]["word_count"],
            "nq": nq,
            "types": " · ".join(group_label(g) for g in d["question_groups"]),
            "url": d["source"].get("source_url", ""),
        })

    if errors:
        print("✗ 目录页未生成，先修以下问题：", file=sys.stderr)
        for e in errors:
            print("  - " + e, file=sys.stderr)
        sys.exit(1)

    total_q = sum(r["nq"] for r in rows)
    parts = [
        "<!DOCTYPE html>", '<html lang="zh-CN">', "<head>", '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>雅思阅读练习库 · 新闻改写 {len(rows)} 篇</title>",
        "<style>" + CSS + "</style>", "</head>", "<body>", '<div class="wrap">',
        "<h1>雅思阅读练习库 · 新闻改写</h1>",
        f'<div class="sub">{len(rows)} 篇 · 2026-08 新闻改编 · 共 {total_q} 题 · 素材日期 2026-06 ~ 09</div>',
        '<div class="note">每页米色护眼、自带计时，提交后即时判分并展开答案与原文证据。'
        '所有题目为<b>原创改写</b>（非官方真题），证据偏移由脚本重算，答案未经人工复核。'
        '目录页由 <code>build_index.py</code> 从 JSON 自动生成，标题与来源 URL 不会出现错位。</div>',
    ]

    # 按批分组（每 10 篇一段）；批次名自动从选题池解析，不硬编码
    batch_names = load_batch_names()
    for i, r in enumerate(rows):
        if i % 10 == 0:
            b = i // 10 + 1
            parts.append(f'<div class="batch-hd">{batch_names.get(b, f"第 {b} 批")}</div>')
        parts.append(
            f'    <a class="card" href="{r["href"]}">\n'
            f'      <div class="num">{r["num"]}</div>\n'
            f'      <div class="body">\n'
            f'        <div class="title">{esc(r["title"])}</div>\n'
            f'        <div class="tags"><span class="chip">{r["wc"]} 词</span>'
            f'<span class="chip">{r["nq"]} 题</span></div>\n'
            f'        <div class="types">{esc(r["types"])}</div>\n'
            f'        <div class="src">{esc(r["url"])}</div>\n'
            f'      </div>\n'
            f'      <div class="go">开始 →</div>\n'
            f'    </a>'
        )

    parts.append("<div class=\"foot\">ELK English Learning Kit · reading-news-2026-08</div>")
    parts.append("</div>\n</body>\n</html>")
    OUT.write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(f"✓ index.html 已生成：{len(rows)} 篇 / {total_q} 题")


if __name__ == "__main__":
    main()
