#!/usr/bin/env python3
"""
hand_pick.py — 从 chillies 抽 5 条样本，生成手工评分模板

为什么是 5 条而不是更多
----------------------
n=5 统计上不具显著性（QWK 的 SE 约 0.15-0.20），
但这里的目的**不是拿稳的 QWK**，而是：
    1. 跑通手工评分流程（格式、字段、校验）
    2. 验证 rubric v1 的描述是否够清晰、是否可执行
    3. 验证 5 条覆盖的 band 区间下，rubric 的封顶规则是否触发现象合理

等流程跑通后，再决定是用更多 chillies 样本扩 n，
还是接 LLM 后端跑更大批量。

抽样原则
--------
- band 5/6/7/8/9 各一条，覆盖全档位（避免聚团在某个档位）
- 题目已知、作文长度在 Task 2 合理区间（200-400 词）
- 题目-作文 alignment > 0.2（过滤掉可能错配的噪声）
- 优先选 TR 与 CC 不相等、且 != overall 的样本——
  这样能测出评分器是否真在区分四个维度，而不是只看 overall

用法
----
    ./.venv/bin/python scripts/eval/hand_pick.py
    ./.venv/bin/python scripts/eval/hand_pick.py --per-band 1 --seed 42
"""

from __future__ import annotations
from elk.paths import (
    repo_root, data_root, schemas_dir, rubrics_dir, prompts_dir,
    db_path, index_path, structured_dir,
)

import argparse
import json
import random
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import duckdb



from elk.eval.scorer import concept_hit_rate  # noqa: E402

OUT = data_root() /  "eval" / "hand_score_template.jsonl"

TARGET_BANDS = [5.0, 6.0, 7.0, 8.0, 8.5]
# chillies 的 essay_text 是英文，1 词 ≈ 6 字符（含空格）。这里按**字符**预筛，
# 再在 Python 里按词数二次过滤。写成 1500-2200 字符 ≈ 250-360 词（Task 2 正常区间）。
CHAR_RANGE = (1500, 2200)
ALIGN_MIN = 0.20


def to_band(b: float) -> float:
    """把任意 band 四舍五入到半档，作为抽样的整数 key。"""
    return round(b * 2) / 2


def _wc(text: str) -> int:
    return len(re.findall(r"[A-Za-z][A-Za-z'\-]*", text or ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-band", type=int, default=1, help="每个目标 band 抽几条")
    ap.add_argument("--seed", type=int, default=20260828)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    rows = duckdb.sql(f"""select essay_id, question, essay_text, overall_band,
                                task_achievement_band, coherence_cohesion_band
                         from read_parquet('data/raw/writing/chillies_task2/data/*.parquet')
                         where length(essay_text) between {CHAR_RANGE[0]} and {CHAR_RANGE[1]}
                         and overall_band in ({",".join(str(b) for b in TARGET_BANDS)})""").fetchall()
    print(f"原始行数（in target bands & 字符 {CHAR_RANGE[0]}-{CHAR_RANGE[1]}）: {len(rows)}", file=sys.stderr)
    if not rows:
        sys.exit("无可用样本")

    buckets: dict[float, list[tuple]] = defaultdict(list)
    for r in rows:
        eid, q, e, ob, tr, cc = r
        if _wc(e) < 200 or _wc(e) > 380:
            continue
        ch = concept_hit_rate(q, e)
        if ch is None or ch < ALIGN_MIN:
            continue
        if tr is None or cc is None:
            continue
        # 优先选 TR 与 CC 不相等的样本（说明四维是独立打分的）
        if to_band(float(tr)) == to_band(float(cc)):
            continue
        buckets[to_band(float(ob))].append(r)

    rng = random.Random(args.seed)
    picks: list[dict] = []
    for band in TARGET_BANDS:
        pool = buckets.get(band, [])
        if not pool:
            print(f"[warn] band {band} 无符合条件的样本，跳过")
            continue
        rng.shuffle(pool)
        for r in pool[: args.per_band]:
            eid, q, e, ob, tr, cc = r
            ch = concept_hit_rate(q, e)
            picks.append({
                "_template": True,
                "id": f"hand-{eid}",
                "prompt_id": f"chillies-{hash(q) & 0xFFFF:04x}",
                "task": 2,
                "test_type": "academic",
                "prompt_text": q,
                "essay_text": e,
                "ground_truth": {                  # 来自 chillies 的真值（**不展示给人**）
                    "overall": float(ob),
                    "TR": float(tr),
                    "CC": float(cc),
                },
                "alignment_score": round(ch, 4) if ch else None,
                "_instructions": (
                    "请按 rubrics/writing-task2.v1.yaml 给四维打 0-9 分（0.5 步长），"
                    "并写明 hit_band（哪一档）和 matched_anchor（命中的可观测判据原文）。"
                    "ground_truth 字段已从 chillies 注入，**请不要看**，等评估时再对照。"
                ),
                "to_fill": {
                    "criteria": [
                        {"id": "TR",  "band": None, "hit_band": None, "matched_anchor": "", "rationale": ""},
                        {"id": "CC",  "band": None, "hit_band": None, "matched_anchor": "", "rationale": ""},
                        {"id": "LR",  "band": None, "hit_band": None, "matched_anchor": "", "rationale": ""},
                        {"id": "GRA", "band": None, "hit_band": None, "matched_anchor": "", "rationale": ""},
                    ],
                    "overall_band": None,
                    "rubric_version": "writing-task2.v1",
                },
            })

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone(timedelta(hours=8))).isoformat()
    with out_path.open("w", encoding="utf-8") as fh:
        for p in picks:
            p["generated_at"] = stamp
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")

    print(f"{'id':<18}{'truth overall':<14}{'truth TR':<10}{'truth CC':<10}{'词数':<6}{'alignment':<10}")
    print("-" * 70)
    for p in picks:
        e_words = len(re.findall(r"[A-Za-z][A-Za-z'\-]*", p["essay_text"]))
        gt = p["ground_truth"]
        print(f"{p['id']:<18}{gt['overall']:<14.1f}{gt['TR']:<10.1f}{gt['CC']:<10.1f}"
              f"{e_words:<6}{p['alignment_score']:.2f}")
    print(f"\n→ {out_path.relative_to(repo_root())}")
    print(f"共 {len(picks)} 条。__template=True 字段表示待人工填写，ground_truth 请勿看。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
