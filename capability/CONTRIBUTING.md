# 贡献指南

感谢你愿意花时间。这份文件说明怎么改动才不会踩雷。

## 先看这三份文档

1. [docs/architecture.md](docs/architecture.md) —— 分层架构与三个契约点
2. [docs/contracts.md](docs/contracts.md) —— 契约点详解（改动 schema 前必读）
3. [docs/compliance.md](docs/compliance.md) —— 版权红线（**最重要**）

## 开发循环

```bash
./scripts/setup.sh          # 首次
source .venv/bin/activate

# 改代码 ...

./scripts/check.sh          # 门禁，必须全绿
```

`check.sh` 等价于 `elk check`，跑五项：契约点 A/B/C、prompt 版本头、schema 自检、数据校验。
CI 与 git hook 用的都是它。

## 提交信息

遵循 [Conventional Commits](https://www.conventionalcommits.org/)：

```
<type>(<scope>): <subject>
```

`type`：`feat` / `fix` / `docs` / `refactor` / `test` / `chore` / `perf`
`scope`：`build` / `parse` / `fetch` / `qc` / `eval` / `schema` / `rubric` / `prompt` / `docs`

示例：

```
feat(parse): 支持 GT 阅读的两种答案行格式
fix(build): 修正索引路径与 paths.index_path 不一致
docs(milestones): 补充 0.2 的验收标准
```

## 改不同类型文件时的注意事项

### 改 schema

**必须同步检查三个契约点。** schema 是唯一真相来源，改了它，rubric 和 prompt 都得跟着。

- 增删评分维度 → 改 `rubrics/*.yaml` 的 `schema_name`
- 增删输入字段 → 改 `prompts/**.md` 的 `input_contract`
- 改完跑 `elk check`，契约点 A/B 会拦住不一致

### 改 rubric

- 判据必须**可观测**。写"错误较多"不合格，写"错误 5–8 处，其中 ≤2 处造成理解困难"才合格。
- **不要复制官方评分标准原文**。用自己的话 paraphrase。
- 改完确认 `elk render` 的输出确实包含了新判据（rubric 是编译注入的）。

### 改 prompt

- 文件头四项（`version` / `purpose` / `input_contract` / `output_contract`）缺一不可。
- 用了新的占位符，必须在 `placeholders` 里声明——否则门禁报错。
- 控制参数（如"生成几问"的 `n`）放 `params`，不要放 `input_contract`。
  `input_contract` 里的字段必须能在 schema 中找到对应。

### 加新的数据解析

参考 `src/elk/parse/parse_reading_taskbank.py`，它的注释里记了 12 个踩过的坑。
新解析器请同样把坑写进注释——**踩坑记录是本仓库最有价值的资产之一**。

## 绝对不能做的事

1. **不要提交任何真实考试材料**（PDF / 图片 / 题库文本）。`data/raw/` 已被忽略，别用 `git add -f`。
2. **不要把官方评分标准原文放进 prompt 或 rubric**。放 `rubrics/_official-reference/` 仅供人工对照，且已被忽略。
3. **不要让代码依赖本地特有路径**。用 `elk.paths` 的函数。
4. **不要在示例数据里放真实题目**。`examples/data/` 必须是可以自由分发的自造内容。

## 报告问题

开 issue 时请附上：

- `elk check` 的输出
- `elk paths` 的输出
- Python 版本与操作系统

如果涉及数据解析，附上触发问题的 PDF 页码或题号（**不要附文件本身**）。
