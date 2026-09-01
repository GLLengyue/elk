# Changelog

本文件遵循 [Keep a Changelog](https://keepachangelog.com/) 格式。

## [0.2.0] — 2026-09-01

阅读题库建设 + 命题工具链沉淀。

### Added

**数据层**
- 阅读数据包 `reading-news-2026-08`：34 篇 / 442 题，全部基于 2026-06~08 公开新闻事实原创改写
- 命题 DSL 中间层（`_dsl/*.txt`）+ `build.py` 编译器：DSL 为唯一可编辑真相源，JSON 可完整重现
- 命题工具链 `capability/scripts/pack_authoring/`：`build.py` / `verify.py` / `regen.py` /
  `check_all.py` / `refresh_pack_json.py` / `build_index.py`（全零依赖、路径基于 `__file__`）
- 方法论文档 `capability/docs/pack-authoring.md` + 包内 DSL 速查 `BUILD.md`

### Changed

- `verify.py` 校验覆盖全部 34 篇，**0 错误 0 警告**
- `acceptable_answers` 契约统一：规范答案本身即「可接受答案」，由 `build.py` 编译时置首并入并去重
  （修复此前 41 篇含答案 / 61 篇不含的历史不一致）
- 24/34 篇保留可重生成 DSL 源；10 篇 JSON-native 为早期已复核文章（待回填 DSL）
- `regen.py` 全量重生成后，24 篇 DSL 与 JSON 零差异（可重现性验证通过）

### Fixed

- 命题硬伤修复（逐篇人工语义复核）：fusion Q2 答案 TRUE→FALSE 回写 DSL、insect-decline
  word_limit 二词→三词、carbon-credit MC 第二解排他、多处 EV 逐字/大小写/省略号修正
- 消除 DSL↔JSON 漂移（313 处 acceptable_answers 不一致 → 全量重生成归一化）

## [0.1.0] — 2026-08-30

首个公开版本。数据层骨架 + 能力层骨架 + 三个契约点。

### Added

**数据层**
- 9 份 JSON Schema 契约（`reading-test` / `speaking-topic` / `score-result` / `writing-{prompt,essay}` / `common/*`），支持跨文件 `$ref`
- 阅读 PDF → 结构化 JSON 的解析器两个：无障碍大字版三件套、分题型 task bank（学术 + 培训类双来源）
- 口语题库快照与换季基线
- 数据质量审计工具：分布形态、维度独立性、配对性三类判据
- evidence 算法式定位（词袋 + IDF 加权 + 必含词硬约束）
- 索引（`index.jsonl`）+ SQLite FTS5 全文检索

**能力层**
- 5 个版本化 prompt 模板：写作 score / tag-question-type，口语 score / generate-part3，阅读 label-evidence
- 2 份自述 paraphrase rubric：写作 Task 2（4 维）、口语（4 维）
- prompt 编译器：版本头强制校验、rubric 编译注入、运行留痕
- 三个契约点的可执行检查
- 统一 CLI：`bootstrap` / `check` / `index` / `validate` / `prompts` / `render` / `paths` / `fetch`

**数据包（Data Pack）**
- `elk load` / `unload` / `packs` / `pack` 四条命令
- `schemas/pack.schema.json` 清单契约：pack_id、licence、redistributable、
  内容统计、校验和
- 支持目录与 .zip 两种形态；多包按 pack_id 隔离共存
- 打包器 `elk pack` 自动扫描统计并生成清单

**工程**
- `scripts/setup.sh` 一键初始化、`scripts/check.sh` 门禁（可挂 git hook）
- `pyproject.toml` 依赖声明（核心依赖 5 个，无重型包）
- 合规文档：`.gitignore` 排除全部数据目录、`rubrics/_official-reference/` 版权说明

### Changed

- **数据改为加载模式**：移除随仓的合成示例数据，题目改由数据包提供。
  仓库不再假装自带题库，`examples/packs/demo-pack/` 仅作格式示范
- 源码从 `scripts/{build,fetch,parse,qc,eval}` 重组为 `src/elk/*` 包
- 路径解析统一收归 `elk/paths.py`，不再依赖目录层级推断（消除对本地路径的依赖）

### Fixed

- `scripts/setup.sh`：变量名 `$PYTHON_BIN` 紧跟中文右括号时，shell 会把多字节字符
  的字节并入变量名，在 `set -u` 下报 `unbound variable`。改用 `${PYTHON_BIN}` 明确边界。
- `.gitignore`：`build/` 会误伤 `src/elk/build/` 这个源码目录，限定为 `/build/`。
- `build_index`：索引路径改用 `paths.index_path()`，此前写在 `data/structured/` 下，
  与契约点 C 的比对位置不一致。
- `cli`：子命令调用需重置 `sys.argv`，否则父命令参数被子模块误解析。

### Security & Compliance

- 官方材料与其解析产物全部排除在版本库之外
- 所有结构化产物强制携带 `not_official: true`
- 随仓示例数据为凭空撰写的合成内容，无版权负担

### 已知限制

见 [MILESTONES.md](MILESTONES.md#已知限制01-未解决) 与 [docs/known-limitations.md](docs/known-limitations.md)。
