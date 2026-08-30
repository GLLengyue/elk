#!/usr/bin/env python3
"""
sync_assets.py — 把能力层的可复现资产同步进专家包

为什么要有这一步
----------------
专家包（expert/）是**分发的产物**，能力层（capability/）是**开发的源头**。
两者分离后，改了 rubric / schema / prompt 必须有一条明确的同步路径，
否则就会出现"仓库里改了但专家包还是旧的"——这正是 ELK 想消灭的一致性问题。

同步内容
--------
    capability/schemas/**     → expert/skills/elk-coach/assets/schemas/**
    capability/rubrics/*.yaml → assets/rubrics/*.json   （YAML 转 JSON，消除运行时 yaml 依赖）
    capability/prompts/**/*.md→ assets/prompts/**/*.md + prompts.json（frontmatter 提取）
    capability/packs/*/       → assets/packs/*/

为什么 YAML 要转 JSON：专家包运行时**零第三方依赖**，而标准库没有 YAML 解析器。
转换放在这里（开发期，可以用 venv 里的 PyYAML），运行时就只需要 json。

用法
----
    python3 scripts/sync_assets.py            # 同步
    python3 scripts/sync_assets.py --check    # 只检查是否过期，不同步（CI 用）
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CAP = REPO / "capability"
EXPERT = REPO / "expert"
ASSETS = EXPERT / "skills" / "elk-coach" / "assets"

RE_FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def _now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _sync_dir(src: Path, dst: Path, pattern: str = "*") -> int:
    """把 src 下所有文件复制到 dst（保持相对结构）。返回复制文件数。"""
    if not src.exists():
        _log(f"  [警告] 源目录不存在: {src}")
        return 0
    n = 0
    for f in src.rglob(pattern):
        if f.is_dir() or "__pycache__" in f.parts:
            continue
        rel = f.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)
        n += 1
    return n


def sync_schemas() -> int:
    return _sync_dir(CAP / "schemas", ASSETS / "schemas", "*.json")


def sync_packs() -> int:
    """同步数据包。注意：只同步 capability/packs/ 下的正式包，不含 data/raw/ 抓取物。"""
    if not (CAP / "packs").exists():
        _log("  [警告] 无数据包目录")
        return 0
    n = 0
    for pack_dir in sorted((CAP / "packs").iterdir()):
        if not pack_dir.is_dir():
            continue
        manifest = pack_dir / "pack.json"
        if not manifest.exists():
            _log(f"  [跳过] {pack_dir.name}: 无 pack.json")
            continue
        n += _sync_dir(pack_dir, ASSETS / "packs" / pack_dir.name)
    return n


def sync_rubrics() -> int:
    """YAML rubric → JSON。开发期用 PyYAML，运行时零依赖。"""
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        _log("  [错误] 需要 PyYAML。请在 capability/.venv 中运行：")
        _log("         source capability/.venv/bin/activate && python3 scripts/sync_assets.py")
        raise SystemExit(1)

    src_dir = CAP / "rubrics"
    dst_dir = ASSETS / "rubrics"
    dst_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for yf in sorted(src_dir.glob("*.yaml")):
        data = yaml.safe_load(yf.read_text(encoding="utf-8"))
        jf = dst_dir / f"{yf.stem}.json"
        with open(jf, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        _log(f"  ✓ rubric {yf.name} → {jf.name}")
        n += 1
    return n


def sync_prompts() -> int:
    """prompt 模板 → 原样复制 + 提取 frontmatter 汇总成 prompts.json。"""
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        _log("  [错误] 需要 PyYAML（同 rubric）")
        raise SystemExit(1)

    src_dir = CAP / "prompts"
    dst_dir = ASSETS / "prompts"
    if not src_dir.exists():
        return 0
    items = []
    n = 0
    for pf in sorted(src_dir.rglob("*.md")):
        rel = pf.relative_to(src_dir)
        target = dst_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pf, target)
        n += 1

        text = pf.read_text(encoding="utf-8")
        m = RE_FM.match(text)
        if not m:
            _log(f"  [警告] {rel}: 无 frontmatter，不进 prompts.json")
            continue
        meta = yaml.safe_load(m.group(1)) or {}
        items.append({
            "file": str(rel).replace("\\", "/"),
            "name": pf.stem,
            "meta": meta,
            "body": text[m.end():],
        })
    dst_dir.mkdir(parents=True, exist_ok=True)
    with open(dst_dir / "prompts.json", "w", encoding="utf-8") as fh:
        json.dump(items, fh, indent=2, ensure_ascii=False)
    _log(f"  ✓ prompts.json（{len(items)} 个模板）")
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="同步能力层资产到专家包")
    ap.add_argument("--check", action="store_true", help="只检查是否过期，不同步")
    args = ap.parse_args()

    if args.check:
        # 简易过期检查：比较 rubric 源文件与产物的修改时间
        stale = []
        for yf in sorted((CAP / "rubrics").glob("*.yaml")):
            jf = ASSETS / "rubrics" / f"{yf.stem}.json"
            if not jf.exists() or yf.stat().st_mtime > jf.stat().st_mtime:
                stale.append(yf.name)
        if stale:
            print("以下资产已过期，请运行 sync_assets.py：")
            for s in stale:
                print(f"  · {s}")
            return 1
        print("资产均为最新 ✓")
        return 0

    _log(f"同步能力层资产 → {ASSETS}")
    n_sch = sync_schemas()
    n_rub = sync_rubrics()
    n_pro = sync_prompts()
    n_pak = sync_packs()
    _log(f"完成：schemas {n_sch} / rubrics {n_rub} / prompts {n_pro} / packs {n_pak} 个文件")
    _log(f"同步时间 {_now()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
