# Milestones

本文件跟踪 ELK 的版本范围、已知限制与后续计划。

**核查方式**：所有验收标准都设计成可执行的——能跑命令验证，或有明确的数量指标。
不要写"性能有所提升"这种无法核查的描述。

---

## 0.1.0 — 骨架（已发布 · 2026-08-30）

### 已完成范围

#### 数据层

| 项 | 状态 | 验收标准（如何核查） |
|---|---|---|
| 9 份 JSON Schema 契约 | ✅ | `ls schemas/*.schema.json schemas/common/*.schema.json` 得 9 份；`elk check` 的"schema 自检"通过 |
| Schema 间 `$ref` 复用 | ✅ | `common/{span,provenance,difficulty,licence}` 被引用；故意改坏一处 `$id` 后 `elk check` 应报错 |
| 阅读 PDF → 结构化 | ✅ | `elk validate` 对解析产物 100% 通过。实测：无障碍大字版 3 篇完整真题 + 22 个题型样本 |
| 口语题库快照 | ✅ | 113 题组过 schema 校验 |
| 数据质量审计工具 | ✅ | `python -m elk.eval.audit_writing_alignment` 可复跑；已产出 chillies/btnotpt/hai2131 三份判决 |
| evidence 算法式定位 | ✅ | `python -m elk.qc.extract_evidence`；填空题准确率 97%（38/39） |
| 索引 + FTS5 检索 | ✅ | `elk index` 后 `data/index.jsonl` 与 `items` 表条目数一致（契约点 C）；实测 `MATCH` 查询可命中 |

#### 能力层

| 项 | 状态 | 验收标准 |
|---|---|---|
| 5 个版本化 prompt 模板 | ✅ | `elk prompts` 列出 5 个；`elk check` 的"prompt 版本头"通过 |
| 2 份自述 paraphrase rubric | ✅ | 写作 4 维 / 口语 4 维；不含任何官方原文（人工核查） |
| rubric 编译注入 prompt | ✅ | 不传 `RUBRIC` 渲染 `writing/score`，输出含全部 5 档 × 5 条判据且无残留 `{{...}}` 占位符 |
| 运行留痕 | ✅ | 每次渲染后 `runs/prompt-runs.jsonl` 新增一行，含 `prompt_version` + `rubric_version` + `input_sha256` |
| 三个契约点可执行检查 | ✅ | `python -m elk.build.check_contracts` 输出"三个契约点全部成立" |
| **数据包加载模式** | ✅ | `elk load <目录或 .zip>` 装载；多包按 `pack_id` 隔离共存；`elk packs` 列出、`elk unload` 卸载 |
| **数据包清单契约** | ✅ | `schemas/pack.schema.json`；`pack.json` 强制声明 `licence` 与 `redistributable`，加载时校验 |
| **打包器** | ✅ | `elk pack <源目录> --id <id>` 自动扫描统计并生成清单；`--zip` 产出压缩包 |
| 统一 CLI | ✅ | `elk bootstrap / check / index / validate / prompts / render / paths / load / unload / packs / pack / fetch` 全部可用 |
| 一键初始化 | ✅ | `./scripts/setup.sh` 在干净环境下从零跑通 |

#### 实测数据（本地私有，不入仓）

| 指标 | 数值 |
|---|---|
| 阅读结构化题目 | 149 道（academic 95 / general_training 54） |
| 阅读题型覆盖 | 15 种（官方 11 种主题型全覆盖） |
| evidence 覆盖 | 92%（137/149） |
| 口语题组 | 113 组 |
| 索引条目平均大小 | 87 token（目标 ~100） |

### 已知限制（0.1 未解决）

| # | 限制 | 影响 | 计划在 |
|---|---|---|---|
| L1 | **仓库不含题目数据** | 新克隆只有格式示范包（1 篇 + 5 题），不足以练习或评测 | 设计如此：题目由数据包提供，`elk load` 装载 |
| L2 | **不含 LLM 调用** | 需自行接入模型 | 设计如此；0.2 提供参考适配器 |
| L3 | **写作评分无可信真值** | 只能给参考区间，不能给确定分数 | 依赖外部真值出现；0.3 建人工标定集 |
| L4 | **发音需音频才可评** | 无音频时输出 `unavailable` | 设计如此 |
| L5 | **无合成题生成**（已部分缓解） | 0.1 仅靠抓取公开源；现已新增 **DSL 原创命题流水线** `capability/scripts/pack_authoring/`，可产出 synthetic 阅读题（首包 34 篇/442 题已交付）。仍无"从母本自动生成"的批量管线 | 0.2 |
| L6 | **paraphrase 全空** | 同义替换未沉淀 | 0.2 |
| L7 | **task bank 正文未切段落** | Matching Headings 类题型的段落标签缺失 | 0.2 |
| L8 | **无 git 版本库的历史** | 0.1 是首次提交，无增量历史 | 设计如此 |
| L9 | **OCR 路线对官方手写体无效** | 官方 sample scripts 正文是手写扫描件，macOS Vision 识别不可用 | 低优先级，暂无计划 |

---

## 0.2.0 — 生产化（计划 · 排期 3–4 周）

主题：**从"能跑通"到"能产出"**。

| # | 目标 | 优先级 | 验收标准（可核查） |
|---|---|---|---|
| F1 | 合成题生成 pipeline | **P0** | 从 CC-BY 母本生成 ≥200 道题；每道题过 `reading-test.schema.json`；`elk check` 全通过 |
| F2 | paraphrase 沉淀 | **P0** | 已有 149 道题的 paraphrase 覆盖率 ≥80%；每条 `{from,to,kind}` 且 `kind` 在四类中 |
| F3 | QC 关卡 G1–G3 | **P0** | G1 唯一性：合成题与已有题的 8-gram 重叠率 >60% 时判为重复；G2 可解性：盲解正确率 <30% 或 >95% 的题目进隔离区；G3 证据定位：evidence 覆盖率 100% |
| F4 | 修 task bank 段落切分 | P1 | GT 与学术 task bank 的 passage 段落数 ≥2；`word_count` 与实际段落文本一致 |
| F5 | 数据包版本兼容检查 | P1 | 加载时校验 `schema_version`；不兼容时给出明确升级指引而非报错崩溃 |
| F6 | LLM 适配器参考实现 | P1 | `examples/llm_adapter.py` 提供 OpenAI / Anthropic 两个后端的最小实现；`render` 输出可直接传入 |
| F6 | 写作 task1 rubric | P2 | 新增 `rubrics/writing-task1.v1.yaml`，4 维对齐 schema 枚举；契约点 A 通过 |
| F7 | 单元测试覆盖核心模块 | P1 | `pytest` 覆盖 `paths` / `build/*` / `qc/*`；覆盖率 ≥60% |

**0.2 的完成判据**：`elk check` 全绿 **且** 合成题数 ≥200 **且** paraphrase 覆盖 ≥80% **且** 至少 2 个不同来源的数据包可并存加载。

### 阅读题库已交付（2026-09-01）

- 数据包 `reading-news-2026-08`：**34 篇 / 442 题**，基于 2026-06~08 公开新闻事实原创改写（synthetic 初稿 + 逐篇人工复核）
- 命题流水线落地：`build.py`（DSL→JSON）/ `verify.py`（0 错误 0 警告）/ `regen.py`（DSL 全量重现）
- 24/34 篇保留可重生成 DSL 源；10 篇 JSON-native 待回填 DSL
- 详见 `capability/docs/pack-authoring.md` 与包内 `BUILD.md`

---

## 0.3.0 — 质量闭环（计划 · 排期 4–5 周）

主题：**能分辨好题坏题**。

| # | 目标 | 优先级 | 验收标准 |
|---|---|---|---|
| F8 | QC 七关全量（G1–G7） | **P0** | 每关有独立可跑的脚本；`elk check --full` 串行跑完七关并输出关卡通过率 |
| F9 | 人工标定集（写作） | **P0** | ≥50 篇由人工按 rubric 打分的作文；可复跑 QWK / Pearson 评估（`elk.eval.eval_qwk`） |
| F10 | 检索增强 | P1 | 支持语义检索后端（可插拔，默认仍是 FTS5）；给定主题词返回 top-10 且人工判定相关性 ≥70% |
| F11 | 换季 diff（口语） | P1 | 两个季度的快照对比，输出新增/移除题组；按考区分开（已知题库区分大陆/海外） |
| F12 | 评分一致性回归 | P1 | 同一批输入跑 3 次，四维分数的标准差 ≤0.5 档；留痕文件可完整复现 |
| F13 | rubric 版本对比 | P2 | 同一批作文用两个 rubric 版本打分，输出逐维差异报告 |

**0.3 的完成判据**：七关全量可跑 **且** 人工标定集 ≥50 篇 **且** 评分一致性标准差 ≤0.5 档。

---

## 1.0.0 — 稳定（计划 · 排期 6–8 周）

主题：**API 稳定 + 第二个实例证明通用性**。

| # | 目标 | 优先级 | 验收标准 |
|---|---|---|---|
| F14 | 冻结公开 API | **P0** | `elk.*` 的公开函数签名在 1.x 内不破坏；有 `docs/api.md` 列出全部稳定接口 |
| F15 | 第二个科目实例 | **P0** | 接入一个非雅思的科目（如托福/考研英语），复用同一套 schema 抽象与契约点；`elk check` 通过 |
| F16 | 完整文档站 | P1 | `docs/` 覆盖架构、契约、流水线、API、FAQ；`mkdocs build` 无警告 |
| F17 | 合成题达到 1000 道 | P1 | 通过 QC 的 `passed` 题 ≥1000；`quarantine` 比例 ≤20% |
| F18 | 性能基线 | P2 | 1000 道题全量重建索引 <60s；单次 prompt 渲染 <100ms |
| F19 | 合规自动化检查 | P1 | CI 检查所有产物带 `not_official` 与 `redistributable` 字段；`raw/` 下无被追踪文件 |

**1.0 的完成判据**：API 冻结文档发布 **且** 第二实例跑通 **且** passed 题 ≥1000。

---

## 排期说明

| 版本 | 相对时间 | 主要风险 |
|---|---|---|
| 0.1 | 已发布 | — |
| 0.2 | +3–4 周 | 合成母本的版权筛选；生成质量不达标导致大量进隔离区 |
| 0.3 | +7–9 周 | 人工标定集需要持续投入（50 篇 × 约 15 分钟 = 12.5 小时） |
| 1.0 | +13–17 周 | 第二实例的 schema 抽象可能需要重构现有契约 |

> ⚠️ **待确认**：以上排期基于"每周投入 8–12 小时的兼职节奏"估算，未与任何外部用户或贡献者对齐。
> 实际推进时会根据反馈调整优先级，版本范围也可能重新切分。
> 请以 GitHub Milestone 页面的实时状态为准。
