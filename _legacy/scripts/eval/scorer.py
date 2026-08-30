#!/usr/bin/env python3
"""
scorer.py — 写作 Task 2 评分器（客观锚点计算 + prompt 编译 + 输出校验）

职责边界
--------
本文件**不做 LLM 调用**。原因是后端选择（OpenAI / Anthropic / 本地 vLLM / 手工）
尚未确定，而下面这三块与后端无关，可以先把它们做扎实：

    1. compute_features()  —— 客观锚点，纯本地计算
    2. compile_prompt()    —— 把 rubric YAML 编译成评分 prompt
    3. validate_output()   —— 校验 LLM 输出是否符合契约

LLM 调用留 `backend` 接口，选定后接上即可。这样避免"为了跑通一条链路
而在环境安装和 API key 上耗掉半天"——用户的时间预算是硬约束。

为什么客观锚点要单独算
----------------------
LLM 不擅长精确计数，同一篇多次询问结果不稳定。把词数、段落数、
与题目的 n-gram 重叠率、TTR 等**先算好注入 prompt**，让 LLM 只做判断不做统计，
是提升评分一致性（QWK）性价比最高的一步。

用法
----
    from scorer import load_rubric, compute_features, compile_prompt

    rubric = load_rubric("rubrics/writing-task2.v1.yaml")
    feats = compute_features(prompt_text, essay_text)
    prompt = compile_prompt(rubric, prompt_text, essay_text, feats)
    print(prompt)

    # 命令行：只对一篇作文编译 prompt，不调用模型
    ./.venv/bin/python scripts/eval/scorer.py --demo
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DEFAULT_RUBRIC = ROOT / "rubrics" / "writing-task2.v1.yaml"

# --------------------------------------------------------------------------- 文本工具
_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")
_SENT = re.compile(r"(?<=[.!?])\s+")
_BULLET_NOISE = re.compile(r"^\s*[-•*·]\s*", re.M)


def _words(text: str) -> list[str]:
    return _WORD.findall(text or "")


def _normalize_for_ngram(text: str) -> list[str]:
    """n-gram 比对用的归一化：小写 + 去标点。"""
    t = _BULLET_NOISE.sub("", text or "").lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return t.split()


def _ngrams(tokens: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


def longest_common_span(a: list[str], b: list[str]) -> tuple[int, int]:
    """两个 token 序列的最长公共连续片段，返回 (长度, 在 b 中的起始下标)。

    为什么需要它：n-gram 比率会被长文稀释。
    实测一篇 204 词作文整句照抄题目（20 词），8-gram 重叠率只有 4.55% ——
    因为分母是全文所有 8-gram，抄一句根本拉不动比率。
    而官方判据说的是 "the first five lines are directly copied rubric"，
    判的是**连续片段**，不是比率。所以直接算最长连续抄袭串才对得上。

    复杂度 O(len(a) * len(b))；题目约 60 词、作文约 300-700 词，
    量级在几万次比较，纯 Python 也是毫秒级，不需要上 numpy。
    """
    if not a or not b:
        return 0, -1
    prev = [0] * (len(b) + 1)
    best_len, best_pos = 0, -1
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                v = prev[j - 1] + 1
                cur[j] = v
                if v > best_len:
                    best_len, best_pos = v, j - v
        prev = cur
    return best_len, best_pos


# --------------------------------------------------------------------------- 客观锚点
# 题目实词判定用的停用词：这些词在任何两篇英文里都会共现，
# 留着会让 concept_hit 虚高，掩盖真正的离题。
_CONCEPT_STOP = set(
    """a an the and or but if of to in on at for with as by is are was were be been being
this that these those it its their them they he she we you i my our your his her from
not no do does did have has had will would can could should may might must about into
over under more most some any all each other others than then so such very much many
there here what which who whom whose when where why how also however although though
because while whereas both whether give gives given own opinion essay discuss views view
people person thing things today nowadays some think thinks agree disagree extent
""".split()
)


def concept_hit_rate(prompt_text: str, essay_text: str) -> float | None:
    """题目核心概念词在作文中的命中率。

    用 5 字符前缀做粗词干匹配，容忍 progress/progressive 这类屈折变化 ——
    雅思考生普遍做 paraphrase，精确匹配会严重低估相关性，把正常作文误判成离题。
    """
    q = [w for w in _WORD.findall((prompt_text or "").lower())
         if len(w) >= 4 and w not in _CONCEPT_STOP]
    if not q:
        return None
    stems = {w.lower()[:5] for w in _WORD.findall(essay_text or "")}
    return round(sum(1 for w in q if w.lower()[:5] in stems) / len(q), 4)


def compute_features(prompt_text: str, essay_text: str, ngram: int = 8) -> dict:
    """计算 rubric 里 objective_anchors 一节声明的全部客观量。

    rubric_overlap 是最重要的一项：它直接驱动 TR 的"抄题不给分"封顶规则。
    用 8-gram 而不是 4-gram，是因为短 n-gram 会命中大量通用搭配
    （如 "the number of people who"），把正常 paraphrase 误判成抄题。
    """
    words = _words(essay_text)
    wc = len(words)

    # 段落：优先按空行切，退化为首行缩进
    paragraphs = [p for p in re.split(r"\n\s*\n", (essay_text or "").strip()) if p.strip()]
    if len(paragraphs) == 1:
        paragraphs = [p for p in re.split(r"\n(?=\s{2,}|(?:\s*)\t)", essay_text) if p.strip()]

    sentences = [s for s in _SENT.split((essay_text or "").strip()) if s.strip()]
    sc = max(len(sentences), 1)

    # 与题目的 n-gram 重叠率（检测**分散**抄袭）
    e_tok = _normalize_for_ngram(essay_text)
    p_tok = _normalize_for_ngram(prompt_text)
    overlap = 0.0
    e_ng = _ngrams(e_tok, ngram)
    if e_ng and len(p_tok) >= ngram:
        p_ng = _ngrams(p_tok, ngram)
        overlap = len(e_ng & p_ng) / len(e_ng)

    # 与题目的最长连续相同片段（检测**整段**照抄）
    span_len, span_pos = longest_common_span(e_tok, p_tok)

    # 词干化的 TTR（粗词干：截 6 字符，够用且无需额外依赖）
    stems = [w.lower()[:6] for w in words]
    ttr = len(set(stems)) / len(stems) if stems else 0.0

    return {
        "word_count": wc,
        "paragraph_count": max(len(paragraphs), 1),
        "sentence_count": max(len(sentences), 1),
        "avg_sentence_words": round(wc / sc, 1),
        "rubric_overlap": round(overlap, 4),
        "max_copied_span": span_len,
        "copied_span_share": round(span_len / wc, 4) if wc else 0.0,
        "concept_hit": concept_hit_rate(prompt_text, essay_text),
        "type_token_ratio": round(ttr, 4),
    }


_COND = re.compile(r"^\s*(?P<key>[a-z_]+)\s*(?P<op>>=|<=|>|<)\s*(?P<val>[\d.]+)(?P<pct>%?)\s*$")


def _eval_condition(cond: str, features: dict) -> bool | None:
    """求值一条封顶条件；无法解析或缺少特征时返回 None（表示'跳过'而非'不触发'）。

    显式区分"没触发"和"没算出来"很重要：
    后者如果按 False 处理，会让一条本该生效的封顶被静默跳过。
    """
    m = _COND.match(cond or "")
    if not m:
        return None
    key = m.group("key")
    if key not in features or features[key] is None:
        return None
    val = float(m.group("val"))
    if m.group("pct"):
        val /= 100
    left = float(features[key])
    op = m.group("op")
    return {">=": left >= val, "<=": left <= val,
            ">": left > val, "<": left < val}[op]


def apply_cap_rules(rubric: dict, features: dict, bands: dict[str, float]) -> dict:
    """执行 rubric.half_band_rule.cap_rules 的封顶逻辑。

    封顶是**独立于 LLM 判断**的硬约束：即便模型给了高分，
    触发条件就压到上限。这样避免"模型觉得写得好就给 7 分，
    但其实有大段是从题目抄的"这类系统性错误。
    """
    applied, skipped = [], []
    for rule in rubric.get("half_band_rule", {}).get("cap_rules", []):
        rid = rule["id"]
        hit = _eval_condition(rule["condition"], features)
        if hit is None:
            skipped.append(f"{rid}: 条件无法求值（{rule['condition']}）")
            continue

        if hit:
            cap_band = float(rule["effect"].split()[-1])
            target = rule["effect"].split()[0]      # 形如 "TR 上限 5.0"
            if target in bands and bands[target] > cap_band:
                applied.append({"rule": rid, "criterion": target,
                                "from": bands[target], "to": cap_band,
                                "reason": rule.get("rationale", "")})
                bands[target] = cap_band
    return {"bands": bands, "applied_caps": applied, "skipped_rules": skipped}


# --------------------------------------------------------------------------- Prompt 编译
def load_rubric(path: str | Path = DEFAULT_RUBRIC) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def compile_prompt(rubric: dict, prompt_text: str, essay_text: str,
                   features: dict | None = None) -> str:
    """把 rubric YAML 编译成评分 prompt。

    编译而非硬编码的意义：rubric 改一版，prompt 自动跟着变，
    杜绝"rubric 更新了但 prompt 还是旧的"这种最难查的一致性问题。
    """
    feats = features if features is not None else compute_features(prompt_text, essay_text)

    lines: list[str] = []
    lines.append(f"You are scoring an IELTS Writing Task 2 response.")
    lines.append(f"Rubric version: {rubric['rubric_version']}  "
                 f"(you MUST echo this exact string in your output).")
    lines.append("")
    lines.append("## Pre-computed objective measurements")
    lines.append("These are already calculated for you. Do NOT recount — use them directly.")
    for a in rubric.get("objective_anchors", []):
        val = feats.get(a["id"])
        if val is not None:
            lines.append(f"- {a['id']}: {val}")
    lines.append("")
    lines.append("## Scoring criteria")
    lines.append("For EACH criterion, find the highest band whose observable checks are ALL met,")
    lines.append("then apply the half-band rule below.")
    lines.append("")

    for c in rubric["criteria"]:
        lines.append(f"### {c['id']} — {c['name']}")
        lines.append(f"Question to answer: {c['question']}")
        for band in sorted(c["bands"].keys(), reverse=True):
            b = c["bands"][band]
            lines.append(f"  Band {band}: {b['summary']}")
            for obs in b["observable"]:
                lines.append(f"    - {obs}")
        lines.append("")

    hb = rubric.get("half_band_rule", {})
    lines.append("## Half-band rule")
    lines.append(hb.get("logic", "").strip())
    lines.append("")
    lines.append("## Hard caps (applied regardless of your judgement)")
    for r in hb.get("cap_rules", []):
        lines.append(f"- {r['id']}: if {r['condition']} -> {r['effect']}")
    lines.append("")

    lines.append("## Task prompt")
    lines.append(prompt_text.strip())
    lines.append("")
    lines.append("## Candidate response")
    lines.append(essay_text.strip())
    lines.append("")

    oc = rubric.get("output_contract", {})
    lines.append("## Output")
    lines.append("Return ONLY a JSON object, no prose, matching this shape:")
    lines.append(json.dumps({
        "rubric_version": rubric["rubric_version"],
        "criteria": [
            {"id": cid, "band": 0.0, "matched_anchor": "...", "rationale": "..."}
            for cid in rubric["criteria_ids"]
        ],
        "overall_band": 0.0,
    }, indent=2))
    lines.append("")
    lines.append("Constraints:")
    for k, v in oc.get("field_notes", {}).items():
        lines.append(f"- {k}: {v}")
    for f in oc.get("forbidden", []):
        lines.append(f"- {f}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- 输出校验
def validate_output(obj: dict, rubric: dict | None = None) -> list[str]:
    """校验 LLM 输出是否符合 rubric 的 output_contract。

    返回问题列表；空列表表示通过。
    这一步不能省：LLM 输出偶发缺字段、band 给成 7.3、
    matched_anchor 写空字符串 —— 这些都会让回归结果失真。
    """
    rubric = rubric or load_rubric()
    errs: list[str] = []

    if obj.get("rubric_version") != rubric["rubric_version"]:
        errs.append(f"rubric_version 不符: 期望 {rubric['rubric_version']}, "
                    f"实得 {obj.get('rubric_version')!r}")

    crit = obj.get("criteria")
    if not isinstance(crit, list):
        errs.append("criteria 缺失或不是数组")
        return errs

    got = {c.get("id"): c for c in crit if isinstance(c, dict)}
    for cid in rubric["criteria_ids"]:
        if cid not in got:
            errs.append(f"缺少维度 {cid}")
            continue
        c = got[cid]
        band = c.get("band")
        if not isinstance(band, (int, float)):
            errs.append(f"{cid}.band 不是数字: {band!r}")
        else:
            if not (0.0 <= band <= 9.0):
                errs.append(f"{cid}.band 越界: {band}")
            if abs(band * 2 - round(band * 2)) > 1e-9:
                errs.append(f"{cid}.band 不是 0.5 步长: {band}")
        if not (c.get("matched_anchor") or "").strip():
            errs.append(f"{cid}.matched_anchor 为空 —— 无证据支撑的分数视为无效")
        if not (c.get("rationale") or "").strip():
            errs.append(f"{cid}.rationale 为空")

    overall = obj.get("overall_band")
    if not isinstance(overall, (int, float)):
        errs.append(f"overall_band 不是数字: {overall!r}")
    elif abs(overall * 2 - round(overall * 2)) > 1e-9:
        errs.append(f"overall_band 不是 0.5 步长: {overall}")

    # 交叉校验：overall 是否等于四维均值按规则取整
    vals = [got[c]["band"] for c in rubric["criteria_ids"]
            if c in got and isinstance(got[c].get("band"), (int, float))]
    if len(vals) == 4 and isinstance(overall, (int, float)):
        expect = round(sum(vals) / 4 * 2) / 2
        if abs(overall - expect) > 1e-9:
            errs.append(f"overall_band={overall} 与四维均值取整 {expect} 不符")
    return errs


# --------------------------------------------------------------------------- CLI
def _demo() -> int:
    rubric = load_rubric()
    sample_prompt = (
        "Some people think that governments should spend money on public transport "
        "rather than building new roads. To what extent do you agree or disagree?"
    )
    sample_essay = (
        "In many cities, traffic congestion has become a serious problem. Some people "
        "think that governments should spend money on public transport rather than "
        "building new roads. I largely agree with this view, although new roads can "
        "still be necessary in certain situations.\n\n"
        "The main reason is that public transport moves far more people per vehicle. "
        "A single bus lane can carry as many commuters as three lanes of cars, which "
        "means the same road space delivers much greater capacity. Cities that "
        "invested heavily in metro systems, such as Seoul and Shanghai, have seen "
        "measurable drops in journey times even as their populations grew.\n\n"
        "Moreover, expanding road networks often induces additional demand. When new "
        "lanes are built, people who previously travelled off-peak or by other modes "
        "switch to driving, so congestion returns within a few years. Public "
        "transport, by contrast, offers a structural alternative rather than "
        "temporary relief.\n\n"
        "However, new roads remain useful where public transport is impractical, for "
        "instance in rural areas with low population density. In such places, a bus "
        "service would be neither frequent nor affordable.\n\n"
        "In conclusion, I agree that public spending should prioritise public "
        "transport in urban areas, while accepting that road building has a limited "
        "but real role elsewhere."
    )
    feats = compute_features(sample_prompt, sample_essay)
    print(json.dumps(feats, indent=2))
    print()
    print("---- compiled prompt ----")
    p = compile_prompt(rubric, sample_prompt, sample_essay, feats)
    print(p)
    print(f"\n[prompt 长度 {len(p)} 字符]")

    print("\n---- validate_output 冒烟 ----")
    good = {"rubric_version": rubric["rubric_version"],
            "criteria": [{"id": c, "band": 7.0, "matched_anchor": "x", "rationale": "y"}
                         for c in rubric["criteria_ids"]],
            "overall_band": 7.0}
    print("合法样例 ->", validate_output(good, rubric) or "通过")
    bad = {"rubric_version": "wrong", "criteria": [{"id": "TR", "band": 7.3,
           "matched_anchor": "", "rationale": "y"}], "overall_band": 7.3}
    print("非法样例 ->")
    for e in validate_output(bad, rubric):
        print("   ·", e)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="对内置样例编译 prompt 并做校验冒烟")
    ap.add_argument("--features", nargs=2, metavar=("PROMPT_FILE", "ESSAY_FILE"))
    args = ap.parse_args()

    if args.features:
        pf, ef = (Path(x) for x in args.features)
        feats = compute_features(pf.read_text(encoding="utf-8"),
                                 ef.read_text(encoding="utf-8"))
        print(json.dumps(feats, indent=2))
        return 0
    if args.demo:
        return _demo()
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
