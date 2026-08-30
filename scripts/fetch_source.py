#!/usr/bin/env python3
"""
fetch_source.py — 从权威公开来源抓取文章原文

职责边界
--------
本脚本**只做抓取与正文提取**，不做命题。命题交给 LLM（见
`capability/prompts/reading/generate-from-source.v1.md`）。

这样切分的原因：抓取是确定性的、可复现的，适合脚本；
命题是创造性的、需要判断的，适合 LLM。把两者混在一起会导致
"改一个 prompt 就要重跑一遍爬虫"。

合规（重要）
-----------
1. 抓取的内容**只落 data/raw/**，该目录被 .gitignore 排除，**永不入库**
2. 只抓取 `doc/media-sources.md` 中标记为"可用（CC BY / 公共领域）"的来源
3. 遵守 robots.txt；请求间隔默认 2 秒，不并发（时间换空间，别把对方打挂）

用法
----
    python3 scripts/fetch_source.py <url> [--out data/raw/reading/]
    python3 scripts/fetch_source.py --list          # 打印推荐来源清单
    python3 scripts/fetch_source.py --batch urls.txt
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RAW = REPO / "data" / "raw" / "reading"

UA = "ELK-StudyBot/0.1 (+educational use; contact: local user)"

# 已知可安全抓取的来源（对应 doc/media-sources.md 的"可用"清单）
SAFE_HOSTS = {
    "journals.plos.org": "CC BY",
    "www.ncbi.nlm.nih.gov": "CC BY (OA subset)",
    "elifesciences.org": "CC BY",
    "www.frontiersin.org": "CC BY",
    "ourworldindata.org": "CC BY",
    "www.nasa.gov": "Public domain",
    "www.noaa.gov": "Public domain",
    "www.who.int": "Check per-report",
    "www.worldbank.org": "Check per-report",
}


def _now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


class TextExtractor(HTMLParser):
    """极简正文提取：只保留 <p> 里的文本，丢弃脚本/样式/导航。

    为什么不装 readability/newspaper3k：它们各自带一堆依赖（lxml、pillow…），
    而 PLOS/PMC 这类站点的正文结构就是干净的 <p>，标准库够用。
    真碰到提取不干净的站点，再针对性补一个提取器。
    """

    SKIP = {"script", "style", "nav", "header", "footer", "aside", "form", "figure"}

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._in_p = False
        self._buf: list[str] = []
        self.paragraphs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag == "p":
            self._in_p = True
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag == "p" and self._in_p:
            self._in_p = False
            text = "".join(self._buf).strip()
            text = re.sub(r"\s+", " ", text)
            if len(text) > 40:  # 过滤导航碎片
                self.paragraphs.append(text)
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._in_p and self._skip_depth == 0:
            self._buf.append(data)

    def text(self) -> str:
        return "\n\n".join(self.paragraphs)


def _slug(url: str) -> str:
    """从 URL 生成稳定的文件名。

    坑：形如 `?id=10.1371/journal.pone.0301234` 的 URL 没有有意义的路径段，
    朴素取末段会退化成一个通用的 "article"，多篇抓取会互相覆盖。
    所以依次尝试：路径末段 → query 里的 id/doi → 域名+路径哈希。
    """
    parts = urllib.parse.urlparse(url)

    # 1. query 里的 id / doi（PLOS、PMC 常见，比路径更有辨识度）
    q = urllib.parse.parse_qs(parts.query)
    base = (q.get("id") or q.get("doi") or q.get("article") or [""])[0]

    # 2. 路径末段（query 没命中时；过滤太短、纯数字、或无意义通用词）
    if not base:
        seg = [s for s in parts.path.split("/") if s]
        if seg and len(seg[-1]) >= 3 and not seg[-1].isdigit():
            base = seg[-1]
    if base:
        base = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    if not base or base in {"article", "abstract", "full", "text"}:
        # 3. 兜底：域名 + 路径哈希，保证唯一
        digest = hashlib.sha256(url.encode()).hexdigest()[:8]
        base = f"{parts.netloc.split('.')[0]}-{digest}"
    return base[:60]


def _check_robots(url: str) -> bool:
    """遵守 robots.txt。查询失败时保守放行（脚本式单次抓取，非爬虫）。"""
    try:
        parts = urllib.parse.urlparse(url)
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{parts.scheme}://{parts.netloc}/robots.txt")
        rp.read()
        return rp.can_fetch(UA, url)
    except Exception:
        return True


def fetch(url: str, out_dir: Path, delay: float = 2.0) -> Path | None:
    host = urllib.parse.urlparse(url).netloc
    licence = SAFE_HOSTS.get(host)
    if licence is None:
        print(f"  [警告] {host} 不在已知安全来源清单，请先确认许可", file=sys.stderr)
        print(f"         参见 doc/media-sources.md", file=sys.stderr)
    if not _check_robots(url):
        print(f"  [拒绝] robots.txt 禁止抓取: {url}", file=sys.stderr)
        return None

    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [失败] {url}: {e}", file=sys.stderr)
        return None

    ex = TextExtractor()
    ex.feed(html)
    text = ex.text()
    if len(text.split()) < 300:
        print(f"  [跳过] {url}: 提取正文仅 {len(text.split())} 词，可能结构特殊",
              file=sys.stderr)
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    slug = _slug(url)
    txt_path = out_dir / f"{slug}.txt"
    meta_path = out_dir / f"{slug}.meta.json"
    txt_path.write_text(text, encoding="utf-8")
    meta_path.write_text(json.dumps({
        "source_url": url,
        "host": host,
        "licence": licence or "UNKNOWN — 需人工确认",
        "retrieved_at": _now(),
        "word_count": len(text.split()),
        "paragraphs": len(ex.paragraphs),
        "redistributable": bool(licence) and "Check" not in (licence or ""),
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"  ✓ {slug}: {len(text.split())} 词 → {txt_path.name}")
    time.sleep(delay)  # 别把对方打挂
    return txt_path


def list_sources() -> int:
    print("已知可安全抓取的来源（详见 doc/media-sources.md）：\n")
    for host, lic in SAFE_HOSTS.items():
        print(f"  {host:32} {lic}")
    print("\n禁止改写入库：BBC / Guardian / Economist / NYT / Nature / The Conversation(CC BY-ND)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="抓取阅读素材原文")
    ap.add_argument("url", nargs="?", help="要抓取的文章 URL")
    ap.add_argument("--out", default=str(RAW), help="输出目录（默认 data/raw/reading/）")
    ap.add_argument("--batch", help="包含多个 URL 的文本文件（每行一个）")
    ap.add_argument("--list", action="store_true", help="打印推荐来源清单")
    ap.add_argument("--delay", type=float, default=2.0, help="请求间隔秒数（默认 2）")
    args = ap.parse_args()

    if args.list:
        return list_sources()

    urls: list[str] = []
    if args.batch:
        urls = [u.strip() for u in Path(args.batch).read_text(encoding="utf-8").splitlines()
                if u.strip() and not u.startswith("#")]
    elif args.url:
        urls = [args.url]
    else:
        ap.print_help()
        return 1

    out_dir = Path(args.out)
    ok = 0
    for u in urls:
        if fetch(u, out_dir, args.delay):
            ok += 1
    print(f"\n完成：{ok}/{len(urls)} 篇 → {out_dir}")
    print("提示：抓取内容仅在本地，不入 git。命题请用 "
          "prompts/reading/generate-from-source.v1.md")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
