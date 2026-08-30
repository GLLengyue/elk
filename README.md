<div align="center">

# ELK

**考试题数据层 + LLM 能力层的开源脚手架**

首个完整实例：雅思（IELTS）

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/dependencies-zero%20(stdlib%20only)-brightgreen.svg)](#设计原则)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

*把可复现的做厚，把不可复现的做薄，用可执行检查把两者焊死。*

</div>

---

## 它解决什么问题

搭"AI 辅助备考系统"时，大多数人先去找数据集。我们也是，然后在两周内撞了三次墙：

**痛点一：公开数据集的标注不可信，但你很难发现。**

我们审计了三个主流雅思写作数据集，结论是**四维分数全部不可用**：

| 数据集 | 症状 |
|---|---|
| chillies (8,049 篇) | GRA 维度 **31% 是满分**（真实分布应 <1%）；CC 从 8.0 直接跳 9.0，中间无 8.5 |
| btnotpt (10,219 篇) | overall 可被四维均值 **100% 还原** —— 四维是由总分反推的 |
| hai2131 (13,925 篇) | 四维两两相关 r ≈ 0.9，CC/LR/GRA 有 55–62% 取值完全相同 |

最致命的交叉证据：chillies 里 TR=9.0 的样本中，**overall=6.0 的有 171 篇**。
满分维度配 6 分总分，在一篇作文里不可能。

用这些数据集做评分回归，等于**用噪声验证噪声**。

**痛点二：prompt 散落在代码里。** rubric 更新了，prompt 还是旧的；
输出结构变了，schema 没跟着变。症状出现在很远的下游，最难查。

**痛点三：元数据不沉淀。** 同一道题问两次，"证据句"不一样。无法回归，无法校验。

**ELK 的做法**：把可复现的部分（schema 契约、rubric、QC、检索索引）做厚，
把不可复现的部分（具体的题）做薄，用**可执行检查**焊死。

---

## 设计原则

| 原则 | 含义 |
|---|---|
| **零依赖运行时** | 专家包内核心逻辑只用 Python 标准库（json / sqlite3 FTS5 / re / argparse）。不含 pandas、torch、duckdb、PyYAML |
| **自包含分发** | 专家包携带全部资产（schema / rubric / prompt / 数据包）。复制到任何 Python 3.10+ 机器即可运行，无需 pip install、无需 .venv、无需环境变量 |
| **路径相对化** | 所有路径基于 `__file__` 解析，不含任何机器相关硬编码 |
| **薄 harness + 厚数据** | 契约与 QC 做厚，题目做薄；题目由可替换的数据包提供 |
| **LLM 不做精确计数** | 词数、重叠率、抄题跨度等一律脚本预计算后注入，不让模型自己数 |
| **不编造分数** | 写作/口语无可信公开真值，只输出 `band_range` 参考区间 + 命中的 rubric 判据 |

---

## 快速开始

### 用专家包（推荐，零安装）

```bash
# 1. 下载分发包并解压
unzip elk-english-coach-v1.0.0-*.zip

# 2. 直接跑，无需任何安装
python3 elk-english-coach/skills/elk-coach/scripts/elk_core.py check
python3 elk-english-coach/skills/elk-coach/scripts/elk_core.py index
python3 elk-english-coach/skills/elk-coach/scripts/elk_core.py search "archaeology"
```

### 从源码开发

```bash
git clone git@github.com:GLLengyue/elk.git && cd elk

# 能力层（带 PyYAML，用于把 rubric 转成 JSON）
cd capability && python3 -m venv .venv && source .venv/bin/activate
pip install -e .          # 国内慢可加 -i https://pypi.tuna.tsinghua.edu.cn/simple

# 回到根目录：同步资产 → 打包专家包
python3 scripts/release.py --install
```

---

## 命令一览

核心脚本：`expert/skills/elk-coach/scripts/elk_core.py`

| 命令 | 用途 |
|---|---|
| `check` | 5 项门禁自检（资产完整性 / 数据可解析 / prompt 模板 / rubric 结构 / 索引状态） |
| `index` | 重建 FTS5 全文检索索引 |
| `search <关键词> [--limit N]` | 检索阅读真题 / 口语话题 |
| `items` | 列出全部阅读题 id、题量、词数 |
| `render-reading <id> [--out x.html]` | 生成**米色护眼**的阅读练习页（自包含单文件 HTML） |
| `features <题> <作文>` | 计算评分客观锚点（词数/段落/抄题跨度/TTR/concept_hit） |
| `render <rubric> <题> <作文>` | 编译评分 prompt，rubric 自动注入 |
| `validate <文件> <schema>` | 按内置 JSON Schema 校验数据 |
| `prompts` / `paths` | 列出 prompt 模板 / 打印资产路径 |

---

## 仓库结构

```
ELK/
├── capability/            # 能力层（开发源头，可带依赖）
│   ├── schemas/           # 10 个 JSON Schema 契约
│   ├── rubrics/           # writing-task2.v1 + speaking.v1（YAML 源）
│   ├── prompts/           # prompt 模板（含 frontmatter 版本头）
│   ├── packs/             # 正式数据包
│   └── src/elk/           # Python 实现（cli / scorer / build / fetch / parse / qc）
├── expert/                # 专家包源（分发的形态）
│   ├── agents/            # 专家人设与工作流
│   └── skills/elk-coach/
│       ├── assets/        # ← 由 sync 脚本从 capability 注入（已 JSON 化）
│       ├── scripts/elk_core.py   # 零依赖核心
│       └── templates/reading.html # 阅读页模板（米色护眼）
├── doc/
│   └── media-sources.md   # 素材合规清单（哪些能改写、哪些不能）
├── scripts/
│   ├── sync_assets.py     # capability → expert 资产同步（YAML→JSON）
│   ├── release.py         # 校验 + 打包可分发 zip
│   └── fetch_source.py    # 抓取权威公开素材（仅落本地）
├── data/                  # 数据层（raw/ 被 gitignore，永不入库）
└── dist/                  # release 产物
```

**为什么 capability 和 expert 分开？**
能力层是开发源头（可以带依赖、可以跑实验），专家包是分发形态（必须自包含零依赖）。
两者之间靠 `sync_assets.py` 单向同步——没有这条管道，就会出现
"仓库改了但发出去的包还是旧的"。

---

## 题库扩充

抓权威公开媒体 → LLM 按雅思题型命题 → schema 校验 → 入库：

```bash
# 1. 看哪些来源合规
python3 scripts/fetch_source.py --list

# 2. 抓原文（只落 data/raw/，gitignore 排除，不入库）
python3 scripts/fetch_source.py "<url>"

# 3. 用 prompt 模板让 LLM 命题（paraphrase 成 700-1000 词考试体，不照抄）
#    → capability/prompts/reading/generate-from-source.v1.md

# 4. 校验结构
python3 expert/skills/elk-coach/scripts/elk_core.py validate <file> reading-test

# 5. 重新打包
python3 scripts/release.py
```

合规红线见 [`doc/media-sources.md`](doc/media-sources.md)。
**关键**：BBC / Guardian / Economist / Nature 等全版权来源禁止改写入库；
The Conversation 是 CC BY-**ND**，ND 明确禁止演绎，同样不可用。

---

## 开发

```bash
# 资产是否过期（CI 可用）
python3 scripts/sync_assets.py --check

# 完整发布：同步 → 冒烟自检 → 打包
python3 scripts/release.py

# 打包并装到本地专家目录（开发时验证用）
python3 scripts/release.py --install
```

### 发布流程（WorkBuddy 专家）

本仓库同时是 WorkBuddy 专家包的源。完整链路：

```bash
python3 scripts/release.py --install
# 等价于：sync_assets → elk_core check → zip → 解压到本地专家目录
```

若需重新注册到专家中心：

```bash
python3 <expert-manager>/scripts/validate_expert.py expert/
python3 <expert-manager>/scripts/register_expert.py expert/ --session-id <id>
```

---

## 路线图

- [x] 自包含零依赖核心（stdlib only）
- [x] FTS5 检索、评分锚点、rubric 编译注入
- [x] 米色护眼阅读练习页模板
- [x] 素材抓取链路（合规来源清单 + fetch 脚本）
- [x] git 项目结构 + release 打包脚本
- [ ] 命题 QC 关卡：答案唯一性、干扰项有效性、证据可定位性
- [ ] 难度自动分级（CEFR / 蓝思值）
- [ ] 写作/口语练习页模板（复用米色护眼设计语言）
- [ ] 错题本与练习记录持久化
- [ ] 更多合规数据包（CC BY 学术期刊、Our World in Data 等）

---

## 许可

代码 **MIT**（见 [LICENSE](LICENSE)）。

**数据包单独声明许可**：见各 `packs/*/pack.json` 的 `licence` 与
`redistributable` 字段。`redistributable: false` 的包可本地加载，但不得再分发。

抓取的原文存放在 `data/raw/`，被 `.gitignore` 排除，**从不入库**。

---

<div align="center">

<sub>Built with the belief that a study tool should be reproducible, auditable,
and honest about what it cannot know.</sub>

</div>
