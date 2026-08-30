---
version: reading-label-evidence.v1
purpose: 为阅读题补 evidence（答案在原文中的定位）与 paraphrase（同义替换对）
module: reading
placeholders:
  - PASSAGE      # 段落化正文，带 A/B/C 标签
  - QUESTIONS    # 题干 + 标准答案
input_contract:
  passage: array<{label, text}>
  questions: array<{number, stem, answer}>
output_contract:
  items[]: {number, evidence, paraphrase, confidence}   # 内联字典的 value 里不能写 []，会破坏 YAML
---

# 任务

为每道题标注：

1. **evidence** —— 答案在原文中的依据句（必须能定位到具体段落与句子）
2. **paraphrase** —— 题干/选项与原文之间的同义替换对

## 为什么 paraphrase 必须现在沉淀

同义替换是雅思阅读的核心考察点。schema 里明确写了：
"必须在结构化阶段沉淀，不能留到运行时让 LLM 临时编"。

运行时临时生成的问题有两个：一是每次生成不一致，二是无法被 QC 校验。
预先沉淀后，它就成了可回归、可统计的资产。

# 正文

{{PASSAGE}}

# 题目与答案

{{QUESTIONS}}

# 输出

只输出 JSON：

```json
{
  "items": [
    {
      "number": 1,
      "evidence": [
        {"quote": "原文中的精确子串（归一化空白后必须完全匹配）",
         "paragraph_label": "B",
         "is_core": true}
      ],
      "paraphrase": [
        {"from": "题干中的表述", "to": "原文中的对应表述", "kind": "synonym"}
      ],
      "confidence": 0.9
    }
  ]
}
```

## 硬约束

- `quote` **必须是正文的精确子串**（归一化空白后完全匹配）。
  自己改写或概括的句子一律无效——它要能被程序校验定位。
- `paragraph_label` 必须取自正文给出的标签（A/B/C…），不要自创。
- `kind` 只能是以下之一：
  - `synonym` 同义词（significant ↔ considerable）
  - `word_family` 同词族（employ ↔ employment）
  - `structural` 结构转换（主动↔被动、从句↔短语）
  - `generalisation` 具体↔概括（cars, buses, trains ↔ vehicles）
- 找不到依据时，`evidence` 给空数组并把 `confidence` 压到 0.3 以下。
  **不要为了凑数编一个**——错误定位比没有定位更有害。

## 判断题（TRUE/FALSE/NOT GIVEN）特别注意

`NOT GIVEN` 的题**没有证据句**（这正是它的定义）。
这种情况 `evidence` 给空数组，`confidence` 保持高位，并在 paraphrase 里
体现"题干提到的概念在原文中出现了，但关系未被陈述"。
