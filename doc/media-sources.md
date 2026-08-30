# 阅读素材来源清单

> **本文件的核心问题不是"哪里有好文章"，而是"哪些文章改写后可以合法再分发"。**
>
> 题库要随专家包一起分发，所以素材许可决定了它能走多远。
> 一条铁律：**原文版权 ≠ 改写后无版权**。改写（paraphrase）在多数许可下属于演绎作品，
> 只有明确允许演绎的许可（CC BY / CC BY-SA / 公共领域）才能安全入库。

## 一、可用（允许演绎，入库安全）

| 来源 | 许可 | 题材 | 雅思适配度 |
|---|---|---|---|
| **PLOS ONE / PLOS Biology** | CC BY | 综合科学、医学、环境 | ⭐⭐⭐⭐⭐ 学术风格最贴近 |
| **PubMed Central (OA subset)** | CC BY | 生命科学、公共卫生 | ⭐⭐⭐⭐⭐ |
| **eLife** | CC BY | 生命科学、医学 | ⭐⭐⭐⭐⭐ |
| **Frontiers 系列** | CC BY | 综合科学 | ⭐⭐⭐⭐ |
| **Our World in Data** | CC BY | 数据驱动：气候、能源、健康、贫困 | ⭐⭐⭐⭐⭐ 图表素材丰富，适合 table/flowchart 题 |
| **Wikipedia（英文）** | CC BY-SA | 百科 | ⭐⭐⭐⭐ 注意 SA 传染性（见下） |
| **NASA / NOAA / USGS** | 公共领域 | 天文、地球科学 | ⭐⭐⭐⭐ |
| **UN / WHO / World Bank 报告** | 多数宽松（需逐份确认） | 发展、卫生、经济 | ⭐⭐⭐⭐ |
| **Project Gutenberg** | 公共领域 | 经典文学 | ⭐⭐ 题材偏文学，仅适合少量训练 |

### CC BY 的要求（必须满足）

- **署名**：数据包 `pack.json` 的 `sources[]` 必须记录 `origin` + `source_url` + `licence`
- **标注改动**：`meta.provenance` 中声明 `engine: llm-generated`（我们做了改写）
- 不需要"相同方式共享"，可与本项目的 MIT 代码共存

### CC BY-SA 的注意事项（Wikipedia）

SA 具有**传染性**：改写后的段落若构成演绎作品，分发时可能需沿用 SA。
保守做法：Wikipedia 素材**只做事实来源、不做 paraphrase 入库**——
即用它核对背景，题目自己写。本项目默认**不**把 Wikipedia 正文写入数据包。

## 二、不可用（版权严格，禁止改写入库）

| 来源 | 原因 |
|---|---|
| BBC News | 全版权保留，无演绎授权 |
| The Guardian | 全版权保留（Open Platform 仅授权 metadata 与特定 API 用途） |
| The Economist / NYT / WSJ / FT | 付费墙 + 全版权 |
| Nature / Science / Scientific American | 全版权，且禁止演绎 |
| **The Conversation** | CC BY-**ND** —— ND 明确**禁止演绎**，改写即违规 ⚠️ |

> The Conversation 是个容易踩的坑：它看起来"开放"，但 ND 条款恰好禁止我们最需要做的改写。

## 三、工作流

```
1. 从"可用"清单挑文章 → 记 URL
2. fetch_source.py 抓取原文 → 落 data/raw/reading/（.gitignore 排除，不入库）
3. LLM 按 prompts/reading/generate-from-source.v1.md 转换
   —— 关键：paraphrase 成 700-1000 词考试体，不照抄
4. elk_core.py validate <file> reading-test 校验结构
5. 通过后放入 capability/packs/<pack-id>/data/reading/<source>/
6. 更新 pack.json 的 contents.counts 与 sources[]
7. release.py 重新打包分发
```

## 四、合规红线（不可协商）

1. **data/raw/ 永不入库** —— 抓取的原文只留在本地，`.gitignore` 强制排除
2. **每个数据包必须声明 `redistributable`** —— 不确定就填 `false`
3. **不可再分发的包照常可加载**，但列出时须显著标注（专家会在输出中提示）
4. **官方考试真material绝不入库** —— 官方样题仅限 `official` 目录下已标注的部分，
   且本项目内置的 `reading-official-sample` 已经过所有者筛选确认为可分发数据

## 五、待补充

- [ ] 来源抓取频率与 robots.txt 遵守策略
- [ ] 每个来源的正文提取器（HTML 结构不同）
- [ ] 难度分级（CEFR / 蓝思值）自动标注
- [ ] 生成题目的 QC 关卡：答案唯一性、干扰项有效性、证据可定位性
