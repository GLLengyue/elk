# 题库命题方法论（Synthetic Authoring）

本手册覆盖 **ELK 阅读题库的原创命题流水线**：从一篇新闻素材到一份可通过校验、能
被人工复核、且能由 DSL 源完整重现的雅思阅读题 JSON。

它与 `data-pipeline.md`（官方样题 `fetch → parse → validate`）互补——那里处理的是
**出题方发布的官方材料**；这里处理的是**我们基于公开新闻事实原创改写的 synthetic 题**。
两者共用同一套 `reading-test.schema.json` 契约与 `verify.py` 门禁。

> 核心理念一句话：**DSL 是唯一可编辑的真相源，JSON 必须可由 DSL 重现，答案本身
> 即「可接受答案」。** 任何不能由 DSL 重现、或 acceptable_answers 不含规范答案的 JSON，
> 都是数据债。

---

## 1. 工具链（均在 `capability/scripts/pack_authoring/`）

| 脚本 | 职责 | 关键约定 |
|---|---|---|
| `build.py` | 把紧凑 DSL 稿编译成符合 schema 的 JSON | `DSL` 是输入，JSON 是产物；`ANS` 自动并入 `acceptable_answers` |
| `verify.py` | 单文件校验（schema + 3 项语义检查） | 零依赖；`evidence.quote` 必须逐字命中段落 |
| `regen.py` | 由 DSL 全量重生成 JSON / 归一化 JSON-native 文件 | 改 acceptable_answers 形态，绝不改答案/证据/题干 |
| `check_all.py` | 34 篇机械层全量质检 | 从 `topic_pool.md` 推导计划，不写死篇号 |
| `refresh_pack_json.py` | 按磁盘重算 `pack.json` 计数 | 只改 `counts`，不动 `notes`/`licence` 等人工字段 |
| `build_index.py` | 生成练习目录页 `index.html` | 基于 `__file__` 推导路径，无本地硬编码 |

所有脚本路径均基于 `__file__` 推导，**无本地硬编码路径**，可在任意副本（capability /
expert / 已安装插件）直接运行。

---

## 2. DSL 中间层

直接手写 442 题 JSON 极易出错（证据偏移、题型枚举、字段遗漏）。DSL 把命题过程
压缩成人类可读、可 diff、可逐题复核的文本：

```
ID: news-xenotransplantation-2026
TAGS: medicine,ethics,biotech
TITLE: When the Donor Is Another Species
URL: https://example.com/...

--- A
<段落原文，逐字>
--- B
...

### GROUP matching_features
INSTR: ...
ORDERED: false
OPTIONS:
    A | the German protected-area survey
    B | the Swiss ninety-year record
Q1 It measured the weight of insects captured rather than the number of individuals or species.
ANS: A
EV: A | The traps in that study were weighed, not counted, so what they measured was mass
SKILL: scanning
EXP: A 段明确说按重量计量而非计数。
```

字段速查见包内 `BUILD.md`（DSL 规范速查）。

### 题型代号 COMBOS（选题池用）

| 代号 | 题组构成（每篇 13 题） |
|---|---|
| A | identifying_information/5 + summary_completion(note)/4 + multiple_choice/4 |
| B | matching_headings/5 + identifying_information/4 + short_answer/4 |
| C | matching_information/5 + identifying_information/4 + sentence_completion/4 |
| D | identifying_information/5 + multiple_choice/4 + summary_completion(summary)/4 |
| E | matching_features/5 + identifying_information/4 + summary_completion(note)/4 |
| F | matching_sentence_endings/5 + identifying_information/4 + summary_completion/4 |
| G | matching_information/5 + identifying_information/4 + multiple_choice/4 |
| H | matching_headings/5 + identifying_information/4 + summary_completion/4 |
| I | matching_information/5 + identifying_information/4 + multiple_choice/4 |
| J | identifying_information/5 + sentence_completion/4 + multiple_choice/4 |

> 每篇固定 13 题；判断题（identifying_*）必须 **TRUE / FALSE / NOT GIVEN** 三态齐备
> （或 writers_views 的 YES/NO/NOT GIVEN）。只出两态是硬伤。

---

## 3. 标准命题流水线

```
写 DSL 稿
   ↓  build.py 编译
生成 JSON（草稿）
   ↓  verify.py 机械校验
修 schema / 证据偏移
   ↓  人工语义复核（见 §5 清单）
改 DSL（不是改 JSON！）→ build.py 重编译
   ↓  regen.py 全量重生成 + check_all.py 机械质检
四副本同步（capability / expert assets / 已安装插件 / 工作副本）
   ↓  refresh_pack_json.py 重算计数 + build_index.py 重建目录
elk check 门禁全绿
```

**铁律：review 阶段的任何修正都改 DSL，再 build，不要直接手改 JSON。**
直接手改 JSON 会让 DSL 与 JSON 漂移（见 §6 的血泪教训）。

---

## 4. 证据（EV）铁律

`EV: <段标签> | <原文逐字连续子串>`

- **逐字**：必须是段落 text 的连续子串，`verify.py` 会硬校验。
- **大小写敏感**：原文 `Sub-Saharan` 就写 `Sub-Saharan`，不能写 `sub-Saharan`。
- **禁止 `...` 省略号**：要引用多句就写多段 `EV:`（一个 `EV:` 一行，可重复）。
- `ACC` 用 `|||` 分隔备选（**不是 `|`**，`|` 是段标签与引用的分隔符）。

---

## 5. 人工语义复核清单

`verify.py` 只管机械合法性，**管不了题意正确性**。逐篇必须过：

1. **答案在原文**：填空题/简答的答案必须是段落里的真实词（不是 paraphrase 改写后的词）。
   例：`the degree to which a food had been processed` 不能简写成 `the degree of processing`。
2. **词限**：`WORD_LIMIT: NO MORE THAN TWO WORDS` 时答案词数必须 ≤2（含连字符算一词）。
   例：`22 per cent` 是 3 词 → 必须升到 `THREE WORDS`。
3. **判断题三态齐备**：每套 identifying 题必须有 TRUE/FALSE/NOT GIVEN 各至少一例。
4. **NOT GIVEN 必须真未提**：用 `grep` 全文检索题干关键词，确认原文确实没出现该断言。
5. **MC 第二解排查**：当选项字面也满足题干时，题干必须加排他措辞。
   例：「one of the two largest issuers」会让 Verra 和 Gold Standard 都成解 →
   改为「named alongside Verra」锁定唯一解。
6. **Matching 选项唯一对应**：每个选项必须只命中一个段落/实体，不能两可。
7. **summary_completion 每题自带题干**：不支持独立 `SUMMARY:` 块，必须在 `Q10`
   等题里写含 `______` 的句子。误用独立块会让 `build.py` 因空 iterable 崩溃。

---

## 6. 踩过的坑（沉淀为约束）

| 坑 | 现象 | 根因 | 修复 |
|---|---|---|---|
| **判断题缺 NOT GIVEN** | 5 道全是 TRUE/FALSE | 初稿没设计 NG 题 | 把某题改为 NG，grep 确认原文真未提 |
| **答案不在原文** | verify 通过但答案词是 paraphrase | 偷懒用改写词当答案 | 题干改写使其直接命中原文词 |
| **EV 大小写错** | 审阅发现证据句首字母不对 | 随手小写 | 改回原文首字母大写 |
| **EV 用省略号** | 证据非逐字 | 为省篇幅省略 | 删 `...`，多句用多 `EV:` |
| **MC 第二解** | 两个选项都满足 | 题干措辞不排他 | 题干加 `alongside X` 锁定 |
| **word_limit 算错词** | `22 per cent` 算 2 词 | 忽略连写数词 | 升为 `THREE WORDS` |
| **summary 独立块崩溃** | `build.py min() empty` | 误用 `SUMMARY:` 块 | 改为每题自带 `______` 题干 |
| **fusion Q2 答案错** | DSL=TRUE / JSON=FALSE 漂移 | review 改了 JSON 没回写 DSL | 统一以 DSL 为真相源，regen 重生成 |
| **DSL↔JSON 漂移** | 313 处 acceptable_answers 不一致 | 旧 build.py 不把答案并入 acceptable | build.py 统一并入 + regen 全量重生成 |

---

## 7. 数据一致性约束（最重要）

1. **DSL = 唯一真相源**。JSON 由 `regen.py` 从 DSL 生成；禁止直接手改 JSON 后不回写 DSL。
2. **acceptable_answers 必含规范答案**：`build.py` 在编译时把 `ANS` 置首并入
   `acceptable_answers` 并去重。`regen.py` 对无 DSL 源的 JSON-native 文件做同样归一化。
   这是 2026-09-01 修复的历史不一致（此前 41 篇含答案、61 篇不含）。
3. **可重现性验证**：`regen.py --dry` 输出应全部 `unchanged`（DSL 与 JSON 零差异）。
   当前 24/34 篇有 DSL 源、可完整重现；10 篇为 JSON-native（早期已复核，待回填 DSL）。
4. **四副本必须字节一致**：capability / expert assets / 已安装插件 / 工作副本
   的 `data/reading/news/*.json` 应通过 `sync_assets.py` 保持同步。

---

## 8. 已知限制

- **10 篇 JSON-native 缺 DSL 源**（早期文章，已人工复核但未保留 DSL）。回流 DSL 是
   backlog：可用 `regen.py` 的归一化逻辑保底一致性，但 DSL 可重现性暂不覆盖这 10 篇。
- **synthetic 命题未经考生实测**：所有题均为 LLM 初稿 + 人工复核，未做大规模实测
  难度标定。难度标注（CEFR）为经验评级，非实测。
- **证据为单点核心句**：`EV` 默认取最能支撑答案的一句，不覆盖全部支撑证据。
