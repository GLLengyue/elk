# 三个契约点

架构约定写在文档里一定会漂移。所以这三个契约点都是**可执行检查**，
由 `python -m elk.build.check_contracts` 强制校验，`elk check` 会调用它。

---

## A. rubrics/ ↔ schemas/ — 维度命名必须一一对应

**检查什么**：每份 rubric 的每个维度，其 `schema_name` 必须出现在
`score-result.schema.json` 的 `criteria.items.properties.name.enum` 里。

### 它已经抓到过一次真问题

第一版跑这个检查时就失败了：

```
rubric 用简称：  TR / CC / LR / GRA
schema 用全称：  task_response / coherence_cohesion /
                lexical_resource / grammatical_range
```

两套命名并存，输出 `score-result` 的那一刻没人知道该写哪个。

**修法**：rubric 的每个 criterion 同时给出 `id`（内部简称）与 `schema_name`（对外全称），
由本检查强制校验。

### 当前状态

| rubric | schema_name |
|---|---|
| writing-task2.v1 | `task_response` / `coherence_cohesion` / `lexical_resource` / `grammatical_range` |
| speaking.v1 | `fluency_coherence` / `lexical_resource` / `grammatical_range` / `pronunciation` |

### 踩过的坑

第一版检查脚本只查 `$defs`，而 `score-result` 的 `criteria` 在**顶层 properties**，
结果什么都没找到、误报"契约破裂"。**两处都要查。**

---

## B. prompts/ ↔ structured/ — 输入字段必须能在 schema 找到

**检查什么**：每个 prompt 的 `input_contract` 里声明的字段，必须能在某份 schema 里找到。

### 为什么分 `input_contract` 与 `params`

第一版检查报 `speaking/generate-part3` 的 `n` 不在 schema 里。但 `n`（生成几问）
是**控制参数**，不是数据字段——它描述"怎么调用"而非"数据长什么样"。

所以 frontmatter 拆成两块：

```yaml
input_contract:      # 数据字段，参与校验
  topic_name: string
  cue_card: string
params:              # 控制参数，不校验
  n: int
```

---

## C. state/ ↔ index.jsonl — 条目数必须一致

**检查什么**：`index.jsonl` 的行数 == `items` 表行数 == `search`(FTS5) 行数。

三者不一致说明索引构建中途失败，或者数据改了但没重建索引。

### 踩过的坑

`build_index` 曾经把索引写到 `data/structured/index.jsonl`，
而 `paths.index_path()` 定义的是 `data/index.jsonl`——
结果契约点 C 永远对不上。**路径必须统一走 `paths.py`。**

---

## 跑检查

```bash
python -m elk.build.check_contracts -v
```

输出：

```
契约点 A　rubrics/ ↔ schemas/　维度命名
  [OK  ] A: writing-task2.v1 → ['task_response', 'coherence_cohesion', ...]
  [OK  ] A: speaking.v1 → ['fluency_coherence', 'lexical_resource', ...]

契约点 B　prompts/ ↔ structured/　输入字段
  [OK  ] B: writing-score.v1 输入字段 ['prompt_text', 'essay_text', 'features']
  ...

契约点 C　state/ ↔ index.jsonl　条目一致
  [OK  ] C: index / items / search 均为 4 条

三个契约点全部成立
```

## 挂到 git hook

```bash
ln -s ../../scripts/check.sh .git/hooks/pre-commit
```
