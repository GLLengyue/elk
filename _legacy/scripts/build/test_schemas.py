#!/usr/bin/env python3
"""
test_schemas.py — schema 层的回归测试

覆盖三类回归：
    1. 每份 schema 自身是否是合法的 JSON Schema
    2. 跨文件 $ref 能否解析（common/ 下的 licence/provenance/span/difficulty）
    3. 合法样例能通过、非法样例被拒绝（尤其 if/then 那条硬约束）

第 3 类里最关键的一条：`band_source = derived_from_overall` 的记录，
`dimensions_trusted` 必须为空数组。四维既是从总分反推的，就不该有任何维度
被标为可信 —— 这条约束把 audit_writing_alignment.py 的结论固化进了数据结构，
防止后来者无脑把四维全当真值用。

用法
----
    ./.venv/bin/python scripts/build/test_schemas.py
    ./.venv/bin/python scripts/build/test_schemas.py -v   # 打印被拒用例的具体原因

退出码 0 = 全部符合预期。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "build"))

from jsonschema import Draft202012Validator          # noqa: E402
from validate import load_schema, REGISTRY, SCHEMAS  # noqa: E402

# --------------------------------------------------------------------------- 样例
PROMPT = {
    "schema_version": "writing-prompt.v1",
    "id": "wt2-academic-2023-2a",
    "task": 2,
    "test_type": "academic",
    "prompt_text": ("Some people think that governments should spend money on public "
                    "transport rather than building new roads. To what extent do you "
                    "agree or disagree?"),
    "task_family": "agree_disagree",
    "instruction_tail": "Give reasons for your answer and include any relevant examples.",
    "topic_tags": ["transport", "government"],
    "word_requirement": {"minimum": 250, "recommended": 300},
    "source": {
        "source_type": "official_sample",
        "source_id": "academic-writing-sample-tasks-2023.pdf",
        "licence": {"name": "IELTS Partners", "redistributable": False,
                    "notes": "官方免费样题，仅限个人备考，不得再发布"},
        "page_refs": [16],
    },
    "difficulty": {"cefr": "B2", "band_estimate": 6.5, "estimated_by": "human"},
}

ESSAY_BODY = "In many cities, traffic congestion has become a serious policy problem. " * 12


def make_essay(band_source: str, trusted: list[str], **over) -> dict:
    d = {
        "schema_version": "writing-essay.v1",
        "id": "we-test-001",
        "prompt_id": "wt2-academic-2023-2a",
        "task": 2,
        "test_type": "academic",
        "essay_text": ESSAY_BODY,
        "word_count": 108,
        "band_source": band_source,
        "dimensions_trusted": trusted,
        "human_bands": {"overall": 6.5, "TR": 7.0, "CC": 6.0, "LR": 6.0, "GRA": 7.0},
        "alignment_score": 0.71,
        "features": {"word_count": 108, "paragraph_count": 1, "sentence_count": 12,
                     "avg_sentence_words": 9.0, "rubric_overlap": 0.0,
                     "max_copied_span": 2, "copied_span_share": 0.018,
                     "concept_hit": 0.71, "type_token_ratio": 0.55},
        "split": "train",
        "is_official": False,
        "source": {"source_type": "open_dataset", "source_id": "chillies_task2",
                   "licence": {"name": "unknown", "redistributable": False}},
    }
    d.update(over)
    return d


def without(d: dict, key: str) -> dict:
    return {k: v for k, v in d.items() if k != key}


CASES = [
    # (名称, schema, 样例, 是否期望被拒)
    ("prompt 合法样例", "writing-prompt", PROMPT, False),
    ("essay model_annotated（chillies 风格）", "writing-essay",
     make_essay("model_annotated", ["TR", "CC"]), False),
    ("essay derived_from_overall（btnotpt 风格）", "writing-essay",
     make_essay("derived_from_overall", []), False),
    ("essay human_examiner（官方）", "writing-essay",
     make_essay("human_examiner", ["TR", "CC", "LR", "GRA"],
                is_official=True, examiner_comment="Band 6.5 ..."), False),
    ("essay 带 span 引用", "writing-essay",
     make_essay("human_examiner", ["TR"]), False),

    ("拒绝：derived 却声明信任 TR", "writing-essay",
     make_essay("derived_from_overall", ["TR"]), True),
    ("拒绝：缺 dimensions_trusted", "writing-essay",
     without(make_essay("model_annotated", []), "dimensions_trusted"), True),
    ("拒绝：band 非 0.5 步长", "writing-essay",
     make_essay("human_examiner", ["TR"], human_bands={"overall": 6.3}), True),
    ("拒绝：licence 缺 redistributable", "writing-prompt",
     {**PROMPT, "source": {**PROMPT["source"], "licence": {"name": "CC-BY-4.0"}}}, True),
    ("拒绝：prompt 缺 source", "writing-prompt",
     without(PROMPT, "source"), True),
]


def main() -> int:
    verbose = "-v" in sys.argv or "--verbose" in sys.argv
    failures = 0

    # 1 & 2: schema 自身合法性
    print("== schema 自身合法性 ==")
    for path in sorted(SCHEMAS.rglob("*.schema.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        try:
            Draft202012Validator.check_schema(doc)
            print(f"  OK   {path.relative_to(SCHEMAS)}")
        except Exception as e:
            failures += 1
            print(f"  FAIL {path.relative_to(SCHEMAS)}: {str(e)[:150]}")

    # 3: 样例校验
    print(f"\n== 样例校验（含跨文件 $ref）==")
    print(f"{'用例':<42}{'期望':<8}{'实际':<8}")
    print("-" * 66)
    for name, schema_name, obj, expect_reject in CASES:
        validator = Draft202012Validator(load_schema(schema_name), registry=REGISTRY)
        errs = list(validator.iter_errors(obj))
        got_reject = bool(errs)
        ok = got_reject == expect_reject
        if not ok:
            failures += 1
        print(f"{name:<42}{'拒绝' if expect_reject else '通过':<8}"
              f"{'拒绝' if got_reject else '通过':<8}{'OK' if ok else 'MISMATCH'}")
        if errs and (verbose or not ok):
            for e in errs[:3]:
                loc = "/".join(str(p) for p in e.absolute_path) or "(root)"
                print(f"      [{loc}] {e.message[:130]}")

    print("-" * 66)
    if failures:
        print(f"{failures} 项不符预期")
        return 1
    print("全部符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
