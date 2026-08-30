"""
check_all.py — 系统健康状态的一站式门禁

为什么需要它
------------
检查散落成四五个模块，时间一长就没人跑得全。改了 rubric 忘了跑契约检查、
改了 schema 忘了跑数据校验——这类遗漏最难查，因为症状出现在很远的下游。

本模块把门禁集中成一条命令：`elk check`。

检查项（失败即中断，按依赖顺序）
------------------------------
    1. 契约点 A/B/C    rubrics/prompts/state 与 schemas/structured 的一致性
    2. prompt 版本头   每个模板的 version + 占位符声明
    3. schema 自检     每份 schema 自身合法 + 样例数据正反例
    4. 数据校验        所有 data/structured 下的数据过 schema

第 4 项在没有数据时**跳过而非失败**——新克隆还没 bootstrap 时不该报错。

用法
----
    elk check
    ./.venv/bin/python -m elk.build.check_all -v
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from elk.paths import structured_dir

PY = sys.executable


def _run(name: str, argv: list[str], verbose: bool) -> tuple[bool, str]:
    p = subprocess.run([PY, "-m", *argv], capture_output=True, text=True,
                       cwd=str(Path(__file__).resolve().parents[3]))
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
    args = ap.parse_args()

    # 数据按 pack_id 隔离在 structured/<pack_id>/reading/... ，
    # 不能按 structured/reading 直接找（那样永远找不到，会误判为"无数据"）。
    sd = structured_dir()
    rd_files = [f for f in sorted(sd.rglob("*.json")) if "reading" in f.parts]
    sp_files = [f for f in sorted(sd.rglob("*.jsonl")) if "speaking" in f.parts]

    steps = [
        ("契约点 A/B/C", ["elk.build.check_contracts"]),
        ("prompt 版本头", ["elk.build.prompt_loader", "--check"]),
        ("schema 自检", ["elk.build.test_schemas"]),
    ]
    if rd_files:
        steps.append(("阅读数据", ["elk.build.validate", "--schema", "reading-test",
                                  *[str(f) for f in rd_files]]))
    if sp_files:
        steps.append(("口语数据", ["elk.build.validate",
                                   *[str(f) for f in sp_files]]))

    print(f"{'检查项':<16}{'结果'}")
    print("-" * 56)
    failed = []
    for name, argv in steps:
        ok, tail = _run(name, argv, args.verbose)
        print(f"  {name:<14}{'通过' if ok else '失败'}")
        if tail and (not ok or args.verbose):
            for l in tail.splitlines():
                print(f"      {l}")
        if not ok:
            failed.append(name)

    skipped = 0
    if not rd_files and not sp_files:
        skipped = 2
        print("  （无结构化数据，数据校验跳过 —— 先跑 elk load <数据包>）")

    print("-" * 56)
    if failed:
        print(f"{len(failed)} 项失败：{', '.join(failed)}")
        return 1
    print(f"全部 {len(steps)} 项通过" + (f"（跳过 {skipped} 项）" if skipped else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
