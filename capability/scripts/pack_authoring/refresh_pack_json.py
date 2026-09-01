#!/usr/bin/env python3
"""refresh_pack_json.py — 按磁盘实际内容重算数据包的 pack.json 计数。

为什么需要
----------
pack.json 里的 contents.counts 是手写死的。题库从 10 篇涨到 30、100 篇的过程中，
这个数字必然漂移，而它是合规声明的一部分（"这个包有多少题、来源是什么"）。
手写 = 一定会错。所以改成从磁盘重算。

用法
----
    python3 refresh_pack_json.py <pack目录> [<pack目录> ...]
    python3 refresh_pack_json.py --all          # 处理 capability/packs 下所有包

只改 counts，不动 pack_id / licence / notes 等人工字段。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 路径基于 __file__ 推导：capability/scripts/pack_authoring/ → capability/packs
CAPABILITY = Path(__file__).resolve().parents[1]
PACKS = CAPABILITY / "packs"


def count_pack(pack_dir: Path) -> dict:
    """统计一个数据包的真实内容量。"""
    data = pack_dir / "data"
    r_items = r_questions = 0
    s_topics = s_questions = 0

    for f in sorted(data.rglob("*.json")):
        if f.name == "pack.json":
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"  [跳过] {f.relative_to(pack_dir)}: JSON 解析失败", file=sys.stderr)
            continue
        if not isinstance(d, dict):
            continue
        if "passage" in d and "question_groups" in d:
            r_items += 1
            r_questions += sum(
                len(g.get("questions", [])) for g in d.get("question_groups", [])
            )
        elif "topics" in d:
            s_topics += len(d["topics"])
            s_questions += sum(
                len(t.get("questions", [])) for t in d["topics"]
            )

    for f in sorted(data.rglob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(d, dict):
                continue
            s_topics += 1
            s_questions += len(d.get("questions", []))

    return {
        "reading_items": r_items,
        "reading_questions": r_questions,
        "speaking_topics": s_topics,
        "speaking_questions": s_questions,
        "writing_prompts": 0,
        "writing_essays": 0,
    }


def refresh(pack_dir: Path) -> bool:
    manifest = pack_dir / "pack.json"
    if not manifest.exists():
        print(f"[跳过] {pack_dir}: 无 pack.json")
        return False
    meta = json.loads(manifest.read_text(encoding="utf-8"))
    counts = count_pack(pack_dir)
    old = meta.setdefault("contents", {}).get("counts", {})
    changed = old != counts
    meta["contents"]["counts"] = counts
    if changed:
        manifest.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    flag = "更新" if changed else "无变化"
    print(
        f"[{flag}] {pack_dir.name}: reading {counts['reading_items']} 篇 / "
        f"{counts['reading_questions']} 题, speaking {counts['speaking_topics']} 话题"
    )
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description="按磁盘实际内容重算 pack.json 计数")
    ap.add_argument("packs", nargs="*", help="数据包目录")
    ap.add_argument("--all", action="store_true", help="处理 packs/ 下所有包")
    args = ap.parse_args()

    if args.all:
        targets = sorted(d for d in PACKS.iterdir() if (d / "pack.json").exists())
    elif args.packs:
        targets = [Path(p).resolve() for p in args.packs]
    else:
        ap.error("给出数据包目录，或用 --all")

    n = sum(refresh(t) for t in targets)
    print(f"完成：{len(targets)} 个包，{n} 个有更新")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
