---
version: reading-generate-from-source.v1
purpose: 把一篇权威公开媒体的文章，按雅思学术类阅读的题型规范，转化成结构化阅读题（reading-test JSON）。用于题库扩充：LLM 负责"命题"这一不可复现环节，schema 负责"结构合规"这一可复现环节。
input_contract:
  SOURCE_TEXT: 原文正文（建议 700-1000 词，雅思阅读单篇长度）
  SOURCE_META: 来源元信息 JSON（source_url / origin / retrieved_at / licence）
  TARGET_TYPES: 期望生成的题型列表（从 11 种官方题型中选）
  TOTAL_QUESTIONS: 期望题量（建议 12-14，与真实考试单篇一致）
output_contract:
  format: 单个 JSON 对象，严格符合 schemas/reading-test.schema.json
  validation: 生成后必须跑 `elk_core.py validate <file> reading-test`
  rubric: 无（命题质量由人工/后续 QC 关卡把关）
---

You are converting an authentic English article into an IELTS Academic Reading test item.

## Hard constraints

1. **Do NOT copy long verbatim spans.** Paraphrase the passage into exam-appropriate
   academic prose (700–1000 words). Verbatim reproduction of copyrighted source text
   is not permitted. Keep technical terms and proper nouns, rewrite the rest.
2. **Every answer must be derivable from the passage.** If a question's answer cannot
   be located in a specific paragraph, discard that question — do not invent.
3. **Evidence is mandatory** for every question: quote the exact sentence(s) in your
   paraphrased passage that justify the answer, with `start`/`end` character offsets
   and `paragraph_label`.
4. **Distractors must be plausible but demonstrably wrong** — grounded in the passage,
   not obviously absurd.
5. Question numbering is continuous across groups (1..N), and `question_range` of each
   group must match the numbers it contains.

## Allowed question types (use `type` / `subtype` exactly as listed)

| type | subtype | notes |
|---|---|---|
| `multiple_choice` | `single_answer` / `multi_answer` | 4 options A–D |
| `identifying_information` | – | answer ∈ TRUE / FALSE / NOT GIVEN |
| `identifying_writers_views` | – | answer ∈ YES / NO / NOT GIVEN |
| `matching_information` | – | match statements to paragraphs A–H |
| `matching_headings` | – | stem = "Paragraph C", answer = heading key |
| `matching_features` | – | match items to a list of features/people |
| `matching_sentence_endings` | – | answer = ending key A–G |
| `sentence_completion` | – | `word_limit` required, e.g. "NO MORE THAN TWO WORDS" |
| `summary_completion` | `summary` / `note` / `table` / `flowchart` | `word_limit` required |
| `diagram_label_completion` | – | label a process/diagram |
| `short_answer` | – | `word_limit` required |

## Output shape

Return ONLY a JSON object (no prose, no markdown fence) matching
`schemas/reading-test.schema.json`. Top-level keys:

```json
{
  "schema_version": "1.0.0",
  "id": "<kebab-case-id, e.g. guardian-ai-climate-2026>",
  "module": "academic",
  "set_name": "<来源集合名>",
  "source": { "origin": "...", "source_url": "...", "retrieved_at": "...", "licence": "..." },
  "passage": {
    "title": "...", "subtitle": null,
    "paragraphs": [{"label": "A", "text": "..."}],
    "word_count": 850
  },
  "question_groups": [
    {
      "id": "q1-5", "type": "identifying_information",
      "instruction": "Do the following statements agree with the information given in the text?",
      "question_range": {"from": 1, "to": 5}, "ordered": true,
      "questions": [
        {
          "number": 1,
          "stem": "...",
          "answer": "TRUE",
          "answer_form": "boolean3",
          "evidence": [{"quote": "...", "start": 120, "end": 210, "paragraph_label": "B",
                        "paraphrase": "..."}]
        }
      ]
    }
  ],
  "meta": {
    "quality_status": "draft",
    "provenance": {
      "engine": "llm-generated",
      "model": "<model id>",
      "generated_at": "<ISO8601>",
      "source_licence": "...",
      "redistributable": true
    }
  }
}
```

## Source metadata

{{SOURCE_META}}

## Target question types

{{TARGET_TYPES}}

## Total questions

{{TOTAL_QUESTIONS}}

## Source article

{{SOURCE_TEXT}}
