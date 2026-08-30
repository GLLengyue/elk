#!/usr/bin/env python3
"""
make_sample_data.py — 生成随仓分发的合成示例数据

为什么需要合成数据
------------------
本仓库**刻意不含任何真实考试题**：官方 PDF 与它们的解析产物都受版权约束
（redistributable: false），口语题库来自第三方代理、无再授权。

但"开箱即用"要求新克隆之后 README 里的每一步都能真跑通。
解法就是随仓发一份**自造的、无版权负担**的最小数据集：
内容由本脚本凭空写出，不是任何真题的复制或改写。

它覆盖两类结构：
    examples/data/reading/sample-reading-test.json   符合 reading-test.schema.json
    examples/data/speaking/sample-speaking-topics.jsonl  符合 speaking-topic.schema.json

数据量刻意做小（1 篇 + 5 题 / 3 个题组），只用于验证链路，
不足以做任何统计意义上的评测——这一点在 README 的"已知限制"里也写了。

用法
----
    ./.venv/bin/python examples/make_sample_data.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

OUT = ROOT / "examples" / "packs" / "demo-pack"
NOW = datetime.now(timezone(timedelta(hours=8))).isoformat()

# ---------------------------------------------------------------- 合成阅读题
# 短文与题目均为凭空撰写的示例，主题为虚构的"城市绿地"研究，
# 用词与结构模仿学术科普，但内容不来自任何真实出版物。
PASSAGE = [
    {"label": "A",
     "text": "Urban green space has moved from being a decorative afterthought to a "
             "measurable component of city planning. Municipal engineers now treat "
             "parks the way they treat drainage: as infrastructure with a "
             "maintenance budget and a performance target."},
    {"label": "B",
     "text": "The most cited benefit is temperature regulation. Measurements across "
             "twelve European cities found that neighbourhoods with continuous tree "
             "canopy were up to four degrees cooler during heat waves than districts "
             "of comparable density without one."},
    {"label": "C",
     "text": "A second benefit concerns water. Vegetated surfaces absorb rainfall "
             "that would otherwise overwhelm combined sewers, which reduces the "
             "frequency of overflow events. Engineers describe this as attenuating "
             "the peak flow rather than increasing total capacity."},
    {"label": "D",
     "text": "Not all schemes perform equally, however. A review of forty "
             "regeneration projects concluded that parks created without a "
             "long-term maintenance commitment frequently deteriorated within a "
             "decade, and in several cases were perceived as less safe than the "
             "sites they replaced."},
    {"label": "E",
     "text": "Planners have drawn a practical lesson from these mixed results. "
             "Green space delivers the benefits described above only when "
             "funding for upkeep is committed at the same time as the capital "
             "works, and when local residents are consulted about how the space "
             "will actually be used. Where both conditions hold, the "
             "infrastructure analogy appears to be justified."},
]

QUESTIONS = [
    {"number": 1, "stem": "Green space is now treated as infrastructure with performance targets.",
     "answer": "TRUE",
     "evidence": [{"quote": "Municipal engineers now treat parks the way they treat drainage: "
                            "as infrastructure with a maintenance budget and a performance target.",
                   "start": 118, "end": 232, "paragraph_label": "A", "is_core": True}],
     "paraphrase": [{"from": "performance targets", "to": "a performance target",
                     "kind": "word_family"}]},
    {"number": 2, "stem": "Tree canopy reduced temperatures by more than ten degrees.",
     "answer": "FALSE",
     "evidence": [{"quote": "neighbourhoods with continuous tree canopy were up to four "
                            "degrees cooler during heat waves",
                   "start": 300, "end": 385, "paragraph_label": "B", "is_core": True}],
     "paraphrase": [{"from": "reduced temperatures by more than ten degrees",
                     "to": "up to four degrees cooler", "kind": "synonym"}]},
    {"number": 3, "stem": "Vegetated surfaces help by ______ the peak flow.",
     "answer": "attenuating",
     "evidence": [{"quote": "Engineers describe this as attenuating the peak flow rather "
                            "than increasing total capacity.",
                   "start": 560, "end": 648, "paragraph_label": "C", "is_core": True}],
     "paraphrase": [{"from": "help by", "to": "describe this as", "kind": "structural"}]},
    {"number": 4, "stem": "Parks without a maintenance commitment often ______ within ten years.",
     "answer": "deteriorated",
     "evidence": [{"quote": "parks created without a long-term maintenance commitment "
                            "frequently deteriorated within a decade",
                   "start": 700, "end": 792, "paragraph_label": "D", "is_core": True}],
     "paraphrase": [{"from": "within ten years", "to": "within a decade", "kind": "synonym"}]},
    {"number": 5, "stem": "Which of the following is the most suitable title for the passage?",
     "answer": "C",
     "evidence": [{"quote": "Urban green space has moved from being a decorative "
                            "afterthought to a measurable component of city planning.",
                   "start": 0, "end": 112, "paragraph_label": "A", "is_core": False}]},
]

READING = {
    "schema_version": "1.0.0",
    "id": "sample-reading-urban-green-space",
    "module": "academic",
    "set_name": "elk-sample-data",
    "source": {
        "source_type": "synthetic",
        "source_id": "examples/make_sample_data.py",
        "licence": {"name": "CC0-1.0", "redistributable": True,
                    "notes": "凭空撰写的示例内容，不含任何真实考试材料。"},
        "retrieved_at": NOW,
        "derivation": "由 examples/make_sample_data.py 生成",
    },
    "passage": {
        "id": "sample-reading-urban-green-space-text",
        "title": "Green Space as Urban Infrastructure",
        "subtitle": "Why cities are budgeting for parks the way they budget for drains",
        "paragraphs": PASSAGE,
        "word_count": sum(len(p["text"].split()) for p in PASSAGE),
        "has_diagram": False,
    },
    "question_groups": [{
        "id": "sample-q1-5",
        "type": "summary_completion",
        "subtype": "note",
        "instruction": "Complete the notes below. Choose ONE WORD ONLY from the passage.",
        "word_limit": "ONE WORD ONLY",
        "question_range": {"from": 1, "to": 5},
        "ordered": True,
        "questions": QUESTIONS,
    }],
    "meta": {
        "quality_status": "synthetic_drill",
        "qc": {"evidence_missing": False, "question_count": 5, "answers_matched": 5},
        "build_version": "make_sample_data.py@0.1",
        "created_at": NOW,
        "not_official": True,
    },
}

# ---------------------------------------------------------------- 合成口语题组
# 题组字段严格对齐 speaking-topic.schema.json：
#   part ∈ {1,2}；season 必须是 YYYY-MM-DD；catalog ∈ {person,object,event,place}
#   cue_card 在 part=1 时为 null，part=2 时为 {prompt, bullets[3-6]} 对象
#   source 是必需字段（{kind, origin}）
SOURCE = {
    "kind": "synthetic",
    "origin": "examples/make_sample_data.py",
    "license_note": "凭空撰写的示例内容，不含任何真实考试材料。",
    "redistributable": True,
}

SPEAKING = [
    {"id": "sample-sp-001", "part": 1, "season": "2026-01-01",
     "catalog": "place", "topic_name": "Hometown",
     "questions": [{"text": "Where is your hometown?"},
                   {"text": "What do you like most about it?"},
                   {"text": "Has it changed much since you were a child?"}],
     "cue_card": None,
     "not_official": True, "source": SOURCE},
    {"id": "sample-sp-002", "part": 2, "season": "2026-01-01",
     "catalog": "object", "topic_name": "Describe a useful object you own",
     "cue_card": {
         "prompt": "Describe a useful object you own.",
         "bullets": ["what it is", "how long you have had it",
                     "how often you use it",
                     "and explain why it is useful to you"],
         "raw": "Describe a useful object you own.\nYou should say:\n"
                "what it is\nhow long you have had it\n"
                "how often you use it\nand explain why it is useful to you.",
     },
     "part3_questions": [
         {"text": "Do people in your country buy more things than they used to?"},
         {"text": "How might shopping habits change in the future?"}],
     "not_official": True, "source": SOURCE},
    {"id": "sample-sp-003", "part": 2, "season": "2026-01-01",
     "catalog": "event", "topic_name": "Describe a time you learned something new",
     "cue_card": {
         "prompt": "Describe a time you learned something new.",
         "bullets": ["what you learned", "where you learned it",
                     "how you learned it",
                     "and explain how you felt about it"],
         "raw": "Describe a time you learned something new.\nYou should say:\n"
                "what you learned\nwhere you learned it\n"
                "how you learned it\nand explain how you felt about it.",
     },
     "part3_questions": [
         {"text": "What skills do you think children should learn at school?"},
         {"text": "Is it better to learn alone or with others?"}],
     "not_official": True, "source": SOURCE},
]


def main() -> int:
    (OUT / "data" / "reading").mkdir(parents=True, exist_ok=True)

    rp = OUT / "data" / "reading" / "sample-reading-test.json"
    rp.write_text(json.dumps(READING, ensure_ascii=False, indent=2), encoding="utf-8")

    n_q = sum(len(g["questions"]) for g in READING["question_groups"])
    manifest = {
        "pack_id": "demo-pack",
        "pack_version": "0.1.0",
        "schema_version": "1.0.0",
        "title": "Format Demo Pack",
        "description": "仅用于演示数据包格式与加载流程，不含任何真实考试材料，"
                       "也不应作为练习内容使用。",
        "modules": ["reading"],
        "licence": {"name": "CC0-1.0", "redistributable": True},
        "redistributable": True,
        "contents": {
            "counts": {"reading_items": 1, "reading_questions": n_q,
                       "speaking_topics": 0, "speaking_questions": 0,
                       "writing_prompts": 0, "writing_essays": 0},
            "seasons": [],
            "source_types": ["synthetic"],
        },
        "notes": "格式示范包。真实数据请使用正式数据包替换。",
    }
    (OUT / "pack.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print("格式示范数据包已生成（凭空撰写，无版权负担）")
    print(f"  {rp.relative_to(ROOT)}")
    print(f"  {OUT.relative_to(ROOT)}/pack.json")
    print(f"  1 篇 / {n_q} 题 / {READING['passage']['word_count']} 词")
    print()
    print("加载：elk load examples/packs/demo-pack")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
