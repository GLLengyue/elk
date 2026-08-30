#!/usr/bin/env python3
"""
hand_score_guide.py — 手工评分的填写辅助

为什么需要这个工具
------------------
直接打开 hand_score_template.jsonl 看到原始 essay_text，要自己数词数、算 alignment、
找最长连续抄题片段、判封顶规则……这套工作每条样本要 5-10 分钟。

本工具为每条样本生成一张"评估卡"：
    1. 客观锚点（已算好）
    2. 触发的封顶规则（自动检查）
    3. essay_text 的前 200 词（避免滚屏看长作文）
    4. rubric v1 的 5 档可观测判据（按维度列出，方便引用 matched_anchor）

用户读完卡片后，对照 rubric 给 4 维 × 5 档打分；填入 hand_score_filled.jsonl。

用法
----
    ./.venv/bin/python scripts/eval/hand_score_guide.py
    ./.venv/bin/python scripts/eval/hand_score_guide.py --only hand-636e7f7ed1146c00196053aa

    # 写入一个填好空字段的工作副本（保留 ground_truth 字段，但**默认不打印**）
    ./.venv/bin/python scripts/eval/hand_score_guide.py --write-stubs hand_score_filled.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "eval"))

from scorer import load_rubric, compute_features, apply_cap_rules  # noqa: E402

TEMPLATE = ROOT / "data" / "eval" / "hand_score_template.jsonl"
RUBRIC = load_rubric()


def _band_label(ws: dict) -> str:
    return "/".join(f"{b}:{ws[b]}" for b in (9, 8, 7, 6, 5) if ws.get(b))


def _hit_band_hints(features: dict, essay_text: str) -> list[str]:
    """基于客观量的简单 band 提示，仅供人工参考，不作为评分依据。"""
    hints = []
    wc = features["word_count"]
    if wc < 200:
        hints.append(f"⚠ 词数 {wc} < 200（TR too_short 封顶 5.0）")
    elif wc < 250:
        hints.append(f"· 词数 {wc} 偏短（Task 2 推荐 ≥250）")
    if features["max_copied_span"] >= 12:
        hints.append(f"⚠ 最长连续抄题 {features['max_copied_span']} 词（copied_block 封顶 5.0）")
    if (features.get("concept_hit") or 0) < 0.01:
        hints.append("⚠ 概念词命中 0（off_topic 封顶 4.0）")
    return hints


def print_card(doc: dict, show_truth: bool = False) -> None:
    eid = doc["id"]
    feats = compute_features(doc["prompt_text"], doc["essay_text"])
    caps = apply_cap_rules(RUBRIC, feats, {"TR": 9.0, "CC": 9.0, "LR": 9.0, "GRA": 9.0})

    print(f"\n{'=' * 76}")
    print(f"# {eid}   (alignment {doc.get('alignment_score', '?')})")
    print(f"{'=' * 76}")
    print(f"\n## 题目\n{doc['prompt_text']}")

    print(f"\n## 客观锚点")
    for k in ("word_count", "paragraph_count", "sentence_count", "avg_sentence_words",
              "rubric_overlap", "max_copied_span", "copied_span_share", "concept_hit",
              "type_token_ratio"):
        print(f"  {k}: {feats.get(k)}")

    hints = _hit_band_hints(feats, doc["essay_text"])
    if hints:
        print(f"\n## 自动检查")
        for h in hints:
            print(f"  {h}")
    if caps["applied_caps"]:
        print(f"\n## 触发的封顶（已知规则）")
        for c in caps["applied_caps"]:
            print(f"  · {c['rule']}: {c['criterion']} 上限 5.0 — {c['reason'][:60]}")
    else:
        print(f"\n## 自动检查：未触发任何封顶规则")

    print(f"\n## 作文（前 200 词）")
    words = doc["essay_text"].split()
    preview = " ".join(words[:200])
    if len(words) > 200:
        preview += f"  ... [+{len(words)-200} 词]"
    print(preview)

    print(f"\n## rubric v1 5 档可观测判据（按维度）")
    for c in RUBRIC["criteria"]:
        print(f"\n  ### {c['id']} — {c['name']}")
        # YAML 解析时数字 key 保持 int：bands[5] 而非 bands['5']
        for band in sorted(c["bands"].keys(), reverse=True):
            b = c["bands"][band]
            print(f"    [{band}] {b['summary']}")

    if show_truth:
        print(f"\n## !GROUND TRUTH (真值，仅验证用)")
        print(f"  {json.dumps(doc.get('ground_truth', {}), ensure_ascii=False)}")

    print(f"\n## 填写指引")
    tf = doc.get("to_fill", {})
    print(f"  对每维选一档，把 hit_band / matched_anchor / rationale / band 填入 to_fill.criteria[i]")
    print(f"  算 overall = (TR+CC+LR+GRA)/4 四舍五入到半档，填 to_fill.overall_band")
    print(f"  rubric_version 保持 writing-task2.v1")


def write_stubs(template_path: Path, out_path: Path, include_truth: bool = False) -> int:
    """写一份待填的空副本（每条 to_fill 都是空，方便人工边读边填）。"""
    out = []
    for line in template_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        doc = json.loads(line)
        d = {
            "id": doc["id"],
            "prompt_text": doc["prompt_text"],
            "essay_text": doc["essay_text"],
            "rubric_version": "writing-task2.v1",
            "to_fill": {
                "criteria": [
                    {"id": c, "band": None, "hit_band": None, "matched_anchor": "", "rationale": ""}
                    for c in ("TR", "CC", "LR", "GRA")
                ],
                "overall_band": None,
            },
        }
        if include_truth:
            d["ground_truth"] = doc.get("ground_truth", {})
        out.append(d)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for d in out:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    return len(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="只看指定 id")
    ap.add_argument("--show-truth", action="store_true", help="打印 ground_truth（验证用）")
    ap.add_argument("--write-stubs", help="写一份待填副本到指定路径")
    ap.add_argument("--include-truth-in-stubs", action="store_true", help="副本里也带真值")
    args = ap.parse_args()

    if not TEMPLATE.exists():
        sys.exit(f"缺少 {TEMPLATE} —— 先跑 scripts/eval/hand_pick.py")

    if args.write_stubs:
        n = write_stubs(TEMPLATE, Path(args.write_stubs), args.include_truth_in_stubs)
        print(f"已生成 {n} 条空模板 -> {args.write_stubs}")
        print("默认不含真值；--include-truth-in-stubs 仅在自行验证时使用。")
        return 0

    docs = []
    for line in TEMPLATE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        if args.only and d["id"] != args.only:
            continue
        docs.append(d)

    if not docs:
        sys.exit(f"无匹配 id={args.only}")
    for d in docs:
        print_card(d, args.show_truth)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
