# 许可与合规声明

本仓库定位：**个人学习用途为主，保留未来公开/开源的可能**。因此合规按「可能公开」标准执行——
比纯自用严格，比商业化宽松。

最后更新：2026-08-28

---

## 1. 红线：IELTS 官方材料

来源：<https://ielts.org/legal/ielts-copyright-and-trade-mark-statement>

> © IELTS Partners（British Council / IDP Education / Cambridge Assessment English）
> 仅限**个人非商业使用**。明文禁止：商业用途、在他站再发布、修改、移除版权声明。
> `IELTS` 为注册商标。

据此执行的硬规则：

| 对象 | 处置 |
|---|---|
| 官方 PDF/DOCX 原件（ielts.org、IDP、Cambridge） | 只落 `data/raw/`，已被 `.gitignore` 排除，**永不提交、永不外发** |
| 官方 Band Descriptors | 锁在 `rubrics/_official-reference/`，仅供人工对照，**禁止写进 prompt** |
| prompt 中的 rubric | **自写 paraphrase**：维度名 + 每档 3–5 条可观测特征，不逐字复制官方文本 |
| 官方题解析产物 | `source.redistributable = false`，`meta.not_official = true`（schema 层强制） |
| 产品/仓库命名 | 不含 `IELTS` 商标词；描述性说明属 nominative fair use |
| UI 展示 | 强制标注「AI 估分，非官方成绩」 |

**关于 GitHub 剑雅真题仓库（EthanLin-TWer/ielts 等）：明确不使用。**
这些是盗版扫描件。使用后果有二：解析产物无法随数据集公开（等于白做）；
且只要有一条数据来自盗版源，整份许可声明的可信度都会崩塌。

**替代方案（已验证等价）**：ielts.org / IDP / Cambridge 三家提供**免费正版**样题 PDF（约 300 题），
由同一批出题方制作，版式与剑雅真题一致，验证解析链路的判别力完全相同。
若后续 300 题不够用，走正版购书（约 $12/套，每册 4 套真题）。

---

## 2. 合成题母本许可

| 源 | 许可 | 可否随数据集公开 |
|---|---|---|
| PLOS ONE | CC-BY | ✅ 可（需署名） |
| PubMed Central OA | CC-BY（多数） | ✅ 可（需逐条核对） |
| OpenStax | CC-BY | ✅ 可（需署名） |
| Wikipedia Featured/Good Articles | CC-BY-SA | ⚠️ 演绎作品需同协议分享，标 `redistributable: false` |
| DOAJ | 各异 | ⚠️ 需逐条核对 |

所有母本在落盘时必须记录 `license + attribution + source_url`，集中登记于 `SOURCES.md`。

---

## 3. 第三方数据集

| 数据集 | 许可 | 状态 | 备注 |
|---|---|---|---|
| `chillies/ielts-writing-task2-essays` | CC-BY-4.0 | ✅ 已下载 8,049 篇 | **唯一可作四维回归真值**，见下 |
| `hai2131/IELTS-essays-task-1` | 需复核 | ⚠️ 已下载 13,925 篇 | 四维疑由 overall 反推 |
| `btnotpt/ielts_task_2` | Apache-2.0 | ⚠️ 已下载 10,219 篇 | band 分布高度集中，分数不可用；题目文本可用 |

---

## 4. 口语题库（第三方代理）

端点 `ielts-bro-proxy.duzhuo.icu` 是**第三方反向代理，无官方授权、无 Terms**。

- 抓取结果**仅作本地缓存**，不对外分发
- 音频 mp3 **只存 URL 与时长，不下载、不入库 blob、不外发**（schema 层 `redistributable: false` 硬约束）
- 端点随时可能失效，只能做一次性语料采集，**不可作为在线依赖**
- 题目文本层面属事实性信息，快照保留在 `data/structured/speaking/`，`not_official: true`

---

## 5. 已实测的数据质量警示（2026-08-28 EDA）

跑 EDA 前默认「样本越多越好」，实测推翻：

| 数据集 | 判定 | 依据 |
|---|---|---|
| **chillies** | ✅ 推荐主用 | band 覆盖 0–9 全 19 档，最大单档仅 12.7%；**维度间相关性普遍 < 0.5**（独立评分）；零重复；1,970 个独立题目 |
| **hai2131** | ⚠️ 仅可用 overall | 四维一致性 **100.0%**（≤0.25 也 100%）；**维度间相关系数 0.865–0.935** → 四维是从 overall 反推的；重复作文 36.4% |
| **btnotpt** | ❌ 分数废弃 | **6.0 分独占 49.4%**；取值仅 7 档且分布断裂（7.0 仅 87 篇，8.0 突跳 861）→ 模板打分 |

**口径提醒**：
- 带红旗的数据集，其分数只能当**代理真值**，代理集 QWK 验收线按 **0.65** 而非 0.80
- chillies 的 CC/LR/GRA 是**整数列无半档**，四维回归无法评到 0.5 档 → 只回归 overall 或放宽到整档
- 分数不可用的数据集，**题目文本仍可抽作题库**（题目与评分是两回事）

---

## 6. Attribution

- IELTS 官方样题：© IELTS Partners，仅个人非商业使用
- `chillies/ielts-writing-task2-essays`：CC-BY-4.0
- `btnotpt/ielts_task_2`：Apache-2.0
- 评分契约骨架源自 `Shpaldik/OpenIELTS-AI`（MIT），已补 `not_official` / `rubric_version` /
  `matched_anchor` / `consistency` 四处短板
- 官方标定集索引思路源自 `AustinWang668/ielts-writing-scorer`（MIT）：只记位置 + 分数，
  刻意不重分发官方原文

---

## 7. 免责

本仓库与 IELTS Partners（British Council / IDP Education / Cambridge Assessment English）
**无任何隶属或授权关系**。所有 AI 评分为估计值，不构成官方成绩，不得用于任何官方用途。
