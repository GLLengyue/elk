---
version: writing-tag-question-type.v1
purpose: 识别雅思写作 Task 2 题型，用于组卷与 rubric 路由
module: writing
task: 2
placeholders:
  - PROMPT_TEXT
input_contract:
  prompt_text: string
output_contract:
  task_family: enum      # 见下方枚举
  instruction_tail: string
  requires_own_opinion: boolean
  requires_both_views: boolean
  confidence: number     # 0-1
---

# 任务

判断下面这道雅思写作 Task 2 题目属于哪一类题型，并标出它要求考生做什么。

## 题型枚举（与 writing-prompt.schema.json 的 `task_family` 一致，不要自创）

| 枚举值 | 判别特征 |
|---|---|
| `agree_disagree` | "To what extent do you agree or disagree?" / "Do you agree or disagree?" |
| `discuss_both` | "Discuss both these views and give your own opinion." |
| `advantages_disadvantages` | "Do the advantages outweigh the disadvantages?" |
| `problem_solution` | "What are the causes? What solutions can you suggest?" |
| `two_part_question` | 两个独立问句（"Why is this? How could this be controlled?"） |

## 判定要点

- 题目常**同时**含多个信号。以**最后一句提问**为准（它才是真正的任务指令）。
- `discuss_both` 与 `agree_disagree` 的区分：前者明确要求"讨论双方 + 给自己的看法"，
  后者要求"你同意到什么程度"。
- `requires_own_opinion`：题目是否要求考生**给出自己的立场**。
  `problem_solution` 通常不需要（它要的是分析而非立场）。
- `requires_both_views`：是否必须覆盖**对立的两方**。`discuss_both` 必须为 true。

# 题目

{{PROMPT_TEXT}}

# 输出

只输出 JSON：

```json
{
  "task_family": "discuss_both",
  "instruction_tail": "Discuss both these views and give your own opinion.",
  "requires_own_opinion": true,
  "requires_both_views": true,
  "confidence": 0.92
}
```

`confidence` 低于 0.7 时，说明题目表述模糊或有混合信号——如实给低分，
不要为了"看起来确定"而给高分。
