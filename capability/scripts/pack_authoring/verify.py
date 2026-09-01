#!/usr/bin/env python3
"""
雅思阅读题 JSON 校验器（零第三方依赖）

为什么不用 elk_core.py validate：
  它只检查 top-level 是否存在 passage / question_groups 两个键，
  源码注释写着「完整 JSON Schema 校验超出零依赖范围」。
  用它当质量门禁等于没有门禁——枚举值、必填字段、evidence 子串全部漏掉。

本脚本把 reading-test.schema.json 里真正会出错的约束全部实现，
外加 3 项 schema 管不到但决定复盘可信度的语义检查：
  · word_count 是否属实
  · evidence.quote 是否真的是段落的逐字子串
  · 题号是否连续且与 question_range 一致

用法：
  python3 verify.py <file.json>            # 只校验
  python3 verify.py <file.json> --fix      # 校验并重算 evidence 偏移后写回
"""
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------- 契约常量

ALLOWED_Q_TYPES = {
    "multiple_choice", "identifying_information", "identifying_writers_views",
    "matching_information", "matching_headings", "matching_features",
    "matching_sentence_endings", "sentence_completion", "summary_completion",
    "diagram_label_completion", "short_answer",
}
ALLOWED_SUBTYPES = {"summary", "note", "table", "flowchart", "single_answer", "multi_answer"}
ALLOWED_ANSWER_FORM = {"option_key", "boolean3", "free_text", "sentence_ending_key"}
ALLOWED_SKILL_TAGS = {
    "scanning", "skimming", "paraphrase", "inference",
    "main_idea", "detail", "writer_attitude", "cohesion",
}
ALLOWED_CEFR = {"B2", "C1", "B2-C1"}
ALLOWED_QUALITY = {
    "official", "parsed_unverified", "synthetic_passed",
    "synthetic_drill", "synthetic_quarantine", "rejected",
}
ALLOWED_SOURCE_TYPE = {
    "official_sample", "open_dataset", "synthetic",
    "self_authored", "user_submitted",
}
ALLOWED_MODULE = {"academic", "general_training"}
BOOL3 = {"TRUE", "FALSE", "NOT GIVEN"}
YESNO3 = {"YES", "NO", "NOT GIVEN"}
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")

TOP_KEYS = {"schema_version", "id", "module", "set_name", "source",
            "passage", "question_groups", "meta"}
PASSAGE_KEYS = {"id", "title", "subtitle", "paragraphs", "word_count", "topic_tags",
                "cefr", "vocab_profile", "fact_anchors", "has_diagram", "diagram_ref"}
GROUP_KEYS = {"id", "type", "subtype", "instruction", "word_limit",
              "question_range", "ordered", "options", "questions"}
QUESTION_KEYS = {"number", "stem", "answer", "answer_form", "acceptable_answers",
                 "evidence", "paraphrase", "distractor_rationale", "skill_tag",
                 "difficulty", "explanation"}
EVIDENCE_KEYS = {"quote", "start", "end", "paragraph_label", "is_core"}
META_KEYS = {"quality_status", "qc", "build_version", "content_hash", "created_at",
             "reviewed_by", "review_note", "not_official"}
SOURCE_KEYS = {"source_type", "source_id", "source_url", "licence", "retrieved_at",
               "verified_on", "page_refs", "derivation"}
LICENCE_KEYS = {"name", "url", "redistributable", "attribution", "notes"}

# 题型 → 答案字母表约束
BOOL3_TYPES = {"identifying_information"}
YESNO3_TYPES = {"identifying_writers_views"}
FILL_TYPES = {"sentence_completion", "summary_completion", "short_answer",
              "diagram_label_completion"}


def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warns: list[str] = []
        self.fixed: list[str] = []

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warns.append(msg)


def check_enums(rep: Report, path: str, val, allowed: set, label: str) -> None:
    if val not in allowed:
        rep.err(f"{path}: {label} 非法 {val!r}，允许 {sorted(allowed)}")


def check_keys(rep: Report, path: str, obj: dict, allowed: set, label: str) -> None:
    extra = set(obj.keys()) - allowed
    if extra:
        rep.err(f"{path}: {label} 出现不允许的字段 {sorted(extra)}（additionalProperties=false）")


def verify(data: dict, fix: bool = False) -> tuple[Report, dict]:
    rep = Report()

    if not isinstance(data, dict):
        rep.err("顶层不是对象")
        return rep, data

    # ---------------- 顶层
    check_keys(rep, "$", data, TOP_KEYS, "顶层")
    for k in ["schema_version", "id", "module", "source", "passage",
              "question_groups", "meta"]:
        if k not in data:
            rep.err(f"$.{k}: 必填缺失")
    if data.get("schema_version") != "1.0.0":
        rep.err(f"$.schema_version: 必须为 '1.0.0'，实际 {data.get('schema_version')!r}")
    if "id" in data and not ID_PATTERN.match(str(data["id"])):
        rep.err(f"$.id: {data['id']!r} 不符合 ^[a-z0-9][a-z0-9-]{{2,63}}$")
    if "module" in data:
        check_enums(rep, "$.module", data["module"], ALLOWED_MODULE, "module")

    # ---------------- source
    src = data.get("source")
    if isinstance(src, dict):
        check_keys(rep, "$.source", src, SOURCE_KEYS, "source")
        for k in ["source_type", "licence"]:
            if k not in src:
                rep.err(f"$.source.{k}: 必填缺失")
        if "source_type" in src:
            check_enums(rep, "$.source.source_type", src["source_type"],
                        ALLOWED_SOURCE_TYPE, "source_type")
        # page_refs 是 PDF 页码整数数组，不是 URL 列表（曾有生成方把信源 URL 塞进来）
        pr = src.get("page_refs")
        if pr is not None:
            if not isinstance(pr, list) or not all(isinstance(x, int) and x >= 1 for x in pr):
                if fix:
                    # 把误放的 URL 挪进 derivation，保留事实溯源信息
                    urls = [x for x in pr if isinstance(x, str)] if isinstance(pr, list) else []
                    if urls:
                        extra = "信源: " + " | ".join(urls)
                        src["derivation"] = (src.get("derivation", "") + " " + extra).strip()
                    del src["page_refs"]
                    rep.fixed.append(f"page_refs 非页码数组，{len(urls)} 个 URL 已并入 derivation 并删除该字段")
                else:
                    rep.err("$.source.page_refs: 必须是正整数数组（PDF 页码），"
                            "信源 URL 应放 source_url / derivation（可 --fix 自动归并）")
        lic = src.get("licence")
        if isinstance(lic, dict):
            check_keys(rep, "$.source.licence", lic, LICENCE_KEYS, "licence")
            for k in ["name", "redistributable"]:
                if k not in lic:
                    rep.err(f"$.source.licence.{k}: 必填缺失")
            if "redistributable" in lic and not isinstance(lic["redistributable"], bool):
                rep.err("$.source.licence.redistributable: 必须是布尔值")
        elif lic is not None:
            rep.err("$.source.licence: 必须是对象")
    elif src is not None:
        rep.err("$.source: 必须是对象")

    # ---------------- passage
    psg = data.get("passage")
    paragraphs: list[dict] = []
    if isinstance(psg, dict):
        check_keys(rep, "$.passage", psg, PASSAGE_KEYS, "passage")
        for k in ["id", "title", "paragraphs", "word_count"]:
            if k not in psg:
                rep.err(f"$.passage.{k}: 必填缺失")
        paragraphs = psg.get("paragraphs") or []
        if not isinstance(paragraphs, list) or len(paragraphs) == 0:
            rep.err("$.passage.paragraphs: 至少 1 段")
            paragraphs = []
        seen_labels: set[str] = set()
        for i, p in enumerate(paragraphs):
            if not isinstance(p, dict):
                rep.err(f"$.passage.paragraphs[{i}]: 不是对象")
                continue
            check_keys(rep, f"$.passage.paragraphs[{i}]", p, {"label", "text", "is_heading_target"}, "段落")
            if "label" not in p or "text" not in p:
                rep.err(f"$.passage.paragraphs[{i}]: 缺 label 或 text")
                continue
            lab = p["label"]
            if lab in seen_labels:
                rep.err(f"$.passage.paragraphs[{i}]: 段落标签 {lab!r} 重复")
            seen_labels.add(lab)
            if not p["text"].strip():
                rep.err(f"$.passage.paragraphs[{i}] (段 {lab}): 文本为空")

        # 词数核对
        if paragraphs and "word_count" in psg:
            actual = sum(len(p["text"].split()) for p in paragraphs)
            declared = psg["word_count"]
            if not isinstance(declared, int):
                rep.err("$.passage.word_count: 必须是整数")
            elif abs(actual - declared) > max(3, declared * 0.02):
                rep.err(f"$.passage.word_count: 声明 {declared}，实际 {actual}，偏差过大")
                if fix:
                    psg["word_count"] = actual
                    rep.fixed.append(f"word_count 已重算为 {actual}")
            if isinstance(declared, int) and not (150 <= declared <= 3600):
                rep.err(f"$.passage.word_count: {declared} 超出 schema 允许区间 150-3600")
            if isinstance(declared, int) and not (700 <= declared <= 950):
                rep.warn(f"$.passage.word_count: {declared}，雅思单篇建议 700-950")

        if "cefr" in psg and psg["cefr"] is not None:
            check_enums(rep, "$.passage.cefr", psg["cefr"], ALLOWED_CEFR, "cefr")
    elif psg is not None:
        rep.err("$.passage: 必须是对象")

    # 全文（偏移基准：各段 text 用 \n 连接）
    full_text = "\n".join(p.get("text", "") for p in paragraphs)
    para_by_label = {p.get("label"): p.get("text", "") for p in paragraphs}
    # 每段在 full_text 中的起始偏移
    para_offset: dict[str, int] = {}
    cursor = 0
    for p in paragraphs:
        para_offset[p.get("label")] = cursor
        cursor += len(p.get("text", "")) + 1  # +1 for the \n

    # ---------------- question_groups
    groups = data.get("question_groups")
    all_numbers: list[int] = []
    if isinstance(groups, list) and groups:
        for gi, g in enumerate(groups):
            gpath = f"$.question_groups[{gi}]"
            if not isinstance(g, dict):
                rep.err(f"{gpath}: 不是对象")
                continue
            check_keys(rep, gpath, g, GROUP_KEYS, "题组")
            for k in ["id", "type", "instruction", "question_range", "ordered", "questions"]:
                if k not in g:
                    rep.err(f"{gpath}.{k}: 必填缺失")
            gtype = g.get("type")
            if gtype is not None:
                check_enums(rep, f"{gpath}.type", gtype, ALLOWED_Q_TYPES, "type")
            if "subtype" in g:
                check_enums(rep, f"{gpath}.subtype", g["subtype"], ALLOWED_SUBTYPES, "subtype")
            if "ordered" in g and not isinstance(g["ordered"], bool):
                rep.err(f"{gpath}.ordered: 必须是布尔值")
            if gtype in FILL_TYPES and not g.get("word_limit"):
                rep.err(f"{gpath}.word_limit: {gtype} 题型必须给 word_limit")

            # options（共享选项池）
            opt_keys = {o.get("key") for o in g.get("options", []) if isinstance(o, dict)}
            if gtype in {"matching_headings", "matching_features",
                         "matching_sentence_endings", "multiple_choice"}:
                if not g.get("options"):
                    rep.err(f"{gpath}: {gtype} 题型必须提供 options 选项池")
            for oi, o in enumerate(g.get("options", [])):
                if not isinstance(o, dict):
                    rep.err(f"{gpath}.options[{oi}]: 不是对象")
                    continue
                check_keys(rep, f"{gpath}.options[{oi}]", o,
                           {"key", "text", "is_correct", "distractor_type"}, "选项")
                if "key" not in o or "text" not in o:
                    rep.err(f"{gpath}.options[{oi}]: 缺 key 或 text")

            # questions
            qs = g.get("questions")
            if not isinstance(qs, list) or not qs:
                rep.err(f"{gpath}.questions: 至少 1 题")
                continue
            g_nums: list[int] = []
            for qi, q in enumerate(qs):
                qpath = f"{gpath}.questions[{qi}]"
                if not isinstance(q, dict):
                    rep.err(f"{qpath}: 不是对象")
                    continue
                check_keys(rep, qpath, q, QUESTION_KEYS, "题目")
                for k in ["number", "stem", "answer"]:
                    if k not in q:
                        rep.err(f"{qpath}.{k}: 必填缺失")
                num = q.get("number")
                if isinstance(num, int):
                    g_nums.append(num)
                    all_numbers.append(num)
                    if not (1 <= num <= 40):
                        rep.err(f"{qpath}.number: {num} 超出 1-40")
                elif num is not None:
                    rep.err(f"{qpath}.number: 必须是整数")

                ans = q.get("answer")
                if "answer_form" in q:
                    check_enums(rep, f"{qpath}.answer_form", q["answer_form"],
                                ALLOWED_ANSWER_FORM, "answer_form")

                # 答案取值域
                if gtype in BOOL3_TYPES:
                    if ans not in BOOL3:
                        rep.err(f"{qpath}.answer: T/F/NG 题型答案必须是 {sorted(BOOL3)}，实际 {ans!r}")
                elif gtype in YESNO3_TYPES:
                    if ans not in YESNO3:
                        rep.err(f"{qpath}.answer: Y/N/NG 题型答案必须是 {sorted(YESNO3)}，实际 {ans!r}")
                elif gtype in {"matching_headings", "matching_features",
                               "matching_sentence_endings", "multiple_choice"}:
                    if opt_keys:
                        if isinstance(ans, str):
                            if ans not in opt_keys:
                                rep.err(f"{qpath}.answer: {ans!r} 不在选项池 {sorted(opt_keys)} 中")
                        elif isinstance(ans, list):
                            for a in ans:
                                if a not in opt_keys:
                                    rep.err(f"{qpath}.answer: {a!r} 不在选项池 {sorted(opt_keys)} 中")
                            if gtype == "multiple_choice" and g.get("subtype") == "single_answer":
                                rep.err(f"{qpath}.answer: single_answer 不应给多个答案")
                        else:
                            rep.err(f"{qpath}.answer: 必须是字符串或字符串数组")
                elif gtype == "matching_information":
                    if ans not in para_by_label:
                        rep.err(f"{qpath}.answer: 段落匹配题答案 {ans!r} 不是有效段落标签 {sorted(para_by_label)}")

                if "skill_tag" in q:
                    if not isinstance(q["skill_tag"], list) or not q["skill_tag"]:
                        rep.err(f"{qpath}.skill_tag: 至少 1 个")
                    else:
                        for t in q["skill_tag"]:
                            check_enums(rep, f"{qpath}.skill_tag", t, ALLOWED_SKILL_TAGS, "skill_tag")

                # evidence：精确子串 + 偏移重算
                evs = q.get("evidence")
                if not evs:
                    rep.err(f"{qpath}.evidence: 缺失（契约要求每题必有证据）")
                    continue
                for ei, ev in enumerate(evs):
                    epath = f"{qpath}.evidence[{ei}]"
                    if not isinstance(ev, dict):
                        rep.err(f"{epath}: 不是对象")
                        continue
                    check_keys(rep, epath, ev, EVIDENCE_KEYS, "evidence")
                    for k in ["quote", "start", "end", "paragraph_label"]:
                        if k not in ev:
                            rep.err(f"{epath}.{k}: 必填缺失")
                    quote = ev.get("quote", "")
                    lab = ev.get("paragraph_label")
                    ptext = para_by_label.get(lab)
                    if ptext is None:
                        rep.err(f"{epath}.paragraph_label: {lab!r} 不存在于段落列表")
                        continue
                    if quote not in ptext:
                        rep.err(f"{epath}.quote: 不是段落 {lab} 的逐字子串，"
                                f"前 60 字符 = {quote[:60]!r}")
                        continue
                    # 重算偏移（相对 full_text）
                    local = ptext.index(quote)
                    abs_start = para_offset[lab] + local
                    abs_end = abs_start + len(quote)
                    if fix and (ev.get("start") != abs_start or ev.get("end") != abs_end):
                        ev["start"] = abs_start
                        ev["end"] = abs_end
                        rep.fixed.append(f"第 {num} 题 evidence[{ei}] 偏移重算 → [{abs_start}, {abs_end}]")
                    elif not fix:
                        if ev.get("start") != abs_start or ev.get("end") != abs_end:
                            rep.warn(f"{epath}: 偏移 ({ev.get('start')},{ev.get('end')}) "
                                     f"与重算值 ({abs_start},{abs_end}) 不符，需 --fix")
                    # 校验 full_text[start:end] 确实等于 quote
                    if full_text[abs_start:abs_end] != quote:
                        rep.err(f"{epath}: 全文切片校验失败（偏移基准不一致）")

            # question_range 与题号一致
            gr = g.get("question_range")
            if isinstance(gr, dict) and g_nums:
                if gr.get("from") != min(g_nums) or gr.get("to") != max(g_nums):
                    rep.err(f"{gpath}.question_range: 声明 {gr.get('from')}-{gr.get('to')}，"
                            f"实际题号 {min(g_nums)}-{max(g_nums)}")
                    if fix and g_nums:
                        gr["from"], gr["to"] = min(g_nums), max(g_nums)
                        rep.fixed.append(f"{gpath}.question_range 已修正为 {min(g_nums)}-{max(g_nums)}")
                if sorted(g_nums) != list(range(min(g_nums), max(g_nums) + 1)):
                    rep.err(f"{gpath}: 题号不连续，实际 {sorted(g_nums)}")
    elif groups is None:
        rep.err("$.question_groups: 缺失")
    else:
        rep.err("$.question_groups: 必须是非空数组")

    # 题号全局连续
    if all_numbers:
        if sorted(all_numbers) != list(range(1, max(all_numbers) + 1)):
            rep.err(f"题号全局不连续/有重复：{sorted(all_numbers)}")
        n = max(all_numbers)
        if not (12 <= n <= 14):
            rep.warn(f"总题数 {n}，建议 12-14（真实考试单篇 13-14 题）")

    # ---------------- meta
    meta = data.get("meta")
    if isinstance(meta, dict):
        check_keys(rep, "$.meta", meta, META_KEYS, "meta")
        for k in ["quality_status", "build_version", "created_at", "not_official"]:
            if k not in meta:
                rep.err(f"$.meta.{k}: 必填缺失")
        if "quality_status" in meta:
            check_enums(rep, "$.meta.quality_status", meta["quality_status"],
                        ALLOWED_QUALITY, "quality_status")
        if "not_official" in meta and meta["not_official"] is not True:
            rep.err("$.meta.not_official: 必须为 true（合规硬约束）")
    elif meta is not None:
        rep.err("$.meta: 必须是对象")

    return rep, data


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    fix = "--fix" in sys.argv
    if not args:
        print(__doc__)
        return 2
    path = Path(args[0])
    if not path.exists():
        print(f"文件不存在: {path}")
        return 2
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"JSON 解析失败: {e}")
        return 2

    rep, data = verify(data, fix=fix)

    if fix and rep.fixed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    name = path.stem
    if rep.errors:
        print(f"✗ {name} — {len(rep.errors)} 项错误")
        for e in rep.errors:
            print(f"   [ERR ] {e}")
        for w in rep.warns:
            print(f"   [warn] {w}")
        return 1
    print(f"✓ {name} — 通过")
    for f in rep.fixed:
        print(f"   [fix ] {f}")
    for w in rep.warns:
        print(f"   [warn] {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
