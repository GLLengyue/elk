#!/usr/bin/env python3
"""
release.py — 从 expert/ 源打包可分发专家包

设计意图
--------
git 仓库里存的是**完整项目源**（capability/ + data/ + expert/ + scripts/），
分发给别人的是**一份能直接用的专家包 zip**。

这两件事必须分开：
- 源项目含开发脚本、venv、抓取的原始材料，体积大且不适合直接分发
- 专家包必须自包含：拿到就能用，不需要 clone 仓库、不需要 pip install

所以 release 做三件事：
    1. 同步资产（调 sync_assets.py，保证 expert/ 里的资产是最新）
    2. 冒烟自检（跑 elk_core.py check，确保包本身可用）
    3. 打包 zip（排除 state/、__pycache__、.DS_Store 等运行时/系统垃圾）

用法
----
    python3 scripts/release.py                    # 打包到 dist/
    python3 scripts/release.py --out /tmp/        # 指定输出目录
    python3 scripts/release.py --no-sync          # 跳过资产同步（资产已确认最新时）
    python3 scripts/release.py --install          # 打包后同步到本地专家目录（开发用）
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXPERT = REPO / "expert"
SKILL = EXPERT / "skills" / "elk-coach"
SCRIPTS = REPO / "scripts"
DIST = REPO / "dist"
PLUGINS_DIR = Path.home() / ".workbuddy/plugins/marketplaces/my-experts/plugins"

# 打包时排除的东西：运行时产物 / 系统垃圾 / 版本控制
EXCLUDE_DIRS = {"state", "__pycache__", ".git", ".venv", ".pytest_cache", ".ruff_cache"}
EXCLUDE_FILES = {".DS_Store", "Thumbs.db", ".created-by-session"}


def _now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def step_sync() -> bool:
    """同步能力层资产。需要 PyYAML → 在 capability/.venv 下运行。"""
    venv_py = REPO / "capability" / ".venv" / "bin" / "python"
    py = str(venv_py) if venv_py.exists() else sys.executable
    _log(f"→ [1/3] 同步资产（python: {py}）")
    r = subprocess.run([py, str(SCRIPTS / "sync_assets.py")],
                       capture_output=True, text=True)
    if r.returncode != 0:
        _log(f"  同步失败：\n{r.stderr}")
        return False
    _log("  资产同步完成 ✓")
    return True


def step_check() -> bool:
    """冒烟自检：确认打包出来的东西能用。"""
    _log("→ [2/3] 冒烟自检")
    r = subprocess.run([sys.executable, str(SKILL / "scripts" / "elk_core.py"), "check"],
                       capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0 or "全部通过" not in out:
        _log(f"  自检未通过：\n{out}")
        return False
    _log("  自检全绿 ✓")
    return True


def step_zip(out_dir: Path) -> Path | None:
    """打包 expert/ 为 zip。"""
    _log(f"→ [3/3] 打包 → {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    meta = EXPERT / ".codebuddy-plugin" / "plugin.json"
    version = "1.0.0"
    if meta.exists():
        version = json.loads(meta.read_text(encoding="utf-8")).get("version", version)
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d")
    zip_path = out_dir / f"elk-english-coach-v{version}-{stamp}.zip"

    n = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(EXPERT.rglob("*")):
            if f.is_dir():
                continue
            rel = f.relative_to(EXPERT)
            if any(part in EXCLUDE_DIRS for part in rel.parts):
                continue
            if f.name in EXCLUDE_FILES:
                continue
            zf.write(f, Path("elk-english-coach") / rel)
            n += 1

    size_kb = zip_path.stat().st_size / 1024
    _log(f"  打包完成：{n} 个文件，{size_kb:.1f} KB → {zip_path.name}")
    return zip_path


def step_install(zip_path: Path) -> bool:
    """把打包结果同步到本地专家目录（开发时方便直接验证）。"""
    target = PLUGINS_DIR / "elk-english-coach"
    _log(f"→ 安装到本地专家目录: {target}")
    if not PLUGINS_DIR.exists():
        _log(f"  [跳过] 专家目录不存在: {PLUGINS_DIR}")
        return False
    if target.exists():
        shutil.rmtree(target)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(target.parent)
    _log("  已安装，重新开会话即生效 ✓")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="打包可分发专家包")
    ap.add_argument("--out", default=str(DIST), help="输出目录（默认 dist/）")
    ap.add_argument("--no-sync", action="store_true", help="跳过资产同步")
    ap.add_argument("--no-check", action="store_true", help="跳过冒烟自检")
    ap.add_argument("--install", action="store_true", help="打包后安装到本地专家目录")
    args = ap.parse_args()

    if not args.no_sync and not step_sync():
        return 1
    if not args.no_check and not step_check():
        return 1

    zip_path = step_zip(Path(args.out))
    if not zip_path:
        return 1

    if args.install:
        step_install(zip_path)

    _log(f"\n发布完成 {_now()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
