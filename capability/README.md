# SKILL

> 考试题**数据层** + LLM **能力层**的开源脚手架。
> 首个完整实例是雅思（IELTS），但结构是通用的。

**当前版本：0.1.0（Alpha）** · 许可：[MIT](LICENSE)

> **ELK 本身不含任何题目数据。** 代码与题目完全解耦：题目由**数据包**（Data Pack）
> 提供，通过 `elk load` 装载。这样同一套代码既能跑非商用样题，也能跑正式授权数据，
> 组织方式完全一致。详见 [§5 数据从哪来](#5-数据从哪来)。

---

## 1. 它解决什么问题

搭一个"AI 辅助备考系统"时，大多数人会先去找数据集。我们也是这么开始的，然后在两周内撞了三次墙：

**痛点一：公开数据集的标注不可信，但你很难发现。**

我们对三个主流的雅思写作数据集做了审计，结论是**四维分数全部不可用**：

| 数据集 | 症状 |
|---|---|
| chillies (8,049 篇) | GRA 维度有 **31% 是满分**（真实分布应 <1%）；CC 从 8.0 直接跳到 9.0，中间没有 8.5 |
| btnotpt (10,219 篇) | overall 可被四维均值 **100% 还原** —— 四维是由总分反推的 |
| hai2131 (13,925 篇) | 四维两两相关系数 r ≈ 0.9，且 CC/LR/GRA 有 55–62% 取值完全相同 |

最致命的一条交叉证据：chillies 里 TR=9.0 的样本中，**overall=6.0 的有 171 篇**。满分维度配 6 分总分，在一篇作文里不可能。

这意味着：用这些数据集做评分回归，等于**用噪声验证噪声**。

**痛点二：prompt 散落在代码里，改一处忘了另一处。**

评分标准（rubric）更新了，但 prompt 里还是旧的；输出结构变了，schema 没跟着变。这类不一致最难查，因为症状出现在很远的下游。

**痛点三：元数据（证据、同义替换）不沉淀，每次让模型临场编。**

同一道题问两次，得到的"证据句"不一样。无法回归，无法校验。

**ELK 的做法**：把可复现的部分（schema 契约、rubric、QC、索引检索）做厚，把不可复现的部分（具体的题）做薄，并用**可执行检查**把两者焊死。

---

## 2. 目标用户

> ⚠️ **待确认**：以下三類是本项目基于自身使用场景做的假设，尚未经过外部用户验证。
> 如果你的用法不在其中，欢迎开 issue 告诉我们——这会影响 0.2 的优先级排序。

1. **想自建考试练习/评测系统的开发者** —— 需要一套能分辨"好题坏题"的 QC 关卡，而不是一堆题。
2. **研究 LLM 评分/命题一致性的人** —— 需要可复现的实验装置：`rubric_version` + `prompt_version` + 输入 hash 全部留痕。
3. **自学者** —— 想把自己的题库、评分标准、练习记录管起来，而不是散在几个文件夹里。

**不适合的场景**：想要一个开箱能用的"AI 打分 App"（本项目不含任何 LLM 调用，见 §7）；想要现成的大规模题库（本仓库**刻意不含任何真实考试材料**，见 §9）。

---

## 3. 核心特性

| 特性 | 说明 |
|---|---|
| **9 份 JSON Schema 作为唯一契约** | 所有结构化数据写入前强校验；schema 之间用 `$ref` 复用 |
| **自述 paraphrase 的 rubric** | 写作 4 维 / 口语 4 维，每档 3–5 条**可观测**判据。不含任何官方原文 |
| **版本化 prompt 模板** | 文件头强制声明 `version` / `purpose` / `input_contract` / `output_contract` |
| **rubric 编译注入 prompt** | 不硬编码。改 rubric 后 prompt 自动同步，杜绝两边漂移 |
| **三个契约点 + 可执行检查** | rubrics↔schemas、prompts↔structured、state↔index，逐条强制校验 |
| **索引 + FTS5 全文检索** | DB 承担"找题"，LLM 只承担"用题"。索引条目约 87 token |
| **数据质量审计工具** | 分布形态、维度独立性、配对性三类判据，已抓出三个公开数据集的问题 |
| **evidence 算法式定位** | 零成本给题目补证据句；填空题准确率 97% |
| **一次留痕** | 每次渲染 prompt 记录 `prompt_version` + `rubric_version` + 输入 sha256 |
| **合规内建** | 官方材料永不入库；产物强制 `not_official: true` |
| **数据加载模式** | 题目由数据包提供，`elk load` 装载；支持目录与 .zip，多包共存、可卸载 |
| **数据包清单契约** | `pack.json` 声明许可、`redistributable`、内容统计与校验和，加载时强制校验 |

---

## 4. 目录结构

```
SKILL/
├── README.md              本文件
├── MILESTONES.md          0.1 已完成范围 / 0.2–1.0 路线图与验收标准
├── CONTRIBUTING.md        贡献指南
├── CHANGELOG.md           版本历史
├── LICENSE                MIT
│
├── src/elk/             ── 源码
│   ├── paths.py           统一路径解析（不依赖本地特有路径）
│   ├── cli.py             命令行入口：bootstrap / check / index / render …
│   ├── build/             索引、契约检查、schema 校验、prompt 编译器
│   ├── fetch/             官方公开源抓取（仅落本地）
│   ├── parse/             PDF → 结构化 JSON
│   ├── qc/                质量关卡（evidence 定位等）
│   └── eval/              数据审计、回归、一致性评估
│
├── schemas/               ── 契约（唯一真相来源）
│   ├── reading-test.schema.json
│   ├── speaking-topic.schema.json
│   ├── score-result.schema.json
│   ├── writing-{prompt,essay}.schema.json
│   └── common/{span,provenance,difficulty,licence}.schema.json
│
├── rubrics/               ── 评分标准（自述 paraphrase，可入仓）
│   ├── writing-task2.v1.yaml
│   ├── speaking.v1.yaml
│   └── _official-reference/   官方原文存放处（禁止入库，见其中 README）
│
├── prompts/               ── 能力模板（可入仓）
│   ├── writing/{score,tag-question-type}.v1.md
│   ├── speaking/{score,generate-part3}.v1.md
│   └── reading/label-evidence.v1.md
│
├── config/                ── 配置
│   └── config.example.yaml
│
├── packs/                 ── 数据包存放处（.gitignore 排除，正式数据不入库）
│   └── reading-official-sample/
│
├── examples/              ── 示例
│   ├── make_sample_data.py     生成格式示范数据包
│   └── packs/demo-pack/        格式示范包（演示数据包长什么样，非练习内容）
│
├── docs/                  ── 文档
│   ├── architecture.md         分层架构与三个契约点
│   ├── contracts.md            契约点详解
│   ├── data-pipeline.md        数据流水线
│   ├── known-limitations.md    已知限制（重要）
│   └── compliance.md           版权与合规红线
│
├── scripts/               ── 启动脚本
│   ├── setup.sh                一键初始化
│   └── check.sh                门禁（可挂 git hook / CI）
│
└── data/                  ── 数据（默认被 .gitignore 排除，bootstrap 重建）
```

---

## 5. 数据从哪来

**题目不在仓库里，在数据包里。**

这是一个刻意的架构选择，理由有两个：

1. **合规** —— 真实考试材料受版权约束，不能随代码分发。
2. **数据与能力解耦** —— 同一套代码，换一个数据包就能换一批题。
   非商用样题与正式授权数据走**完全相同**的组织方式，替换时不用改任何代码。

### 数据包长什么样

```
my-pack/
├── pack.json         清单（必须），契约见 schemas/pack.schema.json
├── SOURCES.md        来源清单（推荐）
├── LICENSE           数据包自身许可（推荐）
└── data/
    ├── reading/**/*.json     符合 reading-test.schema.json
    ├── speaking/**/*.jsonl   符合 speaking-topic.schema.json
    └── writing/**/*.jsonl    符合 writing-*.schema.json
```

`pack.json` 至少要有这些字段：

```json
{
  "pack_id": "my-pack",
  "pack_version": "1.0.0",
  "schema_version": "1.0.0",
  "modules": ["reading"],
  "licence": {"name": "CC-BY-4.0", "redistributable": true},
  "redistributable": true,
  "contents": {
    "counts": {"reading_items": 25, "reading_questions": 149}
  }
}
```

`redistributable: false` 的包（例如付费数据集）照常能加载，
但 `elk packs` 会显示 **否 ⚠**，避免有人误把它当可公开产物。

### 装填数据

```bash
elk load packs/my-pack          # 目录
elk load packs/my-pack.zip      # 或压缩包，自动识别
elk packs                       # 看已装了哪些
elk unload my-pack              # 卸载
```

加载时会做三件事：校验 `pack.json` → 复制进 `data/structured/<pack_id>/` → 重建索引。
多个包按 `pack_id` 隔离，可以共存。

### 自己做数据包

```bash
elk pack ./my-structured-data \
    --id my-pack --version 1.0.0 \
    --licence "CC-BY-4.0" --redistributable \
    --out packs/
```

打包器会自动扫描、统计、生成清单。加 `--zip` 额外产出压缩包便于分发。

### 随仓的格式示范包

`examples/packs/demo-pack/` 是一个**格式示范**，凭空撰写、无版权负担。
它只有 1 篇短文 + 5 题，用来证明加载/校验/索引三步能跑通。

> ⚠️ **它不是练习内容**，样本量毫无意义。 bootstrap 会装上它，
> 只是为了让你克隆后能立刻验证链路。

---

## 6. 环境要求

| 项 | 要求 |
|---|---|
| Python | **≥ 3.10**（代码用了 `X \| None` 与内置泛型） |
| 操作系统 | macOS / Linux / Windows（路径由 `pathlib` 处理，无平台相关代码） |
| 磁盘 | 最小安装 ~50MB；抓取官方源后约 +40MB |
| 网络 | **仅抓取数据时需要**。安装与示例运行全程离线 |
| 外部服务 | **无**。本项目不调用任何 LLM API |

依赖刻意保持精简（无 pandas / torch）：
`duckdb` · `jsonschema` · `pypdf` · `PyYAML` · `referencing`

---

## 7. 安装与快速开始

```bash
git clone <your-repo-url> SKILL
cd SKILL
./scripts/setup.sh
```

`setup.sh` 会建虚拟环境、装依赖、建目录骨架、装填示例数据并建索引。

> **网络较慢时**（例如 PyPI 直连超时），先设镜像源再跑：
>
> ```bash
> export PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
> ./scripts/setup.sh
> ```
>
> `pip` 会自动读取这个环境变量，`setup.sh` 本身不需要改。

想手动装也行：

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
elk bootstrap
```

此时只装了格式示范包。加载你自己的数据包：

```bash
elk load <你的数据包路径>     # 目录或 .zip 均可
elk packs                    # 确认已加载
```

验证安装：

```bash
elk check
```

预期输出：

```
检查项             结果
--------------------------------------------------------
  契约点 A/B/C     通过
  prompt 版本头    通过
  schema 自检     通过
  阅读数据          通过
  口语数据          通过
--------------------------------------------------------
全部 5 项通过
```

---

## 8. 基础用法

### 8.1 装载数据包

```bash
elk load packs/my-pack      # 目录或 .zip
elk packs                   # 已加载清单
elk unload my-pack          # 卸载
```

### 8.2 打包自己的数据

```bash
elk pack ./my-data --id my-pack --version 1.0.0 --out packs/
```

### 8.3 看有哪些 prompt

```bash
elk prompts
```

```
writing-score.v1                     prompts/writing/score.v1.md
writing-tag-question-type.v1         prompts/writing/tag-question-type.v1.md
speaking-score.v1                    prompts/speaking/score.v1.md
speaking-generate-part3.v1           prompts/speaking/generate-part3.v1.md
reading-label-evidence.v1            prompts/reading/label-evidence.v1.md
```

### 8.4 渲染一个 prompt（看最终喂给模型的内容）

```bash
elk render writing/tag-question-type \
  --set PROMPT_TEXT="Some people think that economic progress is the most important goal. Discuss both views and give your own opinion."
```

rubric 会自动编译注入，不用手工传。

### 8.5 用代码调用（这才是主要用法）

```python
from elk.build.prompt_loader import render

prompt = render(
    "writing/score",
    PROMPT_TEXT="Some people think ... Discuss both views and give your own opinion.",
    ESSAY_TEXT="People have different views on how progress should be measured ...",
    FEATURES={                      # 客观锚点，预先算好再注入
        "word_count": 303,
        "paragraph_count": 4,
        "max_copied_span": 2,
        "concept_hit": 0.71,
        "type_token_ratio": 0.52,
    },
)

# prompt 现在是完整的字符串，交给你自己选用的 LLM SDK
response = your_llm_client.complete(prompt)
```

**为什么让客观锚点预先算好再注入？** 因为 LLM 不擅长精确计数——同一篇问两次，词数和错误数可能不同。先算好，模型只做判断不做统计，一致性显著提升。

### 8.6 校验自己的数据

```bash
elk validate "data/structured/reading/**/*.json" --schema reading-test
```

### 8.7 抓取官方公开样题（可选）

```bash
elk fetch --only reading
```

抓到的文件**只落本地 `data/raw/`**，不会进版本库。

### 8.8 重建索引

```bash
elk index
```

---

## 9. 已知限制

> 完整列表见 [docs/known-limitations.md](docs/known-limitations.md)。以下是最重要的五条：

1. **本仓库不含任何题目数据。** 题目由数据包提供（见 [§5 数据从哪来](#5-数据从哪来)）。
   随仓的 `examples/packs/demo-pack/` 是**格式示范**，凭空撰写、只有 1 篇 + 5 题，
   用于验证加载链路，**不是练习内容**。真实题目需要你自备数据包或自行抓取。

2. **不含 LLM 调用。** 本项目只负责 prompt 的编译、渲染与留痕，实际调用由你的代码完成。这是刻意设计——不绑定任何厂商。

3. **写作评分没有可信真值。** 三个公开数据集的四维分数经审计全部不可用（见 §1）。因此评分 prompt **输出参考区间（band_range）而非单点分数**，并强制要求每条判断引用 rubric 中的具体判据。声称"这个 6.5 分就是考官会给的分数"是不诚实的。

4. **发音维度在无音频时输出 `unavailable`。** ASR 转写丢弃声学特征（语调/重音/连读/音准），从文本推断发音分数没有信度。给一个编造的发音分比不给更糟。

5. **0.1 未做合成题生成。** 数据流水线目前是"抓取 → 解析 → 校验 → 索引"，还没有"生成"。这是 0.2 的主要内容。

---

## 10. FAQ

**Q：为什么叫 SKILL？**
A：最初的设想是把系统拆成"数据"+"SKILL 提示词流程"两部分，后者是能力层的名字。名字本身没有特殊含义。

**Q：仓库里为什么没有题？**
A：合规 + 架构。详见 [docs/compliance.md](docs/compliance.md) 与 [§5 数据从哪来](#5-数据从哪来)。
简单说：真实考试材料受版权约束，不能随代码分发；同时把题目做成可替换的数据包，
同一套代码能跑不同来源的数据。

**Q：数据包能自己打包吗？**
A：能，`elk pack <源目录> --id <id> --out packs/`。打包器会自动扫描统计并生成清单。

**Q：付费数据包会被别人拷走吗？**
A：本项目不做加密或授权控制——那是数据包提供方的事。`pack.json` 里
`redistributable: false` 只是**声明**，加载器会在 `elk packs` 里显著标注，
防止误用，但不构成技术保护。如需保护请自行在数据包层面实现。

**Q：换数据包要改代码吗？**
A：不用。数据包之间组织方式完全一致，加载后自动进索引。

**Q：能直接用来备考吗？**
A：0.1 不能——它是给**搭系统的人**用的脚手架，不是给**考生**用的 App。但如果你想自己接一个 LLM 做练习，它的 rubric + prompt + 留痕机制可以直接借用。

**Q：为什么不内置 LLM 调用？**
A：三个理由：(1) 不绑定厂商；(2) 密钥管理不该由库负责；(3) 便于离线测试——不调模型也能验证 prompt 渲染和契约。

**Q：schema 能改吗？**
A：能，但要三个契约点一起改。`elk check` 会拦住不一致的改动。建议先看 [docs/contracts.md](docs/contracts.md)。

**Q：数据目录能放到仓库外吗？**
A：可以，设环境变量 `SKILL_DATA_DIR`，或在 `config/config.yaml` 里配 `paths.data_dir`。详见 [docs/architecture.md](docs/architecture.md)。

**Q：为什么用 SQLite + FTS5 而不是向量检索？**
A：0.1 的检索需求是"按题型/类别/主题词找题"，关键词检索足够且零依赖。语义检索留到 0.3，届时会做成可插拔的检索后端。

---

## 11. 路线图

见 [MILESTONES.md](MILESTONES.md)。

- **0.1（当前）** —— 数据层骨架 + 能力层骨架 + 三个契约点
- **0.2** —— 合成题生成 pipeline + paraphrase 沉淀
- **0.3** —— QC 七关 + 检索增强
- **1.0** —— 稳定 API + 完整文档 + 第二个科目实例

---

## 12. 贡献指南

见 [CONTRIBUTING.md](CONTRIBUTING.md)。

简要版：

```bash
./scripts/check.sh          # 改动前后都要跑
```

提交信息请遵循 [Conventional Commits](https://www.conventionalcommits.org/)：

```
feat(parse): 支持 GT 阅读的两种答案行格式
fix(build): 修正索引路径与 paths.index_path 不一致
docs(readme): 补充已知限制
```

---

## 13. 许可证

[MIT](LICENSE)。

**注意**：本许可证只覆盖本仓库的**代码、schema、自述 rubric 与 prompt 模板**。
它**不覆盖**任何官方考试材料，也不赋予你分发后者的权利。
使用 `elk fetch` 获取的材料仅限个人非商业用途，且不得再发布。
详见 [docs/compliance.md](docs/compliance.md)。
