---
name: elk-coach
description: 自包含零依赖的 ELK 英语学习教练操作手册：环境自检、题库检索、评分锚点、prompt 渲染、数据校验。本 skill 是自包含可分发包，资产（schemas/rubrics/prompts/样题包）全部内置在本目录 assets/ 下，与任何本地项目路径完全解耦。当需要检索雅思真题、计算评分客观锚点、渲染评分 prompt、校验数据契约时使用。
---

# ELK Coach — 自包含操作手册

## 定位

本 skill 是**完全自包含、可无限分发**的英语学习能力包：

- 所有资产（10 个 schema 契约、2 份 rubric、5 个 prompt 模板、官方样题包）都在本目录 `assets/` 下
- 所有逻辑都集中在 `scripts/elk_core.py` —— **纯 Python 标准库，零第三方依赖**
- 路径基于 `__file__` 相对解析，**没有任何机器/用户/项目相关的硬编码**
- 复制整个 skill 目录到任何装有 Python 3.10+ 的机器即可使用

## 运行方式

```bash
# 用任意 python3 直接运行（无需 pip install、无需 .venv、无需环境变量）
python3 scripts/elk_core.py <命令>
```

首次使用先建索引，然后即可检索：

```bash
python3 scripts/elk_core.py index    # 扫描 assets/packs 建 FTS5 检索表（幂等）
python3 scripts/elk_core.py check    # 5 项门禁自检
```

## 命令速查

| 命令 | 用途 |
|---|---|
| `elk_core.py check` | 5 项门禁自检（资产完整性 / 数据可解析 / prompt 模板 / rubric 结构 / 索引状态） |
| `elk_core.py index` | 重建 FTS5 检索索引（读取 assets/packs 下所有数据包） |
| `elk_core.py search <关键词> [--limit N]` | FTS5 全文检索：`python3 scripts/elk_core.py search "archaeology"` |
| `elk_core.py features <题文件> <作文文件>` | 计算评分客观锚点（词数/段落/抄题跨度/TTR/concept_hit 等） |
| `elk_core.py render <rubric> <题文件> <作文文件> [--features JSON]` | 编译评分 prompt（rubric 自动注入） |
| `elk_core.py prompts` | 列出全部 prompt 模板及版本 |
| `elk_core.py validate <数据文件> <schema名>` | 校验数据契约（pack/reading-test/writing-essay/speaking-topic） |
| `elk_core.py paths` | 打印解析到的资产路径（排查用） |
| `elk_core.py items` | 列出全部阅读题 id / 题量 / 词数 |
| `elk_core.py render-reading <题目id> [--out 页.html]` | 渲染米色护眼的阅读练习页（自包含单文件 HTML） |

rubric 可选值：`writing-task2.v1` / `speaking.v1`。

## 出题流程（阅读练习页）

用户要练阅读时，**不要**把题目以纯文本贴出来，而是生成 HTML 练习页：

```bash
# 1. 找题（用户给主题时用 search，要浏览全部时用 items）
python3 scripts/elk_core.py search "archaeology"
python3 scripts/elk_core.py items

# 2. 渲染成米色护眼练习页
python3 scripts/elk_core.py render-reading <题目id> --out reading.html
```

然后用 `present_files` 把 HTML 打开给用户（会自动进入预览面板）。

页面能力：左栏原文（段落带 A/B/C 标号）· 右栏题目 · 顶部计时 ·
提交后即时判分并展开每道题的答案与原文证据（evidence + paraphrase）·
支持 Reset 重做、打印。

设计语言固定为**米色护眼**（`--paper: #FAF6EC` / `--ink: #3A3226`），
改动配色请只调 `:root` 里的 CSS 变量，不要散着写颜色值。

## 资产清单（内置，随包分发）

```
assets/
├── schemas/          # 10 个 JSON Schema 契约（pack/reading-test/writing-essay/speaking-topic/...）
├── rubrics/          # writing-task2.v1 + speaking.v1（JSON 化，无需 YAML 依赖）
├── prompts/          # 5 个 prompt 模板（writing/score、writing/tag-question-type、speaking/score、speaking/generate-part3、reading/label-evidence）+ prompts.json
└── packs/            # 两个数据包：自建新闻阅读包 + 官方口语话题包
    ├── reading-news-2026-08/       # 自建：公开新闻事实原创改写（redistributable: true）
    │   ├── pack.json
    │   └── data/reading/news/...   # 每篇 13 题 / 3 题组，答案均带原文 evidence
    └── speaking-official-sample/   # 官方口语 Part 1-3 话题快照
        ├── pack.json
        └── data/speaking/...
```

数据包合规状态：两个包均在 pack.json 中声明 `redistributable: true`。阅读包为**基于公开新闻事实的原创改写**（事实不受版权保护，句式与结构全部重写，每篇标注 `meta.not_official=true`）；口语包为官方公开话题快照。`elk fetch` 抓取的官方源内容仅限个人使用、不入仓。

## 评分流程（写作 Task 2）

1. 预计算客观锚点（不让 LLM 自己数）：
   ```bash
   python3 scripts/elk_core.py features prompt.txt essay.txt
   # → word_count / paragraph_count / sentence_count / rubric_overlap /
   #   max_copied_span / copied_span_share / concept_hit / type_token_ratio
   ```
2. 编译评分 prompt（rubric 自动注入）：
   ```bash
   python3 scripts/elk_core.py render writing-task2.v1 prompt.txt essay.txt --features '{"word_count":204}'
   ```
3. 按 rubric 判分：输出四维 `band_range` + `matched_anchor`（命中判据原文）+ `rationale`（≤60 词）+ `improvements`
4. `rubric_overlap` 与 `max_copied_span` 驱动"抄题不给分"封顶规则——触发即压上限，独立于 LLM 判断

## 题目检索（FTS5）

找题用 SQL、用题才交 LLM：

```bash
python3 scripts/elk_core.py search "archaeology"          # 阅读主题检索
python3 scripts/elk_core.py search "hometown" --limit 5   # 口语话题检索
```

检索命中后，按 `id` 定位到 `assets/packs/` 下对应数据包的对应文件，
把原文注入给 LLM 使用（索引只存检索字段，不含完整正文，命中后再按需读原文）。

## 数据扩充（自包含模式）

用户自备数据可通过以下方式纳入（全程只读，不修改内置 assets）：

- **标准数据包**：按 `assets/schemas/pack.schema.json` 契约组织目录 + `pack.json`，
  放入任意位置，参考 `assets/packs/reading-news-2026-08/` 的结构
- **示例数据**：`assets/examples/`（如存在）展示最小可用的 pack 形态
- 契约校验：`python3 scripts/elk_core.py validate <file> <schema>`

## 陷阱备忘

- **FTS5 表无主键**：索引层已按 id 去重（后加载覆盖先加载），勿手动修改 state/elk.db
- **发音无音频 → unavailable**：不编造发音分
- **band_range 而非单点**：写作/口语无可信公开真值，只给区间，不给"考官会给 X 分"的断言
- **LLM 不擅长精确计数**：词数/错误数一律用 `features` 预计算后注入，不让模型自己数
- **评分输出校验**：`rubric_version` 必须原样回显；四维 band 必须 0.5 步长；
  `matched_anchor` 为空视为无证据支撑的无效分
