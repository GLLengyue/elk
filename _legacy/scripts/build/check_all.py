#!/usr/bin/env python3
"""
check_all.py — 系统健康状态的一站式门禁

为什么需要它
------------
检查散落成四五个脚本，时间一长就没人跑得全。改了 rubric 忘了跑契约检查、
改了 schema 忘了跑数据校验——这类遗漏最难查，因为症状出现在很远的下游。

本脚本把门禁集中成一条命令。当前项目没有 git 版本库（挂不了 pre-commit
hook），等以后初始化了，直接把这一行塞进 `.git/hooks/pre-commit` 即可：

    ./.venv/bin/python scripts/build/check_all.py || exit 1

检查项（失败即中断，按依赖顺序）
------------------------------
    1. 契约点 A/B/C    rubrics/prompts/state 与 schemas/structured 的一致性
    2. prompt 版本头   每个模板的 version + 占位符声明
    3. schema 自检     每份 schema 自身合法 + 样例数据正反例
    4. 数据校验        所有 structured 数据过 schema

用法
----
    ./.venv/bin/python scripts/build/check_all.py
    ./.venv/bin/python scripts/build/check_all.py -v      # 显示每项的通过明细
    ./.venv/bin/python scripts/build/check_all.py --quick # 只跑 1+2（秒级）
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "bin" / "python"

STEPS = [
    ("契约点 A/B/C", [str(PY), "scripts/build/check_contracts.py"], False),
    ("prompt 版本头", [str(PY), "scripts/build/prompt_loader.py", "--check"], False),
    ("schema 自检", [str(PY), "scripts/build/test_schemas.py"], False),
    ("阅读数据", [str(PY), "scripts/build/validate.py",
                "data/structured/reading/official/*.json",
                "data/structured/reading/official/taskbank/*.json",
                "--schema", "reading-test"], True),
    ("口语快照", [str(PY), "scripts/build/validate.py",
                "data/structured/speaking/seasons/*/snapshot-*.jsonl"], True),
]


def run(name: str, cmd: list[str], verbose: bool) -> tuple[bool, str]:
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    out = (p.stdout or "") + (p.stderr or "")
    ok = p.returncode == 0
    tail = ""
    if not ok or verbose:
        lines = [l for l in out.splitlines() if l.strip()]
        tail = "\n".join(lines[-8:])
    return ok, tail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--quick", action="store_true", help="只跑契约与 prompt 检查（秒级）")
    args = ap.parse_args()

    if not PY.exists():
        sys.exit(f"找不到虚拟环境解释器 {PY}")

    steps = [s for s in STEPS if not args.quick or not s[2]]
    print(f"{'检查项':<16}{'结果'}")
    print("-" * 56)

    failed = []
    for name, cmd, _ in steps:
        ok, tail = run(name, cmd, args.verbose)
        print(f"  {name:<14}{'通过' if ok else '失败'}")
        if tail and (not ok or args.verbose):
            for l in tail.splitlines():
                print(f"      {l}")
        if not ok:
            failed.append(name)

    print("-" * 56)
    if failed:
        print(f"{len(failed)} 项失败：{', '.join(failed)}")
        return 1
    print(f"全部 {len(steps)} 项通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
