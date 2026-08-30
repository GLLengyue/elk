#!/usr/bin/env python3
"""
audit_writing_alignment.py — 写作数据集「题目-作文对齐」与「四维独立性」审计

为什么需要这个脚本
------------------
EDA 只能告诉我们 band 怎么分布，无法回答两个会直接决定"评分器能不能建"的问题：

  Q1. prompt 与 essay 是否配对？
      如果错位，Task Response 维度没有任何真值可言（TR 完全依赖题目）。
      错位不能用"抽一条看看"判定 —— 单样本会以偏概全。本脚本用
      「题目核心概念词在作文中的命中率」做统计判定。

  Q2. 四个维度的分数是独立标注的，还是从 overall 反推的？
      如果是反推的，四维回归就是在拟合一个恒等式，QWK 再高也是假的。
      判据有三条，任意一条命中即判为"非独立"：
        (a) 维度间 Pearson r 过高
        (b) 两维取值完全相同的比例异常高
        (c) overall 能被四维均值的高精度还原（合成规则的痕迹）

用法
----
    ./.venv/bin/python scripts/eval/audit_writing_alignment.py
    ./.venv/bin/python scripts/eval/audit_writing_alignment.py --sample 2000

输出
----
    data/eval/reports/audit-writing-<YYYY-MM-DD>.json

依赖：duckdb（不需要 numpy / pandas）
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------- 停用词
# 只用于"核心概念词"提取：题目要求里高频出现但不承载主题的词必须剔掉，
# 否则任何两篇英文文本都会因为这些词而显得"相关"。
STOP = set(
    """a an the and or but if of to in on at for with as by is are was were be been being
this that these those it its their them they he she we you i my our your his her from
not no do does did have has had will would can could should may might must about into
over under more most some any all each other others than then so such very much many
there here what which who whom whose when where why how also however although though
because while whereas both whether give gives given own opinion essay discuss views view
people person thing things today nowadays some think thinks agree disagree extent
""".split()
)


def content_tokens(text: str) -> list[str]:
    """实词 token（>=4 字母，去停用词）。"""
    return [w for w in re.findall(r"[a-z]{4,}", (text or "").lower()) if w not in STOP]


def concept_hit_rate(prompt: str, essay: str) -> float | None:
    """
    题目核心概念词在作文中的命中率。

    用 5 字符前缀做词干化匹配，容忍 progress/progressive、environment/environmental
    这类屈折变化 —— 雅思作文普遍做同义替换，精确匹配会严重低估对齐度。
    """
    q = content_tokens(prompt)
    if not q:
        return None
    stems = {w[:5] for w in content_tokens(essay)}
    return sum(1 for w in q if w[:5] in stems) / len(q)


def ielts_round(x: float) -> float:
    """官方取整规则的近似：最近半档。"""
    return round(x * 2) / 2


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


# ---------------------------------------------------------------- 数据集定义
DATASETS = [
    {
        "key": "chillies_task2",
        "task": 2,
        "glob": "data/raw/writing/chillies_task2/data/*.parquet",
        "prompt_col": "question",   # 完整题目
        "essay_col": "essay_text",
        "dims": {
            "TR": "task_achievement_band",
            "CC": "coherence_cohesion_band",
            "LR": "lexical_resource_band",
            "GRA": "grammatical_range_band",
        },
        "overall_col": "overall_band",
    },
    {
        "key": "btnotpt_task2",
        "task": 2,
        "glob": "data/raw/writing/btnotpt_task2/data/*.parquet",
        "prompt_col": "topic",      # 该集 topic 存的是完整题目，非主题标签
        "essay_col": "essay",
        "dims": {"TR": "TR", "CC": "CC", "LR": "LR", "GRA": "GRA"},
        "overall_col": "Overall",
    },
    {
        "key": "hai2131_task1",
        "task": 1,
        "glob": "data/raw/writing/hai2131_task1/data/*.parquet",
        "prompt_col": "topic",      # 注意：该集 topic 只是图表类型("Table")，非题目
        "essay_col": "content",
        # 分数列是 VARCHAR，需 CAST
        "dims": {
            "TR": "CAST(task_response_score AS DOUBLE)",
            "CC": "CAST(coherence_cohesion_score AS DOUBLE)",
            "LR": "CAST(lexical_resource_score AS DOUBLE)",
            "GRA": "CAST(grammatical_range_accuracy_score AS DOUBLE)",
        },
        "overall_col": "CAST(overall_band_score AS DOUBLE)",
        "prompt_is_label": True,    # prompt 列无题目语义，跳过对齐判定
    },
]


def audit(ds: dict, sample: int | None) -> dict:
    glob = str(ROOT / ds["glob"])
    limit = f"using sample {sample} rows" if sample else ""

    pc, ec = ds["prompt_col"], ds["essay_col"]
    dims = ds["dims"]
    oc = ds["overall_col"]

    cols = ", ".join([f'"{pc}" AS p', f'"{ec}" AS e'] +
                     [f'{expr} AS "{k}"' for k, expr in dims.items()] +
                     [f"{oc} AS overall"])
    sql = f"select {cols} from read_parquet('{glob}') where {ec} is not null {limit}"
    rows = duckdb.sql(sql).fetchall()

    out: dict = {
        "dataset": ds["key"],
        "task": ds["task"],
        "n_sampled": len(rows),
    }

    # ---------------- Q1 题目-作文对齐 ----------------
    if not ds.get("prompt_is_label"):
        hits = [h for h in (concept_hit_rate(r[0], r[1]) for r in rows) if h is not None]
        hits.sort()
        n = len(hits)
        out["alignment"] = {
            "method": "题目实词在作文中的 5-char 词干命中率",
            "mean": round(statistics.mean(hits), 4),
            "p10": round(hits[n // 10], 4),
            "median": round(statistics.median(hits), 4),
            "zero_hit_rate": round(sum(1 for h in hits if h < 0.01) / n, 4),
            "below_20pct_rate": round(sum(1 for h in hits if h < 0.20) / n, 4),
        }
    else:
        out["alignment"] = {
            "skipped": True,
            "reason": f"prompt 列 `{pc}` 是图表类型标签而非题目，对齐判定不适用",
            "prompt_sample": rows[0][0][:80] if rows else None,
        }

    # ---------------- Q2 四维独立性 ----------------
    keys = list(dims.keys())
    col_of = {k: 2 + i for i, k in enumerate(keys)}   # 0=p, 1=e
    vals = {k: [r[col_of[k]] for r in rows if r[col_of[k]] is not None] for k in keys}
    overall = [r[-1] for r in rows if r[-1] is not None]

    # --- Q2b 单维度分布形态 ---
    # 【2026-08-29 补】第一版漏掉的判据，也是最致命的一条。
    # 相关性低只说明"TR 与 CC 不耦合"，但**两者可能各自都是坏的**。
    # chillies 的 r(TR,CC)=0.489 看着健康，可 TR 单独看有 15% 是满分 9.0，
    # CC 从 8.0 直接跳到 9.0（中间没有 8.5）—— 真实考官评分不会长这样。
    # 所以必须单独检查每个维度的**分布形态**，不能只看维度之间的关系。
    shape = {}
    # overall 也纳入检查：它是"最后一根稻草"——四维都坏时，
    # 若 overall 形态健康，仍可用于排序一致性（而非绝对一致性）检验。
    for k in list(keys) + ["overall"]:
        v = vals[k] if k in vals else overall
        if not v:
            continue
        n = len(v)
        # 【2026-08-29 修 bug】半档的正确判据是 x != round(x)（即 6.5 而非 6.0）。
        # 之前写成 abs(x*2 - round(x*2)) > 1e-9，对 6.5（6.5*2=13）和 6.0（12）
        # 结果都是 0，导致半档占比恒为 0，把 chillies overall（真实 48.4%）
        # 也误判成"无半档"。教训：**写完统计判据先用手算样例验证一遍**。
        half = sum(1 for x in v if abs(x - round(x)) > 1e-9)
        top9 = sum(1 for x in v if x >= 9.0)
        top8 = sum(1 for x in v if x >= 8.0)
        c80 = sum(1 for x in v if abs(x - 8.0) < 1e-9)
        c85 = sum(1 for x in v if abs(x - 8.5) < 1e-9)
        issues_k = []
        if top9 / n > 0.05:
            issues_k.append(f"满分占比 {top9/n:.1%}（真实应 <1%）")
        if top8 / n > 0.25:
            issues_k.append(f"8分以上占比 {top8/n:.1%}（真实应 <10%）")
        if half / n < 0.05:
            issues_k.append(f"半档占比 {half/n:.1%}（真实应 30-50%，通常是整数列导致）")
        if c80 > 0 and c85 == 0 and top9 / n > 0.02:
            issues_k.append("8.0 与 9.0 之间无 8.5，分布断裂")
        shape[k] = {
            "n": n,
            "half_band_ratio": round(half / n, 4),
            "band9_ratio": round(top9 / n, 4),
            "ge8_ratio": round(top8 / n, 4),
            "count_8_0": c80,
            "count_8_5": c85,
            "issues": issues_k,
        }
    out["distribution_shape"] = shape

    corr = {}
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            na = min(len(vals[a]), len(vals[b]))
            if na < 3:
                continue
            r = pearson(vals[a][:na], vals[b][:na])
            if r is not None:
                corr[f"{a}-{b}"] = round(r, 3)

    # 两两取值完全相同的比例 —— 独立评分不该大面积重合
    identical = {}
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            na = min(len(vals[a]), len(vals[b]))
            if na == 0:
                continue
            identical[f"{a}=={b}"] = round(
                sum(1 for x, y in zip(vals[a][:na], vals[b][:na]) if x == y) / na, 4
            )

    # 差值分布的正负对称性 —— 同源构造会留下明显的方向偏态
    skew = {}
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            na = min(len(vals[a]), len(vals[b]))
            if na == 0:
                continue
            d = [round(y - x, 2) for x, y in zip(vals[a][:na], vals[b][:na])]
            pos = sum(1 for v in d if v > 0)
            neg = sum(1 for v in d if v < 0)
            skew[f"{b}-{a}"] = {
                "equal": round(sum(1 for v in d if v == 0) / na, 4),
                "pos": round(pos / na, 4),
                "neg": round(neg / na, 4),
                "asymmetry": round(pos / neg, 2) if neg else None,
            }

    # overall 被四维均值还原的比例
    exact = recoverable = 0
    m = 0
    for r in rows:
        vs = [r[col_of[k]] for k in keys]
        o = r[-1]
        if any(v is None for v in vs) or o is None:
            continue
        m += 1
        if abs(o - ielts_round(sum(vs) / len(vs))) < 1e-9:
            exact += 1
            recoverable += 1
            continue
        # 允许任一维/两维补 0.5（模拟"半档被截断成整数列"的存储损失）
        found = any(
            abs(o - ielts_round(sum(v + d for v, d in zip(vs, combo)) / len(vs))) < 1e-9
            for combo in _half_combos(len(vs))
        )
        if found:
            recoverable += 1

    out["dimension_independence"] = {
        "pairwise_correlation": corr,
        "max_correlation": max(corr.values()) if corr else None,
        "identical_rate": identical,
        "diff_asymmetry": skew,
        "overall_reconstruction": {
            "n": m,
            "exact_from_mean": round(exact / m, 4) if m else None,
            "recoverable_with_half_band": round(recoverable / m, 4) if m else None,
            "note": "还原率高 => overall 由四维合成，四维回归存在恒等式风险",
        },
    }

    out["overall_band_half_ratio"] = (
        round(sum(1 for v in overall if abs(v * 2 - round(v * 2)) > 1e-9) / len(overall), 4)
        if overall else None
    )

    # ---------------- 判决 ----------------
    verdict, usable_dims, reasons = _judge(out, keys)
    out["verdict"] = {"level": verdict, "usable_dimensions": usable_dims, "reasons": reasons}
    return out


def _half_combos(n: int):
    """生成所有"至少一维补 0.5"的增量组合（不含全 0）。

    用于模拟"半档分数被截断存成整数列"造成的存储损失：
    若允许补档后就能还原 overall，说明该维的真实半档信息已丢失。
    """
    for j in range(n):
        for k in range(j, n):
            c = [0.0] * n
            c[j] += 0.5
            if k != j:
                c[k] += 0.5
            yield c


def _judge(out: dict, keys: list[str]) -> tuple[str, list[str], list[str]]:
    reasons: list[str] = []
    ind = out["dimension_independence"]
    maxc = ind["max_correlation"] or 0.0
    reco = ind["overall_reconstruction"]["recoverable_with_half_band"] or 0.0

    # 找出"病态耦合"的维度对：极高相关 或 极高同值率 或 极不对称
    sick: set[str] = set()
    for pair, r in ind["pairwise_correlation"].items():
        if r >= 0.80:
            sick.update(pair.split("-"))
            reasons.append(f"{pair} 相关性过高 r={r}")
    for pair, p in ind["identical_rate"].items():
        if p >= 0.50:
            sick.update(pair.split("=="))
            reasons.append(f"{pair} 取值完全相同的比例达 {p:.1%}，非独立评分特征")
    for pair, s in ind["diff_asymmetry"].items():
        a = s["asymmetry"]
        if a is not None and a >= 3.0 and s["equal"] >= 0.40:
            sick.update(pair.split("-"))
            reasons.append(f"{pair} 差值正负比 {a:.1f}:1，存在构造性方向偏态")

    if reco >= 0.98:
        reasons.append(f"overall 可被四维均值还原 {reco:.1%}，四维疑似由 overall 反推")

    # 分布形态：单维度检查（判不出耦合，但能抓出"这一维本身是坏的"）
    # overall 单独处理：它即使有问题也只是"不可用"，不该被塞进 sick（sick 是维度集）
    for k, s in out.get("distribution_shape", {}).items():
        if k == "overall" or not s["issues"]:
            continue
        sick.add(k)
        for iss in s["issues"]:
            reasons.append(f"{k} 分布形态异常：{iss}")

    ov_shape = out.get("distribution_shape", {}).get("overall")
    if ov_shape and ov_shape["issues"]:
        out["overall_usable_for_ranking"] = False
        for iss in ov_shape["issues"]:
            reasons.append(f"overall 分布形态异常：{iss}（连排序一致性都不可用）")
    elif ov_shape:
        out["overall_usable_for_ranking"] = True
        reasons.append(
            f"overall 分布形态健康（半档 {ov_shape['half_band_ratio']:.0%}、"
            f"满分 {ov_shape['band9_ratio']:.1%}）—— 四维不可用，"
            f"但可用于**排序一致性**检验，不能用于绝对一致性")

    usable = [k for k in keys if k not in sick]
    aligned = out.get("alignment", {})
    if aligned.get("zero_hit_rate") is not None and aligned["zero_hit_rate"] > 0.10:
        reasons.append(f"题目-作文零命中率 {aligned['zero_hit_rate']:.1%}，配对不可靠")
        usable = []

    if not usable:
        level = "UNUSABLE"
    elif len(usable) < len(keys):
        level = "PARTIAL"
    elif maxc >= 0.75 or reco >= 0.98:
        level = "SUSPECT"
    else:
        level = "USABLE"
        reasons.append("未发现非独立性证据")
    return level, usable, reasons


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=None, help="每个数据集抽样行数；不传则全量")
    args = ap.parse_args()

    results = [audit(ds, args.sample) for ds in DATASETS]
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    out_path = ROOT / "data/eval/reports" / f"audit-writing-{stamp}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                    "sampled": args.sample, "datasets": results},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"{'dataset':<18}{'task':<6}{'verdict':<10}{'可用维度':<22}还原率")
    print("-" * 78)
    for r in results:
        ind = r["dimension_independence"]
        print(f"{r['dataset']:<18}{r['task']:<6}{r['verdict']['level']:<10}"
              f"{','.join(r['verdict']['usable_dimensions']) or '—':<22}"
              f"{ind['overall_reconstruction']['recoverable_with_half_band']:.1%}")
        for reason in r["verdict"]["reasons"]:
            print(f"    · {reason}")
    print(f"\n→ {out_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
