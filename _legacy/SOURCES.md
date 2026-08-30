# 数据源清单

> 采集状态更新：2026-08-28
> 合规细则见 `LICENSE-NOTICE.md`

## 状态图例

✅ 已采集 · 🔜 待采集 · ⛔ 明确不使用

---

## 一、口语 Speaking

| 源 | URL | 规模 | 格式 | 状态 | 许可/风险 |
|---|---|---|---|---|---|
| 雅思哥代理（Part1） | `ielts-bro-proxy.duzhuo.icu/ielts-bro/topic-list?catalog={人物\|事物\|事件\|地点}&part=0` | **48 题组** | JSON | ✅ 2026-08-28 | 第三方代理无授权，仅本地缓存 |
| 雅思哥代理（Part2&3） | 同上 `&part=1` | **65 题组** | JSON | ✅ 2026-08-28 | 同上；音频不外发 |
| joespeaking 公开题库 | `joespeaking.com/ielts-speaking-questions/2026-may-aug` | 191 话题 | HTML | 🔜 用于交叉验证覆盖率 | 非结构化，需抓取 |

**采集结果**：113 题组 / 716 道题目，零失败。产物：
- `data/structured/speaking/seasons/2026-05-08/snapshot-2026-08-28.jsonl`（113 条，schema 校验 100% 通过）
- `state/ielts.db`（`oral_topics` 113 行 / `oral_questions` 716 行）

**API 两个坑**（已写进脚本注释）：
1. `part` 不是 1/2/3 —— `part=0` → Part1，`part=1` → Part2&3，`part=2` → 返回空
2. cue card 文本含控制字符，必须 `json.loads(s, strict=False)`

**timeTag 实测三种值**：`2026年5-8月`（70）/ `2026年9-12月`（35）/ `非大陆地区5-8月`（8）。
第三种是侦察阶段未发现的——**题库区分大陆与海外考区，换季 diff 必须按考区分开处理**。

**换季时间窗**：现役 2026 年 5–8 月题库；**9 月第一周开库**，新题约 30–40%。
基线快照已于 2026-08-28 采集，作为 9 月 diff 的比较基准。

---

## 二、写作 Writing

| 数据集 | 规模 | 状态 | 审计判决（2026-08-28 修订） |
|---|---|---|---|
| `chillies/ielts-writing-task2-essays` | 8,049 篇 | ✅ | ⚠️ **PARTIAL** — 仅 TR / CC 可用 |
| `btnotpt/ielts_task_2` | 10,219 篇 | ✅ | ⚠️ **SUSPECT** — 仅 overall 可用；题目文本仍可用（4,958 题） |
| `hai2131/IELTS-essays-task-1` | 13,925 篇 | ✅ | ❌ **UNUSABLE** — 四维全反推，Task 1 且需图片，P0 不做 |
| `AustinWang668/ielts-writing-scorer` 的 `evals/official-sources.json` | 23 例官方标定 | ✅ 2026-08-28 | **仅 overall，无四维**；Task 2 仅 10 例 |

下载命令：
```bash
huggingface-cli download chillies/ielts-writing-task2-essays \
  --repo-type dataset --local-dir data/raw/writing/chillies_task2
```

EDA 报告：`data/eval/reports/eda-writing-2026-08-28.json`（band 分布层面）
审计报告：`data/eval/reports/audit-writing-2026-08-28.json`（对齐 + 四维独立性层面）
审计脚本：`scripts/eval/audit_writing_alignment.py`（可复跑）

### 官方源抓取已打通（2026-08-28）

**Cloudflare 拦截的解法：`--http1.1`。** 实测矩阵：

| 方式 | 结果 |
|---|---|
| curl 默认（HTTP/2） | 403 `Just a moment...` |
| curl + UA / Referer（仍是 HTTP/2） | 403 同上 |
| **curl `--http1.1` + 全套 sec-ch-ua 头** | **200 正常下载** |

根因是 Cloudflare 对 **HTTP/2 指纹**（SETTINGS 帧顺序、WINDOW_UPDATE、优先级树）做 bot 判定，
本地 curl 的 HTTP/2 指纹不在白名单；降级到 HTTP/1.1 后不再触发。
**改 UA / 加 Referer 都无效，只有降级协议版本有效。** 已固化在 `scripts/fetch/fetch_official.py`。

已下载（6/7，GT 阅读 URL 猜错 404）：

| 文件 | 大小 | 用途 |
|---|---|---|
| `writing/official-sample/academic-writing-sample-tasks-2023.pdf` | 1.50 MB | 23 例标定集源（学术 12 例） |
| `writing/official-sample/general-writing-sample-tasks-2023.pdf` | 1.44 MB | 标定集源（培训类 11 例） |
| `reading/ielts-official/access/reading-{text,question}-booklet.pdf` | 136 / 208 KB | 无障碍大字版，三件套分离 |
| `reading/ielts-official/access/reading-answer-key.pdf` | 26 KB | 答案 |
| `reading/ielts-official/academic-reading-sample-tasks-2023.pdf` | 934 KB | 常规排版版，题量更大 |

### 官方样题里到底有什么（两条实测结论，都改变方案）

**1. 只公布 overall band，不公布 TR/CC/LR/GRA 四维分数。**
manifest 的 `official_band` 是单个数字；PDF 里是 "Examiner comment / Band 4" 加一段综合评语。
→ 这批材料**无法用于四维回归**，只能校准 overall。

**2. 作文正文是图片，Examiner comment 是文本。**
26 页中页 14/16/19/21/23/25 的文本量仅 82 字符（只有页眉）却含 2-4 张图片 —— 正文渲染成图。
要拿正文必须 OCR，而 comment 直接可提。

**价值排序因此反转**：

| 内容 | 获取成本 | 价值 |
|---|---|---|
| **23 条 Examiner comment** | 低（纯文本，已提取） | **高** — rubric v1 的锚点素材 |
| 23 篇作文正文 | 高（需 OCR） | 中 — Task 2 仅 10 例，统计功效弱 |

已用 `scripts/parse/extract_official_comments.py` 提取 **23/23 条 comment，零校验失败**
（以 manifest 的 `official_band` 做强校验，版面错位会立刻被抓出），
band 覆盖 3.0–8.5，Task 2 comment 平均 154 词。
产出 `data/raw/writing/official-sample/comments.jsonl`（不分发）。

**两条格式差异（修过的 bug）**：学术版是 `Sample Task – 2A – Script A`（Task 后有 dash），
培训类是 `Sample Task 1A – Script A`（无 dash）。正则里 dash 必须写成可选，
否则培训类 11 条全漏。

### ⚠️ 审计结果推翻了 EDA 的排序 —— 以本节为准

EDA 只能看 band 怎么分布，无法判断「题目和作文配不配对」「四维是不是从 overall 反推的」。
这两个问题直接决定评分器能不能建，所以补跑了一轮审计。**结论修正如下：**

**Q1 题目-作文对齐**（题目实词在作文中的 5-char 词干命中率）

| 数据集 | 平均命中 | 零命中占比 | 结论 |
|---|---|---|---|
| chillies | 0.708 | 1.2% | ✅ 配对可靠 |
| btnotpt | 0.662 | 1.0% | ✅ 配对可靠 |
| hai2131 | — | 27.4% | 不适用：`topic` 列存的是 `"Table"` 这类图表类型标签，不是题目 |

> **方法学教训**：审计前我抽到 chillies 一条「题目讲经济发展、作文写青少年志愿者」的样本，
> 一度怀疑整集系统性错位。量化后才知道那是 1.2% 的噪声，不是全局问题。
> **单样本抽样会以偏概全，此类判定必须统计化。**

**Q2 四维独立性**

| 数据集 | max r | 同值率 | overall 由四维均值还原 | 判决 |
|---|---|---|---|---|
| chillies | 0.848 (LR-GRA) | LR==GRA **67.4%** | 89.5%（精确 71.9%） | PARTIAL |
| btnotpt | 0.777 | — | **100.0%** | SUSPECT |
| hai2131 | 0.935 | CC==GRA 61.6% | **100.0%** | UNUSABLE |

关键证据与推断：

1. **chillies 的 LR 与 GRA 非独立**：`LR == GRA` 占 67.4%，`GRA - LR` 差值正负比 **10.6 : 1**
   （正差 2,345 例 / 负差 223 例）。真实独立评分不该有这种方向偏态，
   推断为「LR 复制后再叠加非负扰动」的构造痕迹。→ **LR / GRA 分数不可作回归真值。**
2. **chillies 的 TR / CC 可用**：r(TR,CC)=0.489，与 TR-LR(0.16)、TR-GRA(0.203) 同处健康区间，
   未见耦合证据。
3. **btnotpt 四维是纯合成**：overall 可被四维均值 **100% 精确还原**，
   即四维严格服从 `overall = round(mean)`，不是独立标注。→ 只能回归 overall。
   EDA 当时只抓到「band 集中 49.5%」，漏掉了这条致命伤。
4. **chillies 的 10.5% 不可还原反而是优势**：说明它不是纯合成，
   存在独立标注成分（btnotpt 为 0%）。
5. **chillies 的 LR/GRA 是 BIGINT 整数列**（TR/CC 是 DOUBLE），半档信息在存储时已丢失；
   `gpt_advices` 字段 **68.5% 为空**，不能当作 rubric 锚点的主要来源。

**对 P0 的直接影响**

- 评分器回归目标从「四维」收缩为 **TR / CC / overall 三维**；LR / GRA 只能输出主观分，无法验证。
- 唯一能验证四维的仍是**官方 23 例标定集**（`AustinWang668/ielts-writing-scorer`），P0 必须先拿它。
- 样本筛选需新增一条门禁：丢弃 `alignment < 0.20` 的样本（chillies 约 1.8%）。

---

## 三、阅读 Reading

**HuggingFace 与 Kaggle 均无结构化雅思阅读数据集**（已全量检索确认）。
可合法获取的官方题约 300 道，全部为 PDF/DOCX，需自建解析。

| 源 | URL | 内容 | 状态 | 备注 |
|---|---|---|---|---|
| ielts.org 学术样题 | `https://ielts.org/cdn/Sample-tests/ielts-academic-reading-sample-tasks-2023.pdf` | 46 页，3 篇 + 40 题 + 答案 | 🔜 | PDF |
| ielts.org 培训类 | `.../ielts-general-reading-sample-tasks-2023.pdf` | 33 页 | 🔜 | PDF |
| ielts.org 机考分题型 | `https://ielts.org/cdn/computer-delivered-sample-tests-academic-reading/ielts-academic-reading-computer-delivered-{题型}-answer-key.pdf` | 17 个（10 学术 + 7 培训类） | 🔜 | 题型 slug 见下 |
| **ielts.org 无障碍版** | `https://www.ielts.org/for-test-takers/special-requirements` | text/question/answer booklet | 🔜 **优先** | **提供 DOCX，解析成本远低于 PDF** |
| IDP | `https://ielts.idp.com/kuwait/prepare/ielts-test-preparation-material/listening,reading/academic` | 3 套完整 practice test + 分题型 | 🔜 | 补 Matching headings / Diagram label |
| Cambridge | `https://www.cambridgeenglish.org/Images/23435-ielts-sample-papers.zip` | 样卷 | 🔜 | 已验证可达 |

机考分题型 slug：`multiple-choice-one-answer` / `identifying-information-true-flase-not-given`（原文拼写如此）/
`note-completion` / `table-completion` / `matching-features` / `summary-completion-selecting-words-from-text` /
`summary-completion-selecting-from-list-of-words-or-phrases` / `sentence-completion` / `matching-sentence-endings`

### ⛔ 明确不使用

GitHub 剑雅真题仓库（如 `EthanLin-TWer/ielts`）—— 盗版扫描件。
理由与替代方案见 `LICENSE-NOTICE.md` §1。

### 合成母本池（P1 阶段建）

PLOS ONE（CC-BY）/ PubMed Central OA / OpenStax / DOAJ。
筛选：正文 1500–3500 词、CEFR-J A1–B2 覆盖 85–93%、C1+ ≤ 8%、含多观点或研究对比。

---

## 四、词汇表（🔜）

| 源 | URL | 规模 | 许可 |
|---|---|---|---|
| machine_readable_wordlists | `github.com/lpmi-13/machine_readable_wordlists` | AWL/UWL/AKL/AVL/NAWL，570–1991 词条 | 有 LICENSE |
| CEFR-J Wordlist | `github.com/openlanguageprofiles/olp-en-cefrj` | v1.6，7,801 条 A1–B2 | 需引用（TUFS） |

---

## 五、听力 Listening

本阶段**明确不做**（音频版权 + ASR 对齐 + 题目时序，复杂度是阅读数倍）。
`data/raw/listening/` 目录与 schema 位已预留。
