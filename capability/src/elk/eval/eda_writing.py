#!/usr/bin/env python3
"""
写作数据集 EDA —— 决定 P0 评分器的可信度上限，必须最先跑。

零重度依赖：只用 duckdb（读 parquet + SQL 聚合）。

检查项：
  1. band 分布          —— 若高度集中（如 6.5 占 60%），说明是模板打分，回归天花板低
  2. 四维一致性         —— |overall - mean(4维)| <= 0.5 的覆盖率，目标 >= 90%
  3. 维度雷同率         —— 四维分数完全相同的比例，过高说明维度是复制出来的
  4. 维度间相关性       —— 若 TR/CC/LR/GRA 两两近乎完全相关，等于只有一个信号
  5. 半档精度           —— chillies 的 LR/GRA 是整数列，需量化对回归目标的影响
  6. 词数分布           —— 过滤 Task2 <250 词的无效样本
  7. 重复率             —— 精确重复 + prompt 重复

用法:
  .venv/bin/python scripts/eval/eda_writing.py
  .venv/bin/python scripts/eval/eda_writing.py --json-only
"""

from __future__ import annotations
from elk.paths import (
    repo_root, data_root, schemas_dir, rubrics_dir, prompts_dir,
    db_path, index_path, structured_dir,
)

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import duckdb


RAW = data_root() /  "raw" / "writing"
REPORTS = data_root() /  "eval" / "reports"
CST = timezone(timedelta(hours=8))


def pct(x: float) -> str:
    return f"{x * 100:5.1f}%"


def bar(count: int, maxc: int, width: int = 34) -> str:
    n = int(round(count / maxc * width)) if maxc else 0
    return "█" * n


# 各数据集的分数列映射。hai2131 全是 VARCHAR，必须 TRY_CAST。
SPECS = {
    "chillies_task2": {
        "path": "chillies_task2/data/*.parquet",
        "task": 2,
        "overall": "overall_band",
        "dims": {
            "TR": "task_achievement_band",
            "CC": "coherence_cohesion_band",
            "LR": "lexical_resource_band",
            "GRA": "grammatical_range_band",
        },
        "text": "essay_text",
        "prompt": "question",
        "cast": False,
    },
    "hai2131_task1": {
        "path": "hai2131_task1/data/*.parquet",
        "task": 1,
        "overall": "overall_band_score",
        "dims": {
            "TR": "task_response_score",
            "CC": "coherence_cohesion_score",
            "LR": "lexical_resource_score",
            "GRA": "grammatical_range_accuracy_score",
        },
        "text": "content",
        "prompt": "image_description",
        "cast": True,
    },
    "btnotpt_task2": {
        "path": "btnotpt_task2/data/*.parquet",
        "task": 2,
        "overall": "Overall",
        "dims": {"TR": "TR", "CC": "CC", "LR": "LR", "GRA": "GRA"},
        "text": "essay",
        "prompt": "topic",
        "cast": False,
    },
}


def num(col: str, cast: bool) -> str:
    return f"TRY_CAST({col} AS DOUBLE)" if cast else f"CAST({col} AS DOUBLE)"


def analyse(name: str, spec: dict) -> dict:
    p = str(RAW / spec["path"])
    cast = spec["cast"]
    ov = num(spec["overall"], cast)
    dims = {k: num(v, cast) for k, v in spec["dims"].items()}
    mean4 = f"(({'+'.join(dims.values())})/4.0)"
    text, prompt = spec["text"], spec["prompt"]

    print("\n" + "=" * 66)
    print(f"  {name}   (Task {spec['task']})")
    print("=" * 66)

    total = duckdb.sql(f"SELECT COUNT(*) FROM '{p}'").fetchone()[0]
    # 有效样本：overall 与四维都能转成数字，且在 0-9
    where = (f"{ov} IS NOT NULL AND {ov} BETWEEN 0 AND 9 AND "
             + " AND ".join(f"{d} BETWEEN 0 AND 9" for d in dims.values()))
    valid = duckdb.sql(f"SELECT COUNT(*) FROM '{p}' WHERE {where}").fetchone()[0]
    print(f"  总行数 {total:,}   分数有效行 {valid:,}  ({pct(valid / total)})")

    res: dict = {"dataset": name, "task": spec["task"], "total": total, "valid": valid}

    # ---- 1. band 分布 ----
    dist = duckdb.sql(
        f"SELECT {ov} AS b, COUNT(*) c FROM '{p}' WHERE {where} GROUP BY 1 ORDER BY b"
    ).fetchall()
    maxc = max((c for _, c in dist), default=1)
    print("\n  [1] overall band 分布")
    for b, c in dist:
        print(f"    {b:>4}  {c:>6,}  {pct(c / valid)}  {bar(c, maxc)}")
    # 集中度：最大单档占比
    top_share = max(c for _, c in dist) / valid if dist else 0
    print(f"    → 最大单档占比 {pct(top_share)}"
          + ("   ⚠ 高度集中，疑似模板打分" if top_share > 0.40 else "   （分布尚可）"))
    res["band_distribution"] = {str(b): c for b, c in dist}
    res["top_band_share"] = round(top_share, 4)

    # ---- 2. 四维一致性 ----
    cons = duckdb.sql(f"""
        SELECT
          AVG(CASE WHEN ABS({ov} - {mean4}) <= 0.5 THEN 1.0 ELSE 0.0 END) AS within05,
          AVG(CASE WHEN ABS({ov} - {mean4}) <= 0.25 THEN 1.0 ELSE 0.0 END) AS within025,
          AVG(ABS({ov} - {mean4})) AS mad
        FROM '{p}' WHERE {where}
    """).fetchone()
    within05, within025, mad = cons
    print("\n  [2] 四维一致性  |overall − mean(TR,CC,LR,GRA)|")
    print(f"    ≤0.5  覆盖 {pct(within05)}   {'✓ 达标' if within05 >= 0.90 else '⚠ 低于 90% 目标'}")
    print(f"    ≤0.25 覆盖 {pct(within025)}")
    print(f"    平均绝对偏差 {mad:.3f}")
    res["consistency_within_0.5"] = round(within05, 4)
    res["consistency_within_0.25"] = round(within025, 4)
    res["mean_abs_dev"] = round(mad, 4)

    # ---- 3. 维度雷同率 ----
    allsame = duckdb.sql(
        f"SELECT AVG(CASE WHEN {dims['TR']}={dims['CC']} AND {dims['CC']}={dims['LR']} "
        f"AND {dims['LR']}={dims['GRA']} THEN 1.0 ELSE 0.0 END) FROM '{p}' WHERE {where}"
    ).fetchone()[0]
    print("\n  [3] 维度雷同率（四维分数完全相同）")
    print(f"    {pct(allsame)}"
          + ("   ⚠ 过高，维度可能是复制出来的" if allsame > 0.50 else "   （可接受）"))
    res["all_dims_identical_rate"] = round(allsame, 4)

    # ---- 4. 维度间相关性 ----
    print("\n  [4] 维度间相关系数")
    keys = list(dims)
    corr = {}
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = dims[keys[i]], dims[keys[j]]
            r = duckdb.sql(f"SELECT corr({a}, {b}) FROM '{p}' WHERE {where}").fetchone()[0]
            corr[f"{keys[i]}-{keys[j]}"] = round(r, 3) if r is not None else None
    for k, v in corr.items():
        flag = "  ⚠ 近乎同一信号" if v is not None and v > 0.95 else ""
        print(f"    {k:<8} {v}{flag}")
    res["dim_correlation"] = corr

    # ---- 5. 半档精度 ----
    print("\n  [5] 半档精度（含 .5 的取值占比）")
    half = {}
    for label, col in [("overall", ov)] + list(dims.items()):
        r = duckdb.sql(
            f"SELECT AVG(CASE WHEN {col} = FLOOR({col}) THEN 0.0 ELSE 1.0 END) "
            f"FROM '{p}' WHERE {where}"
        ).fetchone()[0]
        half[label] = round(r, 4) if r is not None else None
        mark = "  ⚠ 整数列，无半档" if r is not None and r < 0.01 else ""
        print(f"    {label:<8} 半档占比 {pct(r or 0)}{mark}")
    res["half_band_ratio"] = half

    # ---- 6. 文本长度 ----
    wc = duckdb.sql(
        f"SELECT MIN(w), AVG(w), MAX(w) FROM ("
        f"SELECT array_length(str_split_regex(regexp_replace({text},'\\s+',' ','g'),' ')) AS w "
        f"FROM '{p}' WHERE {text} IS NOT NULL)"
    ).fetchone()
    lo = 250 if spec["task"] == 2 else 130
    n_short = duckdb.sql(
        f"SELECT COUNT(*) FROM '{p}' WHERE {where} AND "
        f"array_length(str_split_regex(regexp_replace({text},'\\s+',' ','g'),' ')) < {lo}"
    ).fetchone()[0]
    print("\n  [6] 词数")
    print(f"    min {wc[0]}  avg {wc[1]:.0f}  max {wc[2]}")
    print(f"    低于 {lo} 词: {n_short:,} ({pct(n_short / valid)})  ← 建议剔除")
    res["word_count"] = {"min": wc[0], "avg": round(wc[1], 1), "max": wc[2], "too_short": n_short}

    # ---- 7. 重复 ----
    dup_text = duckdb.sql(
        f"SELECT COUNT(*) - COUNT(DISTINCT {text}) FROM '{p}' WHERE {text} IS NOT NULL"
    ).fetchone()[0]
    n_prompt = duckdb.sql(f"SELECT COUNT(DISTINCT {prompt}) FROM '{p}'").fetchone()[0]
    print("\n  [7] 重复与题库")
    print(f"    重复作文 {dup_text:,} ({pct(dup_text / total)})")
    print(f"    去重后题目数 {n_prompt:,}  (篇/题 = {total / max(n_prompt, 1):.1f})")
    res["dup_essays"] = dup_text
    res["unique_prompts"] = n_prompt

    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json-only", action="store_true", help="只写 JSON，不打印")
    args = ap.parse_args()

    REPORTS.mkdir(parents=True, exist_ok=True)
    results = []
    for name, spec in SPECS.items():
        try:
            results.append(analyse(name, spec))
        except Exception as exc:  # noqa: BLE001
            print(f"\n! {name} 分析失败: {exc}", file=sys.stderr)

    # 结论
    print("\n" + "=" * 66)
    print("  EDA 结论 —— 对 P0 验收线的影响")
    print("=" * 66)
    for r in results:
        name = r["dataset"]
        issues = []
        severity = 0
        if r["top_band_share"] > 0.40:
            issues.append(f"band 高度集中({pct(r['top_band_share'])})")
            severity += 5
        # 一致性过低是真噪声；一致性"过于完美"同样是红旗——真实评分不会 100%
        # 满足 |overall - mean(4维)| <= 0.25，那说明四维是从 overall 反推出来的。
        if r["consistency_within_0.5"] < 0.80:
            issues.append(f"四维一致性仅{pct(r['consistency_within_0.5'])}")
        if r["consistency_within_0.25"] > 0.99:
            issues.append("四维一致性过于完美，疑似由 overall 反推")
            severity += 5
        # 维度间强相关 = 四维不是独立评分，等于只有一个信号，四维回归失去意义
        max_corr = max((v for v in r["dim_correlation"].values() if v is not None), default=0.0)
        if max_corr > 0.85:
            issues.append(f"维度高度相关(r={max_corr:.2f})，非独立评分")
            severity += 10          # 致命：真值有效性问题
        if r["all_dims_identical_rate"] > 0.50:
            issues.append(f"维度雷同{pct(r['all_dims_identical_rate'])}")
            severity += 3
        for k, v in r["half_band_ratio"].items():
            if v is not None and v < 0.01:
                issues.append(f"{k} 无半档精度")
                severity += 1       # 轻微：只影响精度，不影响真值有效性
        if r["dup_essays"] / max(r["total"], 1) > 0.30:
            issues.append(f"重复作文{pct(r['dup_essays'] / max(r['total'], 1))}")
            severity += 2
        verdict = "；".join(issues) if issues else "暂无红旗"
        print(f"  {name:<16} 有效 {r['valid']:>6,}  |  {verdict}")
        r["issues"] = issues
        r["max_dim_corr"] = round(max_corr, 3)
        r["severity"] = severity

    # 排序按「严重性加权分低 + 样本多」。不能只看样本量，也不能只数红旗条数：
    # chillies 的 3 条红旗全是「无半档精度」（精度问题，权重 1），
    # hai2131 的「非独立评分」是真值有效性问题（权重 10）—— 两者不可同等计票。
    ranked = sorted(results, key=lambda x: (x["severity"], -x["valid"]))
    print("\n  四维回归真值可用性排序（严重性加权分低优先，其次样本多）:")
    for i, r in enumerate(ranked, 1):
        flag = "  ← 推荐主用" if i == 1 else ""
        print(f"    {i}. {r['dataset']:<16} 加权分 {r['severity']:>2}  "
              f"红旗 {len(r['issues'])}  有效 {r['valid']:>6,}  max_r {r['max_dim_corr']}{flag}")
        r["rank"] = i

    print("\n  口径提醒：")
    print("   · 带红旗的数据集，其分数只能当代理真值，代理集 QWK 验收线按 0.65 而非 0.80。")
    print("   · 维度无半档精度时，四维回归无法评到 0.5 档，应只回归 overall 或放宽到整档。")
    print("   · 分数不可用的数据集，其题目文本仍可抽作题库（题目与评分是两回事）。")

    out = REPORTS / f"eda-writing-{datetime.now(CST).strftime('%Y-%m-%d')}.json"
    out.write_text(json.dumps({"generated_at": datetime.now(CST).isoformat(timespec="seconds"),
                               "datasets": results}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n  报告: {out.relative_to(repo_root())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
