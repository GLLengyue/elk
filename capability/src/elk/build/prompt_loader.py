#!/usr/bin/env python3
"""
prompt_loader.py — prompt 模板的加载、校验与渲染

为什么要有这层
--------------
prompt 散落在代码里是最难维护的状态：改一个判据要翻遍所有调用点，
而且无法回答"这次推理用的是哪一版 prompt"。

这层提供三件事：

1. **版本头强制校验** —— 每个模板必须声明 version / purpose / input_contract /
   output_contract。缺字段直接报错，不让"临时写一个 prompt"混进库。
2. **rubric 编译注入** —— prompt 里的 `{{RUBRIC}}` 由 `rubrics/*.yaml` 渲染，
   **不硬编码**。改 rubric 后 prompt 自动跟着变，杜绝"rubric 更新了但
   prompt 还是旧的"这种最难查的一致性问题。
3. **运行留痕** —— 每次渲染写入 `runs/prompt-runs.jsonl`，记录
   prompt_version + rubric_version + 输入 hash，支持事后复现与回归。

用法
----
    from prompt_loader import list_prompts, render, check_all

    check_all()                      # 校验所有模板的版本头
    text = render("writing/score", RUBRIC=None, FEATURES={...},
                  PROMPT_TEXT="...", ESSAY_TEXT="...")
"""

from __future__ import annotations
from elk.paths import (
    repo_root, data_root, schemas_dir, rubrics_dir, prompts_dir,
    db_path, index_path, structured_dir,
)

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml



PROMPTS = prompts_dir()
RUBRICS = rubrics_dir()
RUNS = repo_root() / "runs"

RE_FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
RE_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")

REQUIRED_FM = ("version", "purpose", "input_contract", "output_contract")


def _now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def list_prompts() -> list[Path]:
    return sorted(PROMPTS.rglob("*.md"))


def load(path: Path) -> tuple[dict, str]:
    """返回 (frontmatter, 模板正文)。"""
    text = path.read_text(encoding="utf-8")
    m = RE_FM.match(text)
    if not m:
        raise ValueError(f"{path.name}: 缺少 YAML frontmatter（--- 包裹的头部）")
    meta = yaml.safe_load(m.group(1)) or {}
    return meta, text[m.end():]


def check_all(verbose: bool = True) -> int:
    """校验所有模板的版本头。返回错误数。"""
    errs = 0
    for p in list_prompts():
        try:
            meta, body = load(p)
        except Exception as e:                       # noqa: BLE001
            print(f"  [FAIL] {p.relative_to(PROMPTS)}: {e}")
            errs += 1
            continue
        missing = [k for k in REQUIRED_FM if k not in meta]
        if missing:
            print(f"  [FAIL] {p.relative_to(PROMPTS)}: 缺字段 {missing}")
            errs += 1
            continue
        declared = set(meta.get("placeholders") or [])
        used = set(RE_PLACEHOLDER.findall(body))
        undeclared = used - declared
        unused = declared - used
        if undeclared:
            print(f"  [FAIL] {p.relative_to(PROMPTS)}: 用了未声明的占位符 {sorted(undeclared)}")
            errs += 1
            continue
        if verbose:
            note = f"  未使用: {sorted(unused)}" if unused else ""
            print(f"  [OK  ] {meta['version']:<34} 占位符 {len(used)} 个{note}")
    return errs


def render_rubric(rubric_version: str) -> str:
    """把 rubric YAML 渲染成可注入 prompt 的文本。

    与 scorer.compile_prompt 同源：都从 YAML 生成，保证 rubric 与 prompt 不漂移。
    """
    for f in sorted(RUBRICS.rglob("*.yaml")):
        d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        if d.get("rubric_version") == rubric_version:
            lines = [f"## {d.get('task', '')} — {rubric_version}", ""]
            for c in d.get("criteria", []):
                lines.append(f"### {c['id']} — {c['name']}")
                lines.append(f"判据核心：{c.get('question','')}")
                for band in sorted(c["bands"].keys(), reverse=True):
                    b = c["bands"][band]
                    lines.append(f"  Band {band}: {b['summary']}")
                    for o in b["observable"]:
                        lines.append(f"    - {o}")
                lines.append("")
            lines.append("## 半档规则")
            lines.append((d.get("half_band_rule") or {}).get("logic", "").strip())
            lines.append("")
            lines.append("## 封顶规则（独立于你的判断，触发即生效）")
            for r in (d.get("half_band_rule") or {}).get("cap_rules", []):
                lines.append(f"- {r['id']}: 若 {r['condition']} → {r['effect']}")
            return "\n".join(lines)
    raise FileNotFoundError(f"未找到 rubric_version={rubric_version}")


def render(name: str, **values) -> str:
    """渲染模板。`RUBRIC` 可由 rubric_version 自动编译。"""
    path = PROMPTS / f"{name}.v1.md"
    if not path.exists():
        cands = sorted(p for p in list_prompts() if p.stem.startswith(Path(name).name))
        if not cands:
            raise FileNotFoundError(f"未找到 prompt `{name}`")
        path = cands[0]

    meta, body = load(path)
    vals = dict(values)

    # rubric 自动编译：避免调用方手工传一大段文本，也避免手改 prompt
    if "RUBRIC" not in vals and meta.get("rubric_version"):
        vals["RUBRIC"] = render_rubric(meta["rubric_version"])

    missing = [p for p in RE_PLACEHOLDER.findall(body) if p not in vals]
    if missing:
        raise ValueError(f"{meta['version']}: 缺少占位符取值 {sorted(set(missing))}")

    out = body
    for k, v in vals.items():
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False, indent=2)
        out = out.replace("{{" + k + "}}", str(v))

    _log_run(meta, vals)
    return out


def _log_run(meta: dict, values: dict) -> None:
    """每次渲染留痕，支持事后复现与回归。"""
    RUNS.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)
    rec = {
        "at": _now(),
        "prompt_version": meta.get("version"),
        "rubric_version": meta.get("rubric_version"),
        "input_sha256": hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16],
        "input_bytes": len(blob),
    }
    with (RUNS / "prompt-runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="校验所有模板版本头")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--render", metavar="NAME", help="渲染指定模板（其余用 --set k=v）")
    ap.add_argument("--set", action="append", default=[],
                    help="占位符取值，形如 PROMPT_TEXT=xxx；可重复")
    args = ap.parse_args()

    if args.list:
        for p in list_prompts():
            try:
                m, _ = load(p)
                print(f"  {m.get('version','?'):<36} {p.relative_to(PROMPTS)}")
            except Exception as e:                   # noqa: BLE001
                print(f"  (解析失败) {p.relative_to(PROMPTS)}: {e}")
        return 0

    if args.check:
        n = check_all()
        print(f"\n{'全部通过' if n == 0 else f'{n} 个模板有问题'}")
        return 1 if n else 0

    if args.render:
        vals = {}
        for s in args.set:
            k, _, v = s.partition("=")
            vals[k.strip()] = v
        try:
            print(render(args.render, **vals))
        except (FileNotFoundError, ValueError) as e:
            print(f"[错误] {e}", file=sys.stderr)
            return 1
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
