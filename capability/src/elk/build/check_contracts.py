#!/usr/bin/env python3
"""
check_contracts.py — 强制校验能力层与数据层之间的三个契约点

为什么需要这个
--------------
架构约定如果只是写在文档里，就一定会漂移。这三个契约点必须在 CI 里跑：

    A. rubrics/ ↔ schemas/       维度命名必须一一对应
    B. prompts/ ↔ structured/    prompt 的输入契约必须能在 schema 里找到
    C. state/ ↔ index.jsonl      索引条目数必须等于库里条目数

**A 是最容易破的一条**，而且它已经破过一次：

    rubric 用 TR / CC / LR / GRA（简称）
    score-result.schema.json 用 task_response / coherence_cohesion（全称）

两套命名并存时，输出 score-result 的那一刻没人知道该写哪个。
现在 rubric 的每个 criterion 必须带 `schema_name`，由本脚本强制校验。

用法
----
    ./.venv/bin/python scripts/build/check_contracts.py
    ./.venv/bin/python scripts/build/check_contracts.py -v

退出码 0 = 三个契约点全部成立。
"""

from __future__ import annotations
from elk.paths import (
    repo_root, data_root, schemas_dir, rubrics_dir, prompts_dir,
    db_path, index_path, structured_dir,
)

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import yaml


SCHEMAS = schemas_dir()
RUBRICS = rubrics_dir()
PROMPTS = prompts_dir()
DB = db_path()
INDEX = index_path()


def load_schema(name: str) -> dict:
    return json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))


def _criteria_enum(schema: dict) -> list[str] | None:
    """在 schema 里找评分维度的枚举。

    score-result 把 criteria 放在**顶层 properties**，不在 $defs 里
    （第一版只查 $defs，结果什么都没找到，误报契约破裂）。两处都要查。
    """
    c = (schema.get("properties") or {}).get("criteria")
    if not c:
        for s in (schema.get("$defs") or {}).values():
            c = (s.get("properties") or {}).get("criteria")
            if c:
                break
    if not c:
        return None
    items = c.get("items") or {}
    props = items.get("properties") or {}
    for key in ("name", "id"):
        enum = (props.get(key) or {}).get("enum")
        if enum:
            return enum
    return None


def check_a(verbose: bool) -> int:
    """契约点 A：rubric 的维度命名 ↔ schema 的枚举。"""
    enum = None
    for s in (SCHEMAS / "score-result.schema.json",):
        if s.exists():
            enum = _criteria_enum(load_schema(s.stem.replace(".schema", "")))
    if not enum:
        print("  [FAIL] A: 在 score-result.schema.json 里找不到评分维度枚举")
        return 1

    errs = 0
    for f in sorted(RUBRICS.rglob("*.yaml")):
        d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        if "criteria" not in d:
            continue
        names = [c.get("schema_name") or c["id"] for c in d["criteria"]]
        bad = [n for n in names if n not in enum]
        if bad:
            errs += 1
            print(f"  [FAIL] A: {d.get('rubric_version')} 的维度 {bad} 不在 schema 枚举内")
            print(f"         schema 枚举: {enum}")
        elif verbose:
            print(f"  [OK  ] A: {d.get('rubric_version')} → {names}")
    return errs


def check_b(verbose: bool) -> int:
    """契约点 B：prompt 声明的输入契约能在对应 schema 里找到。

    只校验 `input_contract`（数据字段）。`params` 是**控制参数**
    （如"生成几问"的 n、temperature），它描述的是"怎么调用"而非
    "数据长什么样"，不该要求它出现在 schema 里。
    """
    from elk.build.prompt_loader import list_prompts, load

    schema_fields: dict[str, set] = {}
    for f in sorted(SCHEMAS.rglob("*.schema.json")):
        s = json.loads(f.read_text(encoding="utf-8"))
        fields = set((s.get("properties") or {}).keys())
        for d in (s.get("$defs") or {}).values():
            fields |= set((d.get("properties") or {}).keys())
        schema_fields[f.stem.replace(".schema", "")] = fields

    all_fields = set().union(*schema_fields.values()) if schema_fields else set()
    errs = 0
    for p in list_prompts():
        try:
            meta, _ = load(p)
        except Exception as e:                       # noqa: BLE001
            print(f"  [FAIL] B: {p.name}: {e}")
            errs += 1
            continue
        for field in (meta.get("input_contract") or {}):
            if field not in all_fields:
                errs += 1
                print(f"  [FAIL] B: {meta['version']} 的输入字段 `{field}` "
                      f"在任何 schema 里都不存在")
        if verbose and errs == 0:
            print(f"  [OK  ] B: {meta['version']} 输入字段 "
                  f"{list((meta.get('input_contract') or {}).keys())}")
    return errs


def check_c(verbose: bool) -> int:
    """契约点 C：index.jsonl 与 state/ielts.db 条目数一致。"""
    if not INDEX.exists():
        print("  [FAIL] C: index.jsonl 不存在（先跑 elk index）")
        return 1
    n_index = sum(1 for l in INDEX.read_text(encoding="utf-8").splitlines() if l.strip())
    if not DB.exists():
        print("  [FAIL] C: state/ielts.db 不存在")
        return 1
    conn = sqlite3.connect(str(DB))
    try:
        n_items = conn.execute("select count(*) from items").fetchone()[0]
        n_search = conn.execute("select count(*) from search").fetchone()[0]
    except sqlite3.Error as e:
        print(f"  [FAIL] C: 查表失败 {e}（先跑 build_index.py）")
        return 1
    finally:
        conn.close()

    errs = 0
    if n_index != n_items:
        errs += 1
        print(f"  [FAIL] C: index.jsonl {n_index} 条 ≠ items 表 {n_items} 行")
    if n_items != n_search:
        errs += 1
        print(f"  [FAIL] C: items 表 {n_items} 行 ≠ search(FTS5) {n_search} 行")
    if not errs and verbose:
        print(f"  [OK  ] C: index / items / search 均为 {n_index} 条")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    print("契约点 A　rubrics/ ↔ schemas/　维度命名")
    a = check_a(args.verbose)
    print("\n契约点 B　prompts/ ↔ structured/　输入字段")
    b = check_b(args.verbose)
    print("\n契约点 C　state/ ↔ index.jsonl　条目一致")
    c = check_c(args.verbose)

    total = a + b + c
    print(f"\n{'三个契约点全部成立' if total == 0 else f'{total} 处契约破裂'}")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
