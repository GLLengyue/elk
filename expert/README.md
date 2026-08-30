# ELK 英语学习教练（自包含可分发包）

> 开箱即用的雅思备考助手，**完全自包含、可无限分发**。
> 不依赖任何本地项目路径、虚拟环境或环境变量——复制到任何装有 Python 3.10+ 的机器即可使用。

## 类型

Agent 型（单个 AI 专家）

## 定位

本专家包是 ELK 脚手架（考试题数据层 + LLM 能力层）的**自包含版本**。与早期版本不同，
本包不再指向任何外部仓库，而是把全部能力资产内置在包内：

| 资产 | 位置 | 说明 |
|---|---|---|
| JSON Schema 契约 ×10 | `skills/elk-coach/assets/schemas/` | pack / reading-test / writing-essay / speaking-topic / score-result 等 |
| 评分标准 rubric ×2 | `skills/elk-coach/assets/rubrics/` | writing-task2.v1 + speaking.v1（JSON 化） |
| Prompt 模板 ×5 | `skills/elk-coach/assets/prompts/` | writing/score、speaking/score、generate-part3、label-evidence、tag-question-type |
| 官方样题包 | `skills/elk-coach/assets/packs/reading-official-sample/` | 25 篇阅读真题 + 15 类题型 + 113 条口语话题（redistributable: true） |
| 零依赖核心 | `skills/elk-coach/scripts/elk_core.py` | 纯 Python 标准库（json/sqlite3/re/argparse/pathlib/hashlib） |

## 功能

1. **环境自检**：`elk_core.py check` — 5 项门禁（资产完整性/数据可解析/prompt 模板/rubric 结构/索引状态）
2. **题目检索**：`elk_core.py search <关键词>` — 内置 FTS5 全文检索（阅读主题 / 口语话题）
3. **评分锚点**：`elk_core.py features <题> <作文>` — 预计算客观量，不让 LLM 数数
4. **Prompt 渲染**：`elk_core.py render <rubric> <题> <作文>` — rubric 自动编译注入
5. **契约校验**：`elk_core.py validate <文件> <schema>` — 按内置 schema 校验数据
6. **数据扩充**：按 `pack.schema.json` 契约新增数据包目录即可扩展题库

## 使用示例

- 「帮我初始化学习环境并检查题库状态」→ `python3 scripts/elk_core.py check && index`
- 「从题库挑 3 篇阅读真题让我练」→ `python3 scripts/elk_core.py search "archaeology"`
- 「用评分标准给我的作文打分并给出改进建议」→ `features` → `render` → 按 rubric 判分

## 运行依赖

- **仅需 Python 3.10+**（任意安装方式，系统自带即可）
- **零第三方包**：无需 pip install、无需 .venv、无需环境变量
- 所有路径基于 `__file__` 相对解析，与所在机器/目录完全解耦

## 合规

- 内置样题包 `reading-official-sample` 在 `pack.json` 声明 `redistributable: true`
  （所有者筛选整理的合法数据，可随专家分发）
- `elk fetch` 抓取的官方源内容仅限个人使用、不入仓、不再发布

## 分发

复制整个 `elk-english-coach/` 目录（或打包 zip）到目标机器，放入专家目录并注册即可：

```bash
# 打包
zip -r elk-english-coach.zip elk-english-coach/

# 目标机器注册
python3 scripts/register_expert.py <expert-dir>
```

## 头像

头像已生成在 `avatars/` 目录下。如需替换：PNG/JPG，512×512 px，≤500KB。
