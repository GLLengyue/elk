# DSL 规范速查（reading-news-2026-08）

本包题目由 `_dsl/*.txt` 的紧凑 DSL 编译而成（`capability/scripts/pack_authoring/build.py`）。
**DSL 是唯一可编辑真相源，JSON 由 `regen.py` 重生成。**

## 头部

```
ID: news-<slug>-2026          # 必须全局唯一，决定 JSON 文件名
TAGS: climate,policy,business # 逗号分隔
TITLE: <标题>
URL: https://...              # 信源，可多个用 | 分隔
NOTES: <事实依据与信源说明>     # 命题依据，必填
```

## 段落

```
--- A
<段落原文，逐字，将作为 evidence 的检索底本>
--- B
...
```

段落标签 `A`–`H` 与题目 `EV:` 的段标签对应。

## 题组

```
### GROUP <题型>
INSTR: <答题指令原文>
ORDERED: true|false          # 题号是否顺序对应段落
WORD_LIMIT: NO MORE THAN TWO WORDS   # 仅填空/简答组需要
OPTIONS:                     # 仅 matching_* / multiple_choice 需要
    A | <选项文本>
    B | <选项文本>
```

题型枚举：`matching_information` / `matching_headings` / `matching_features` /
`matching_sentence_endings` / `identifying_information` / `identifying_writers_views` /
`multiple_choice` / `sentence_completion` / `summary_completion` /
`short_answer`。

`summary_completion` 的 `SUBTYPE` 写在 `### GROUP` 后：`summary_completion note` 或
`summary_completion summary`。

## 单题

```
Q1 <题干，summary 型须含 ______ 占位>
ANS: A                       # 选项键 / boolean3 的 TRUE|FALSE|NOT GIVEN / free_text 的答案
ACC: 变体1 ||| 变体2         # acceptable_answers（可选），用 ||| 分隔，规范答案自动并入
EV: A | <段落 A 的逐字连续子串>   # 证据，必须逐字、大小写一致、禁止省略号
SKILL: scanning             # 技能标签
EXP: <解析，说明答案为何成立>
```

## 铁律

1. `EV:` 的引用必须是段落 text 的**逐字连续子串**，`verify.py` 硬校验。
2. 大小写必须与原文一致（`Sub-Saharan` 不是 `sub-Saharan`）。
3. **禁止 `...` 省略号**；多句引用写多行 `EV:`。
4. `ACC` 用 `|||` 分隔（不是 `|`）；`|` 是段标签与引用的分隔符。
5. 填空题答案必须是原文真实词，且词数 ≤ `WORD_LIMIT`。
6. `summary_completion` 每题自带含 `______` 的题干，不支持独立 `SUMMARY:` 块。
7. 判断题（identifying_*）必须 TRUE/FALSE/NOT GIVEN 三态齐备。
