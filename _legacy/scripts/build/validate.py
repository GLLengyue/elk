#!/usr/bin/env python3
"""
结构化数据写入前的 schema 校验门禁。

用法:
  .venv/bin/python scripts/build/validate.py data/structured/speaking/seasons/*/snapshot-*.jsonl
  .venv/bin/python scripts/build/validate.py <file|glob> [--schema NAME] [--max-errors 10]

schema 选择：显式 --schema 优先；否则按文件名/顶层字段自动推断。
退出码 0 = 全部通过，1 = 有失败（供 CI/pre-commit 使用）。
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "schemas"

# 按顶层字段特征自动识别 schema。
# 用各类型**独有**的字段做签名，避免误判（task / test_type 在多份 schema 里都有）。
AUTO = {
    "writing-essay": ("essay_text", "band_source"),
    "writing-prompt": ("prompt_text", "task_family"),
    "speaking-topic": ("part", "season", "topic_name"),
    "reading-test": ("passage", "question_groups"),
    "score-result": ("estimated_band", "criteria"),
}


def build_registry() -> Registry:
    """把所有 schema 按 $id 注册进 registry，使跨文件 $ref 可用。

    为什么需要它：common/ 下定义了 licence / provenance / span / difficulty，
    被多份 schema 引用。若不支持跨文件引用，就只能在每份 schema 里各抄一遍 ——
    改一处要改 N 处，漂移只是时间问题。

    相对 $ref 的解析依赖各自的 $id：
      writing-essay  $id = .../schemas/writing-essay.schema.json
        $ref "common/provenance.schema.json"
        -> .../schemas/common/provenance.schema.json   ✓
      provenance     $id = .../schemas/common/provenance.schema.json
        $ref "licence.schema.json"
        -> .../schemas/common/licence.schema.json      ✓
    因此 $id 必须反映真实的目录层级，移动文件时要同步改 $id。
    """
    resources = []
    for path in sorted(SCHEMAS.rglob("*.schema.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SystemExit(f"schema JSON 解析失败: {path} -> {e}")
        res_id = doc.get("$id")
        if res_id:
            resources.append((res_id, Resource.from_contents(
                doc, default_specification=DRAFT202012)))
    return Registry().with_resources(resources)


REGISTRY = build_registry()


def load_schema(name: str) -> dict:
    path = SCHEMAS / f"{name}.schema.json"
    if not path.exists():
        raise SystemExit(f"schema 不存在: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def guess_schema(obj: dict) -> str | None:
    if not isinstance(obj, dict):
        return None
    keys = set(obj)
    for name, sig in AUTO.items():
        if set(sig).issubset(keys):
            return name
    return None


def iter_json(path: Path):
    """支持 .json（单对象或数组）与 .jsonl（每行一个对象）。"""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        for i, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if line:
                yield i, json.loads(line)
    else:
        data = json.loads(text)
        if isinstance(data, list):
            for i, obj in enumerate(data, 1):
                yield i, obj
        else:
            yield 1, data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", help="文件或 glob")
    ap.add_argument("--schema", default=None, help="显式指定 schema 名")
    ap.add_argument("--max-errors", type=int, default=10, help="每个文件最多报几条")
    args = ap.parse_args()

    schema_cache: dict[str, Draft202012Validator] = {}
    total = ok = failed = 0
    skipped = 0

    for pattern in args.files:
        paths = [Path(p) for p in sorted(glob.glob(pattern, recursive=True))]
        if not paths:
            print(f"! 无匹配: {pattern}")
            continue
        for path in paths:
            n_ok = n_bad = 0
            errs: list[str] = []
            for lineno, obj in iter_json(path):
                total += 1
                name = args.schema or guess_schema(obj)
                if not name:
                    skipped += 1
                    print(f"  ? {path.name}:{lineno} 无法识别 schema，跳过")
                    continue
                if name not in schema_cache:
                    schema_cache[name] = Draft202012Validator(
                        load_schema(name), registry=REGISTRY)
                v = schema_cache[name]
                if v.is_valid(obj):
                    n_ok += 1
                    continue
                n_bad += 1
                for e in sorted(v.iter_errors(obj), key=lambda x: list(x.path))[: args.max_errors]:
                    loc = "/".join(str(p) for p in e.absolute_path) or "(root)"
                    errs.append(f"    {path.name}:{lineno} [{loc}] {e.message[:150]}")
            ok += n_ok
            failed += n_bad
            status = "OK  " if n_bad == 0 else "FAIL"
            # glob 可能返回相对路径，且 macOS 文件系统大小写不敏感而 relative_to 是
            # 纯字符串比较（系统底层 Workbuddy ≠ cd 进去的 WorkBuddy），故兜底。
            try:
                disp = path.resolve().relative_to(ROOT)
            except ValueError:
                disp = path
            print(f"  [{status}] {disp}  通过 {n_ok}  失败 {n_bad}")
            for e in errs[: args.max_errors]:
                print(e)

    print(f"\n合计 {total} 条：通过 {ok}  失败 {failed}  跳过 {skipped}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
