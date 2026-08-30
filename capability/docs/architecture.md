# 架构：能力层与数据层如何结合

## 一句话

**数据层负责"存什么、什么格式"，能力层负责"怎么用、怎么判、怎么找"，
两者由三个契约点焊死，由 `elk check` 强制校验。**

## 分层

```
┌─────────────────────────────┐        ┌──────────────────────────────┐
│ 能力层 Capability           │        │ 数据层 Data                  │
│                             │        │                              │
│ prompts/    怎么用          │  读取  │ schemas/    存什么格式       │
│ rubrics/    怎么判          │ ─────→ │ structured/ 成品             │
│ state/      怎么找          │        │ raw/ + interim/              │
└─────────────────────────────┘        └──────────────────────────────┘
              │                                      ↑
              │ 渲染                                 │ 构建
              ↓                                      │
         LLM（由你的代码调用）              fetch → parse → validate → index
```

## 三条设计约束

### 1. DB 找题，LLM 用题

LLM 绝不扫描文件。检索由 `state/elk.db` 的 FTS5 承担：

```python
# 找题：SQL，零 token
c.execute("SELECT id FROM search WHERE search MATCH ?", ("archaeology",))

# 用题：读命中的那一条，注入 prompt
```

149 道题靠遍历还能忍，到 1000 道时遍历就是在烧 token。

### 2. 索引条目要小

`index.jsonl` 每条约 **87 token**（目标 ~100），只放够定位的字段：
`id / kind / module / title / types / q_count / word_count / path`。

**不含正文**——含了就失去"精简"的意义（单篇 6k token vs 索引 87 token）。

### 3. rubric 编译进 prompt，不硬编码

```python
render("writing/score", PROMPT_TEXT=..., ESSAY_TEXT=..., FEATURES={...})
```

`{{RUBRIC}}` 由 `rubrics/*.yaml` 现场渲染。改 rubric 后 prompt 自动同步，
杜绝"rubric 更新了但 prompt 是旧的"这种最难查的不一致。

## 路径解析

所有位置由 `src/elk/paths.py` 统一管理，**不依赖目录层级推断**：

| 函数 | 返回 | 可被环境变量覆盖 |
|---|---|---|
| `repo_root()` | 静态资源根（schemas/rubrics/prompts） | `SKILL_ROOT` |
| `data_root()` | 数据根，默认 `<repo>/data` | `ELK_DATA_DIR` |
| `structured_dir()` / `index_path()` / `db_path()` | 具体位置 | 同上 |

把数据放到仓库外：

```bash
export ELK_DATA_DIR="/Volumes/External/elk-data"
elk bootstrap
```

或在 `config/config.yaml` 里配 `paths.data_dir`。

## 分科不是一刀切

不同科目的"厚"在不同地方：

| 科目 | 厚 | 薄 | 能力层重点 |
|---|---|---|---|
| 阅读 | harness（QC 关卡） | 数据 | QC prompt + 合成 |
| 写作 | 数据 | harness | rubric + score prompt |
| 口语 | 数据（题库） | harness | generate / score prompt |

所以不要用同一个 KPI 衡量三个科目。阅读的 KPI 是"QC 免人工通过率"，
不是"生成了多少道题"。

## 目录布局的取舍

0.1 采用**仓库根目录即资源目录**：`schemas/` `rubrics/` `prompts/` 都在顶层。

- 优点：clone 即可用，改 rubric 不用找包路径
- 缺点：`pip install` 后这些资源需要单独携带（0.2 会改为随包分发）

详见 [MILESTONES.md](../MILESTONES.md) 的 F19。
