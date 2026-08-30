---
version: speaking-generate-part3.v1
purpose: 基于 Part 2 话题卡生成 Part 3 追问（考官在真实考试中的追问风格）
module: speaking
placeholders:
  - TOPIC_NAME
  - CUE_CARD
  - N
input_contract:
  topic_name: string
  cue_card: string
params:                    # 控制参数，非数据字段，不参与契约点 B 校验
  n: int          # 要生成几问，通常 4-6
output_contract:
  questions[]: {text, type, difficulty}
---

# 任务

基于下面的 Part 2 话题卡，生成 {{N}} 个 Part 3 追问。

# 话题

**{{TOPIC_NAME}}**

{{CUE_CARD}}

# Part 3 的考察本质

Part 2 考的是**描述与个人经历**（"Describe a time when…"），
Part 3 考的是**抽象讨论与论证**（"Do you think society…?"）。

所以 Part 3 的追问必须**从具体经历抽离到一般性问题**，
否则就只是重复 Part 2。考官在真实考试里做的正是这个跃迁。

## 六类追问（尽量覆盖 3 类以上，不要全挤在一类）

| type | 特征 | 例子 |
|---|---|---|
| `opinion` | 要求给出看法 | "Do you think … is important?" |
| `compare` | 两代/两地/两类对比 | "How is this different from … thirty years ago?" |
| `cause` | 追问原因 | "Why do you think … has become so popular?" |
| `hypothetical` | 假设未来或反事实 | "How might this change in the future?" |
| `evaluation` | 评价政策或做法 | "Should governments …?" |
| `example` | 要求举社会层面的例 | "Can you give an example of … in your country?" |

## 质量要求

- 问句必须是**完整、可直接朗读**的英文，长度 8–25 词。
- 不要生成"是/否"就能答完的封闭问题（Part 3 要的是展开）。
- 难度递增：前 1–2 问贴近话题（容易开口），后几问更抽象。
- 不要出现话题卡里已经问过的具体内容。

# 输出

只输出 JSON：

```json
{
  "questions": [
    {"text": "Do you think people today spend too much time on their phones?",
     "type": "opinion",
     "difficulty": "easy"},
    {"text": "How might technology change the way we communicate in the next twenty years?",
     "type": "hypothetical",
     "difficulty": "hard"}
  ]
}
```

`difficulty` 只能是 `easy` / `medium` / `hard`。
