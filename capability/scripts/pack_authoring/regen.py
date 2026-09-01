#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
regen.py — 由 DSL 源重生成题目 JSON，并对无 DSL 源的 JSON 做就地规范化。

设计目标（数据一致性，2026-09-01 沉淀）：
- DSL 是唯一可编辑的真相源；JSON 必须可由 DSL 重现。
- 任何题目的「规范答案」本身即「可接受答案」：acceptable_answers 必须以规范答案开头、
  并去重（build.py 已负责；本脚本对 JSON-native 文件补同样的归一化）。
- 仅修改 acceptable_answers 的归一形态，绝不改动答案、证据或题干等已 review 内容。

用法:
  python3 regen.py                         # 用默认包 (capability/packs/reading-news-2026-08)
  python3 regen.py --pack <包目录>
  python3 regen.py --dry                   # 只报告哪些文件会变化，不写盘
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build as B


def normalize_acceptable(q: dict) -> bool:
    """就地归一化 acceptable_answers：确保规范答案在内、去重、答案置首。返回是否改动。"""
    ans = q.get("answer")
    acc = list(q.get("acceptable_answers") or [])
    if ans is None:
        return False
    seen = set()
    out = []
    if ans not in acc:
        out.append(ans)
    for x in acc:
        if x not in seen:
            seen.add(x)
            out.append(x)
    if out != acc:
        q["acceptable_answers"] = out
        return True
    return False


def regen_from_dsl(dsl_path: Path, out_dir: Path, dry: bool) -> str:
    doc = B.build(B.parse(dsl_path))
    cur_path = out_dir / f"{doc['id']}.json"
    if cur_path.exists():
        cur = json.loads(cur_path.read_text(encoding="utf-8"))
    else:
        cur = None
    # 排除构建时间戳
    probe = json.loads(json.dumps(doc))
    probe["meta"].pop("created_at", None)
    probe.get("source", {}).pop("retrieved_at", None)
    if cur is not None:
        cur["meta"].pop("created_at", None)
        cur.get("source", {}).pop("retrieved_at", None)
    if cur == probe:
        return "unchanged"
    if not dry:
        cur_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return "regenerated"


def normalize_json_only(json_path: Path, dry: bool) -> str:
    d = json.load(open(json_path, encoding="utf-8"))
    changed = False
    for g in d.get("question_groups", []):
        for q in g.get("questions", []):
            if normalize_acceptable(q):
                changed = True
    if changed and not dry:
        json_path.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return "normalized" if changed else "unchanged"


def main():
    ap = argparse.ArgumentParser(description="由 DSL 重生成 JSON / 归一化 JSON-native 文件")
    ap.add_argument("--pack", default=str(B.DEFAULT_PACK), help="数据包目录")
    ap.add_argument("--dry", action="store_true", help="只报告，不写盘")
    a = ap.parse_args()

    pack = Path(a.pack).resolve()
    dsl_dir = pack / "_dsl"
    news = pack / "data" / "reading" / "news"
    if not news.is_dir():
        raise SystemExit(f"✗ 找不到数据目录：{news}")

    dsl_slugs = {p.stem for p in dsl_dir.glob("*.txt")} if dsl_dir.is_dir() else set()
    json_files = sorted(news.glob("*.json"))

    regen_n = norm_n = same_n = 0
    for jf in json_files:
        slug = jf.stem
        if slug in dsl_slugs:
            status = regen_from_dsl(dsl_dir / f"{slug}.txt", news, a.dry)
            if status == "regenerated":
                regen_n += 1
                print(f"  ↻ DSL→JSON  {slug}")
            else:
                same_n += 1
        else:
            status = normalize_json_only(jf, a.dry)
            if status == "normalized":
                norm_n += 1
                print(f"  ✎ 归一化    {slug}  (无 DSL 源)")
            else:
                same_n += 1

    print(f"\n完成：DSL 重生成 {regen_n} / JSON 归一化 {norm_n} / 未变 {same_n} / 共 {len(json_files)}")


if __name__ == "__main__":
    main()
