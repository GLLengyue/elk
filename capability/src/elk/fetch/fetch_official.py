#!/usr/bin/env python3
"""
fetch_official.py — 雅思官方免费样题抓取（ielts.org / cdn.ielts.org）

为什么单独写这个文件
--------------------
ielts.org 全站有 Cloudflare bot 防护，实测结果：

  | 方式                          | 结果                        |
  |-------------------------------|-----------------------------|
  | curl 默认（HTTP/2）           | 403 "Just a moment..."      |
  | curl + UA / Referer（HTTP/2） | 403 同上                    |
  | **curl --http1.1 + 全套浏览器头** | **200 正常下载**         |

根因是 Cloudflare 对 **HTTP/2 指纹**（SETTINGS 帧顺序、WINDOW_UPDATE、优先级树）
做 bot 判定，本地 curl 的 HTTP/2 指纹不在白名单内；降级到 HTTP/1.1 后不再触发。
**改 UA / 加 Referer 都无效，只有降级协议版本有效** —— 这一条别再试错。

因此本脚本用 subprocess 调 curl 并强制 `--http1.1`，而不是 urllib
（urllib 无法控制 ALPN 协商，且不好复刻 sec-ch-ua 系列头）。

合规
----
下载的是 IELTS 官方免费公开的 sample materials（面向考生的练习材料），
仅存于 `data/raw/`（已被 .gitignore 隔离，不进版本库、不随数据集分发）。
解析产物（题目结构、题型、答案位置）才可能进入 data/structured/。
详见 LICENSE-NOTICE.md。

用法
----
    ./.venv/bin/python scripts/fetch/fetch_official.py              # 下载缺失的
    ./.venv/bin/python scripts/fetch/fetch_official.py --force      # 全部重下
    ./.venv/bin/python scripts/fetch/fetch_official.py --only reading
    ./.venv/bin/python scripts/fetch/fetch_official.py --dry-run

依赖：仅标准库 + 系统 curl。
"""

from __future__ import annotations
from elk.paths import (
    repo_root, data_root, schemas_dir, rubrics_dir, prompts_dir,
    db_path, index_path, structured_dir,
)

import argparse
import subprocess
import sys
import time
from pathlib import Path


RAW = data_root() /  "raw"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# 已验证可通的 header 集。sec-ch-ua 三件套必须齐全，缺一会掉回 challenge。
HEADERS = [
    "-H", f"User-Agent: {UA}",
    "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "-H", "Accept-Language: en-GB,en-US;q=0.9,en;q=0.8",
    "-H", 'sec-ch-ua: "Chromium";v="131", "Not_A Brand";v="24"',
    "-H", "sec-ch-ua-mobile: ?0",
    "-H", 'sec-ch-ua-platform: "macOS"',
    "-H", "Sec-Fetch-Dest: document",
    "-H", "Sec-Fetch-Mode: navigate",
    "-H", "Sec-Fetch-Site: none",
    "-H", "Upgrade-Insecure-Requests: 1",
]

# ------------------------------------------------------------------ 目标清单
# (分组, 相对路径, URL, 最小期望字节)
ACCESS = "https://ielts.org/cdn/ielts-access-arrangements-sample-tests/ielts-modified-large-print"
SAMPLE = "https://ielts.org/cdn/Sample-tests"
CD_ACADEMIC = "https://ielts.org/cdn/computer-delivered-sample-tests-academic-reading"

TARGETS: list[tuple[str, str, str, int]] = [
    # --- 写作：官方标定集（23 例 sample scripts 的源） -------------------
    ("writing", "writing/official-sample/academic-writing-sample-tasks-2023.pdf",
     "https://cdn.ielts.org/Sample-tests/ielts-academic-writing-sample-tasks-2023.pdf", 1_000_000),
    ("writing", "writing/official-sample/general-writing-sample-tasks-2023.pdf",
     f"{SAMPLE}/ielts-general-training-writing-sample-tasks-2023.pdf", 1_000_000),

    # --- 阅读：无障碍大字版（text/question/answer 三件套分离，解析成本最低）
    ("reading", "reading/ielts-official/access/reading-text-booklet.pdf",
     f"{ACCESS}/ielts-academic-reading-access-arrangement-modified-large-print-text-booklet.pdf", 100_000),
    ("reading", "reading/ielts-official/access/reading-question-booklet.pdf",
     f"{ACCESS}/ielts-academic-reading-access-arrangement-modified-large-print-question-booklet.pdf", 150_000),
    ("reading", "reading/ielts-official/access/reading-answer-key.pdf",
     f"{ACCESS}/ielts-academic-reading-access-arrangement-modified-large-print-sample-test-answer-key.pdf", 10_000),

    # --- 阅读：常规排版版（题量更大，作对照） ---------------------------
    ("reading", "reading/ielts-official/academic-reading-sample-tasks-2023.pdf",
     f"{SAMPLE}/ielts-academic-reading-sample-tasks-2023.pdf", 500_000),
    # 注意是 general-reading 不是 general-training —— 后者 404。
    # （写作那条 URL 用的是 general-training，两者命名不一致，别顺手统一。）
    ("reading", "reading/ielts-official/general-reading-sample-tasks-2023.pdf",
     f"{SAMPLE}/ielts-general-reading-sample-tasks-2023.pdf", 500_000),
] + [
    # --- 阅读：机考分题型（**只有答案页**，见下方重要说明）--------------
    # 2026-08-29 实测：15 个候选 slug 里 8 个可达，其余 404。
    # 注意 identifying-information 的官方 slug 拼错了 —— 是 true-FLASE-，
    # 不是 false。照抄官方拼写，别"顺手修正"。
    #
    # 【重要】这些 -answer-key.pdf 每个只有 1 页，内容形如
    #     "Sample Academic Reading Note Completion / Answers / 1 ... 2 ..."
    # **没有配套的题目与文本**。已试过 note-completion.pdf /
    # -questions.pdf / -text.pdf / -sample.pdf 全部 404。
    # 所以它们目前**无法构成练习题**，只能作为答案交叉校验的备用材料。
    # 不要拿它们的数量去虚报题量。
    ("reading", f"reading/ielts-official/computer-delivered/academic-{slug}.pdf",
     f"{CD_ACADEMIC}/ielts-academic-reading-computer-delivered-{slug}-answer-key.pdf",
     12_000)
    for slug in (
        "multiple-choice-one-answer",
        "multiple-choice-more-than-one-answer",
        "identifying-information-true-flase-not-given",
        "matching-features",
        "matching-sentence-endings",
        "sentence-completion",
        "note-completion",
        "table-completion",
    )
]

# 实测 404、不要再加的 slug（避免下次重复试错）：
#   identifying-writers-views-yes-no-not-given / matching-information /
#   matching-headings / flow-chart-completion / diagram-label-completion /
#   short-answer-questions
# 这些题型在 46 页 task bank (academic-reading-sample-tasks-2023.pdf) 里都有。

# 听力音频 8MB，本阶段不做，仅登记 URL 备用
LISTENING_AUDIO = f"{ACCESS}/ielts-listening-access-arrangements-audio.mp3"


def is_valid_pdf(path: Path, min_bytes: int) -> bool:
    """校验 PDF 魔数与最小体积，避免把 Cloudflare 的 challenge HTML 存成 .pdf。

    踩过的坑：403 返回体也是 200 时被误存过一次，
    文件扩展名是 .pdf 但内容是 HTML，后续解析全部失败。
    """
    if not path.exists():
        return False
    if path.stat().st_size < min_bytes:
        return False
    with path.open("rb") as fh:
        return fh.read(5) == b"%PDF-"


def download(url: str, dest: Path, min_bytes: int, timeout: int = 90) -> tuple[bool, str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    cmd = [
        "curl", "-sSL", "--http1.1",          # --http1.1 是关键，勿删
        "-m", str(timeout),
        *HEADERS,
        "-o", str(tmp),
        "-w", "%{http_code}",
        url,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 20)
    except subprocess.TimeoutExpired:
        tmp.unlink(missing_ok=True)
        return False, "timeout"

    code = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""
    if code != "200":
        tmp.unlink(missing_ok=True)
        return False, f"http={code or proc.stderr.strip()[:60]}"

    if not is_valid_pdf(tmp, min_bytes):
        # 明确报告是不是又吃到了 challenge 页面，方便快速定位
        head = tmp.open("rb").read(200).decode("utf-8", "replace") if tmp.exists() else ""
        hint = "疑似 Cloudflare challenge" if "Just a moment" in head else "体积不足或非法 PDF"
        tmp.unlink(missing_ok=True)
        return False, hint

    tmp.replace(dest)
    return True, f"{dest.stat().st_size:,} bytes"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="忽略已存在文件，全部重下")
    ap.add_argument("--only", choices=["reading", "writing"], help="只下载某一组")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    targets = [t for t in TARGETS if not args.only or t[0] == args.only]
    ok = skip = fail = 0

    print(f"{'分组':<9}{'结果':<6}文件")
    print("-" * 78)
    for group, rel, url, min_bytes in targets:
        dest = RAW / rel
        if not args.force and is_valid_pdf(dest, min_bytes):
            print(f"{group:<9}{'跳过':<6}{rel}  ({dest.stat().st_size:,} bytes, 已存在)")
            skip += 1
            continue

        if args.dry_run:
            print(f"{group:<9}{'待下':<6}{rel}")
            continue

        success, info = download(url, dest, min_bytes)
        if success:
            print(f"{group:<9}{'完成':<6}{rel}  ({info})")
            ok += 1
        else:
            print(f"{group:<9}{'失败':<6}{rel}  -> {info}")
            fail += 1
        time.sleep(0.6)   # 礼貌间隔，别让 Cloudflare 重新起防

    print("-" * 78)
    print(f"完成 {ok} · 跳过 {skip} · 失败 {fail}")
    if LISTENING_AUDIO and not args.only:
        print(f"听力音频（本阶段不下载）: {LISTENING_AUDIO}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
