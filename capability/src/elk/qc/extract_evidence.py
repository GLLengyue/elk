#!/usr/bin/env python3
"""
extract_evidence.py — 为阅读题补 evidence（答案在原文中的定位）

为什么先做算法式
----------------
evidence 是 QC 七关里 G3 的入口，没有它后面的 G4/G5 全部跑不起来。
但它需要 LLM 逐题定位，95 题成本不低。

先做**算法式**（零成本、可立即验证）的理由：
    95 题里 39 题的答案是**原文原词**（填空题），直接字符串匹配就能精确定位，
    剩下的用选项文本/stem 做词袋匹配也能定位到大致句子。
    先把能自动解决的解决掉，剩下的再决定是否值得上 LLM。

三类查询与预期置信度
--------------------
    原文词/短语  39 题  → 高置信（答案即原文词，字符串直接命中）
    字母/罗马数字 31 题 → 中置信（用正确选项的文本去匹配）
    判断题       13 题  → 低置信（只能用 stem 实词做语义匹配）
    多答案列表   12 题  → 中置信（同字母选项）

评分
----
用**加权重叠率**：query 词命中数 / query 词总数，并对"稀有词"（在原文中
出现次数少的词）给更高权重——"the" 命中没意义，"mineralisation" 命中才是信号。
这与 TF-IDF 的直觉一致，但不需要额外依赖。

输出
----
就地更新 JSON，给每题加 evidence 数组与 evidence_confidence 字段，
并把 meta.qc.evidence_missing 置为 false，同时记录 needs_review 的题号。

用法
----
    ./.venv/bin/python scripts/qc/extract_evidence.py
    ./.venv/bin/python scripts/qc/extract_evidence.py --min-score 0.30
    ./.venv/bin/python scripts/qc/extract_evidence.py --dry-run
"""

from __future__ import annotations
from elk.paths import (
    repo_root, data_root, schemas_dir, rubrics_dir, prompts_dir,
    db_path, index_path, structured_dir,
)

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path


FILES = (sorted((repo_root() / "data/structured/reading/official").glob("*.json")) +
         sorted((repo_root() / "data/structured/reading/official/taskbank").glob("*.json")))

STOP = set("""a an the and or but if of to in on at for with as by is are was were be been being
this that these those it its their them they he she we you i my our your his her from
not no do does did have has had will would can could should may might must about into
over under more most some any all each other others than then so such very much many
there here what which who whom whose when where why how also however although though
because while whereas both whether give gives given into onto upon""".split())

RE_SENT = re.compile(r"(?<=[.!?])\s+")
RE_WORD = re.compile(r"[a-z][a-z\-']*")
RE_JUDGE = re.compile(r"^(TRUE|FALSE|NOT GIVEN|YES|NO)$", re.I)
RE_LETTER = re.compile(r"^[A-H]$")
RE_ROMAN = re.compile(r"^(i{1,3}v?|iv|vi{0,3}|ix|x)$", re.I)


def norm(s: str) -> str:
    """归一化空白——schema 要求 quote 是 passage 的精确子串（归一化后）。"""
    return re.sub(r"\s+", " ", (s or "")).strip()


def tokens(text: str) -> list[str]:
    return [w for w in RE_WORD.findall((text or "").lower()) if w not in STOP and len(w) > 2]


def build_query(q: dict, group: dict) -> tuple[list[str], str, list[str]]:
    """构造查询，返回 (词表, 方法标签, 必含词)。

    **必含词是准确性的关键**。第一版只算"与查询最相似的句子"，结果
    87.4% 的题目都"找到了"证据句，但人工验证发现答案词常常不在句子里
    —— 自动抽检 answer_word 类 37 题，只有 24 题（65%）的证据句真的
    包含答案。覆盖率是个会骗人的指标。

    对填空题，答案就是原文原词，所以"证据句必须包含答案"是硬约束：
    先用它筛候选，再用相似度排序。
    """
    ans = q.get("answer")
    opts = {str(o.get("key", "")).strip(): o.get("text", "")
            for o in group.get("options", []) or []}

    if isinstance(ans, list):
        texts = [opts.get(str(a).strip(), "") for a in ans]
        return tokens(" ".join(texts) + " " + q.get("stem", "")), "option_list", []

    a = str(ans or "").strip()
    if RE_JUDGE.match(a):
        return tokens(q.get("stem", "")), "stem_only", []
    if RE_LETTER.match(a) or RE_ROMAN.match(a):
        t = opts.get(a, "")
        if t:
            return tokens(t + " " + q.get("stem", "")[:80]), "option_text", []
        return tokens(q.get("stem", "")), "stem_only", []
    # 原文词/短语：答案必须出现在证据句中
    must = [w for w in RE_WORD.findall(a.lower()) if len(w) > 2]
    return tokens(a + " " + q.get("stem", "")), "answer_word", must


def score_sentence(q_tokens: list[str], sent_tokens: set[str],
                   idf: dict[str, float]) -> float:
    """加权重叠率：命中词的 IDF 之和 / 查询词 IDF 之和。"""
    if not q_tokens:
        return 0.0
    hit = sum(idf.get(w, 1.0) for w in set(q_tokens) if w in sent_tokens)
    total = sum(idf.get(w, 1.0) for w in set(q_tokens))
    return hit / total if total else 0.0


def locate(paras: list[dict], q_tokens: list[str], min_score: float,
           must_include: list[str] | None = None):
    """在 passage 中定位证据句，返回 evidence dict 或 None。

    must_include：若给出，候选句必须包含其中**任一**词的原形或 4 字符词干前缀
    （填空题的答案是原文原词，这条约束把准确率从 65% 拉到 ~100%）。
    """
    if not q_tokens:
        return None
    must = [w.lower() for w in (must_include or []) if len(w) > 2]

    # 全文与 IDF
    full = norm(" ".join(p["text"] for p in paras))
    all_tokens = tokens(full)
    n_sents = 0
    df: Counter = Counter()
    sent_index: list[tuple[int, int, str, str]] = []   # (start, end, label, text)
    pos = 0
    for p in paras:
        ptext = norm(p["text"])
        if not ptext:
            continue
        base = full.find(ptext, 0)
        if base < 0:
            base = pos
        for s in RE_SENT.split(ptext):
            s = norm(s)
            if not s:
                continue
            st = full.find(s, base)
            if st < 0:
                st = base
            en = st + len(s)
            sent_index.append((st, en, p["label"], s))
            df.update(set(tokens(s)))
            n_sents += 1
            base = en
        pos = base

    n_docs = max(n_sents, 1)
    idf = {w: math.log(n_docs / (1 + c)) + 1.0 for w, c in df.items()}

    # 按约束强度分档收集候选：全部必含词命中 > 任一命中 > 仅相似度。
    # 分档的原因：query 里混了 stem 的十几个词，答案只占 1-2 个，
    # 加权重叠率会被稀释到 0.25 以下而被误过滤（实测 glucose/free/temperate
    # 都在正文里，却因 score 过低返回 None）。
    tiers: list[list[tuple]] = [[], [], []]

    for i, (st, en, label, text) in enumerate(sent_index):
        low = text.lower()
        tier = 2
        if must:
            stems = {w[:4] for w in RE_WORD.findall(low)}
            hits = [w for w in must
                    if w in low or (len(w) >= 4 and w[:4] in stems)]
            if len(hits) == len(must):
                tier = 0
            elif hits:
                tier = 1
            else:
                continue
        stoks = set(tokens(text))
        sc = score_sentence(q_tokens, stoks, idf)
        # 相邻句合并窗口：答案常跨两句
        if i + 1 < len(sent_index):
            nxt = sent_index[i + 1]
            if nxt[2] == label:
                combo = set(stoks) | set(tokens(nxt[3]))
                sc2 = score_sentence(q_tokens, combo, idf)
                if sc2 > sc:
                    sc, en, text = sc2, nxt[1], norm(text + " " + nxt[3])
        tiers[tier].append((sc, st, en, label, text))

    # 优先取最高档；只有最低档（纯相似度）才适用 min_score 门槛
    best = None
    for tier_idx, cand in enumerate(tiers):
        if not cand:
            continue
        best = max(cand)
        if tier_idx < 2:
            break                      # 硬约束命中，不再看 min_score
        if best[0] < min_score:
            best = None
        break

    if not best:
        return None
    sc, st, en, label, text = best
    return {
        "quote": text[:300],
        "start": st,
        "end": en,
        "paragraph_label": label,
        "is_core": True,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-score", type=float, default=0.25,
                    help="最低接受分数，低于此值标记为待复核")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    stats = Counter()
    review: list[str] = []
    for f in FILES:
        d = json.loads(f.read_text(encoding="utf-8"))
        paras = d["passage"]["paragraphs"]
        changed = False

        for g in d["question_groups"]:
            for q in g["questions"]:
                q_tokens, method, must = build_query(q, g)
                ev = locate(paras, q_tokens, args.min_score, must)
                stats[method] += 1
                if ev:
                    # 只写 schema 里定义过的字段；置信度留给 QC 阶段在 meta.qc 里记
                    q["evidence"] = [ev]
                    changed = True
                    stats[f"{method}_hit"] += 1
                else:
                    review.append(f"{d['id']} Q{q['number']} ({method})")

        if changed and not args.dry_run:
            d["meta"]["qc"]["evidence_missing"] = False
            d["meta"]["qc"]["evidence_needs_review"] = sorted(
                {r.split()[1] for r in review if r.startswith(d["id"])})
            f.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    total = sum(v for k, v in stats.items() if not k.endswith("_hit"))
    hit = sum(v for k, v in stats.items() if k.endswith("_hit"))
    print(f"题目总数 {total}  定位成功 {hit}  覆盖率 {hit/max(total,1):.1%}")
    print()
    print(f"{'方法':<16}{'题数':<7}{'成功':<7}{'成功率'}")
    print("-" * 46)
    for m in ("answer_word", "option_text", "option_list", "stem_only"):
        n = stats.get(m, 0)
        h = stats.get(f"{m}_hit", 0)
        if n:
            print(f"  {m:<14}{n:<7}{h:<7}{h/n:.0%}")
    if review:
        print(f"\n待复核 {len(review)} 题（前 12）:")
        for r in review[:12]:
            print(f"  · {r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
