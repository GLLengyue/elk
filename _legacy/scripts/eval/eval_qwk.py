#!/usr/bin/env python3
"""
eval_qwk.py — 评分器验收：QWK + 系统偏差 + ±0.5 命中率

为什么自己实现 QWK
------------------
- scipy 不小（~30MB），本项目目前没用
- QWK 公式简单，~30 行可以写完
- 自己实现能把"每步算的是什么"打印出来，调试更直观

QWK 公式
--------
    QWK = 1 - sum(W * O) / sum(W * E)
    W[i][j] = (i - j)^2 / (k - 1)^2      # 二次权重
    E[i][j] = row_sum[i] * col_sum[j] / N
    O[i][j] = 实际 i、预测 j 的样本数
其中 k 是档位总数（0-9 步长 0.5，共 19 档）。

读入什么
--------
两份 JSONL，按 id 配对：
    1. 真值：人工评分或 chillies 已知 band（从 ground_truth 字段读）
    2. 预测：模型输出或人工二评（从 to_fill 或 score-result 字段读）

只对"两份都有的 id"算指标，未配对的会列出。

报告
----
每维一个表：
    Pearson r
    QWK
    MAE (平均绝对误差)
    ±0.5 命中率（误差 ≤ 半档 的比例）
    系统偏差（mean_pred - mean_true）
    偏差直方图（按真实档分组）

用法
----
    ./.venv/bin/python scripts/eval/eval_qwk.py \
        --truth data/eval/hand_score_template.jsonl --truth-field ground_truth \
        --pred data/eval/hand_score_filled.jsonl --pred-field to_fill

默认从 hand_score_template.jsonl 找 ground_truth 和 to_fill 字段。
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "eval"))

BANDS = [i / 2 for i in range(0, 19)]    # 0.0, 0.5, ..., 9.0
BAND_IDX = {b: i for i, b in enumerate(BANDS)}


def _band_to_idx(x: float) -> int:
    """把任意浮点 band 四舍五入到最近半档并取下标。"""
    return BAND_IDX[round(x * 2) / 2]


def qwk(y_true: list[float], y_pred: list[float]) -> tuple[float, dict]:
    """二次加权 Cohen's kappa。返回 (qwk, {level: confusion_count})。

    19 档 × 19 档的混淆矩阵全打印到 QWK 报告里，单条样本量小（< 100）时矩阵很稀。
    """
    assert len(y_true) == len(y_pred)
    n = len(y_true)
    if n == 0:
        return float("nan"), {}

    k = len(BANDS)
    O = [[0] * k for _ in range(k)]
    for t, p in zip(y_true, y_pred):
        O[_band_to_idx(t)][_band_to_idx(p)] += 1

    row_sum = [sum(O[i]) for i in range(k)]
    col_sum = [sum(O[i][j] for i in range(k)) for j in range(k)]
    W = [[((i - j) ** 2) / ((k - 1) ** 2) for j in range(k)] for i in range(k)]
    E = [[row_sum[i] * col_sum[j] / n for j in range(k)] for i in range(k)]

    num = sum(W[i][j] * O[i][j] for i in range(k) for j in range(k))
    den = sum(W[i][j] * E[i][j] for i in range(k) for j in range(k))
    if den == 0:
        return float("nan"), {}

    # 仅返回非零的对角线附近作为混淆热图的子集
    dense = {}
    for i in range(k):
        for j in range(k):
            if O[i][j] > 0:
                dense[f"{BANDS[i]:.1f}->{BANDS[j]:.1f}"] = O[i][j]
    return 1.0 - num / den, dense


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return float("nan")
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    if dx == 0 or dy == 0:
        return float("nan")
    return num / (dx * dy)


def _read_score(field_val, dim: str):
    """从 truth_field/pred_field 取 dim 对应的分数。

    两种合法结构：
        A. {TR: 5.0, CC: 6.0, overall: 5.0, ...}    # 平铺 dim->分
        B. {criteria: [{id:TR,band:5.0},...],       # 标准 score-result 形态
             overall_band: 5.875}
    """
    if not isinstance(field_val, dict):
        return None
    if dim == "overall":
        if isinstance(field_val.get("overall"), (int, float)):
            return float(field_val["overall"])
        if isinstance(field_val.get("overall_band"), (int, float)):
            return float(field_val["overall_band"])
        return None
    if isinstance(field_val.get(dim), (int, float)):
        return float(field_val[dim])
    for c in field_val.get("criteria", []) or []:
        if isinstance(c, dict) and c.get("id") == dim:
            b = c.get("band")
            return float(b) if isinstance(b, (int, float)) else None
    return None


def per_criterion(truth_docs, pred_docs, truth_field, pred_field,
                  criteria=("TR", "CC", "LR", "GRA", "overall")):
    out = {}
    for c in criteria:
        ts, ps = [], []
        for td, pd in zip(truth_docs, pred_docs):
            t = _read_score(td.get(truth_field), c)
            p = _read_score(pd.get(pred_field), c)
            if t is not None and p is not None:
                ts.append(t); ps.append(p)
        out[c] = (ts, ps)
    return out


def load_jsonl(path: Path, key: str | None = "id") -> list[dict]:
    docs = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            docs.append(json.loads(line))
        except json.JSONDecodeError as e:
            sys.exit(f"{path}:{i} JSON 解析失败: {e}")
    if key:
        miss = [d for d in docs if key not in d]
        if miss:
            print(f"[warn] {len(miss)} 条无 `{key}` 字段，跳过", file=sys.stderr)
            docs = [d for d in docs if key in d]
    return docs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--truth", required=True, help="真值 JSONL")
    ap.add_argument("--pred", required=True, help="预测 JSONL")
    ap.add_argument("--truth-field", default="ground_truth",
                    help="真值字段名（dict）或直接是数字")
    ap.add_argument("--pred-field", default="to_fill",
                    help="预测字段名（dict）或直接是数字")
    ap.add_argument("--id", default="id")
    args = ap.parse_args()

    truth = {d[args.id]: d for d in load_jsonl(Path(args.truth), args.id)}
    pred = {d[args.id]: d for d in load_jsonl(Path(args.pred), args.id)}

    common = sorted(set(truth) & set(pred))
    only_truth = sorted(set(truth) - set(pred))
    only_pred = sorted(set(pred) - set(truth))
    if only_truth:
        print(f"[warn] 真值有但预测无: {len(only_truth)} 条，如 {only_truth[:3]}")
    if only_pred:
        print(f"[warn] 预测有但真值无: {len(only_pred)} 条，如 {only_pred[:3]}")

    td = [truth[i] for i in common]
    pd = [pred[i] for i in common]
    n = len(common)
    print(f"\n配对样本: {n}")

    pairs = per_criterion(td, pd, args.truth_field, args.pred_field)

    print(f"\n{'维度':<8}{'n':<5}{'Pearson':<10}{'QWK':<8}{'MAE':<7}"
          f"{'±0.5 命中':<10}{'系统偏差':<10}{'真值均值':<10}{'预测均值':<10}")
    print("-" * 80)
    for c, (ts, ps) in pairs.items():
        if not ts:
            print(f"{c:<8}0")
            continue
        r = pearson(ts, ps)
        q, _ = qwk(ts, ps)
        mae = statistics.mean(abs(t - p) for t, p in zip(ts, ps))
        hit = sum(1 for t, p in zip(ts, ps) if abs(t - p) <= 0.5) / n
        bias = statistics.mean(ps) - statistics.mean(ts)
        print(f"{c:<8}{n:<5}{r:<10.3f}{q:<8.3f}{mae:<7.2f}{hit:<10.1%}{bias:<+10.2f}"
              f"{statistics.mean(ts):<10.2f}{statistics.mean(ps):<10.2f}")

    if n >= 1:
        for c, (ts, ps) in pairs.items():
            if not ts or c not in ("overall", "TR", "CC"):
                continue
            print(f"\n[{c}] 逐样本偏差（真值 → 预测，+ = 高估）")
            for t, p in zip(ts, ps):
                mark = "  " if abs(t - p) <= 0.5 else "* "
                print(f"  {mark}{t:>4.1f} → {p:<4.1f}  Δ={p - t:+.1f}")

    # 验收门禁（n 小时的 n 统计功效弱，给的是参考线）
    print("\n[验收参考 · n 较小时置信区间宽，不作硬性门禁]")
    overall_q, _ = qwk(*pairs.get("overall", ([], [])))
    if math.isnan(overall_q):
        print("  n 不足，QWK 无法计算")
    elif overall_q >= 0.85:
        print(f"  overall QWK={overall_q:.3f} ≥ 0.85 — 与考官高度一致")
    elif overall_q >= 0.70:
        print(f"  overall QWK={overall_q:.3f} ≥ 0.70 — 与考官中度一致（rubric 计划中目标）")
    elif overall_q >= 0.50:
        print(f"  overall QWK={overall_q:.3f} ≥ 0.50 — 中等，需排查 rubric 描述或样本问题")
    else:
        print(f"  overall QWK={overall_q:.3f} < 0.50 — 评分器与真值偏差大，需要重审")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
