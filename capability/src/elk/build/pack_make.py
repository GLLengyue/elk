"""
pack_make.py — 把已有结构化数据打包成 SKILL 数据包

为什么单独有这个
----------------
数据包的消费者是 `elk load`，生产者就是这里。把"打包"做成一等命令，
是为了让**非商用样题与将来的正式授权数据走完全相同的流程**——
届时替换数据只需要换一个 pack_id，代码与组织方式都不用动。

产生的目录结构
--------------
    <out>/<pack_id>/
    ├── pack.json        清单（契约见 schemas/pack.schema.json）
    ├── SOURCES.md       来源清单（若源目录有则复制）
    ├── LICENSE          数据包自身许可（若有则复制）
    └── data/
        ├── reading/     *.json
        ├── speaking/    *.jsonl
        └── writing/     *.jsonl

用法
----
    elk pack <源目录> --id reading-official --version 1.0.0 --out packs/

    elk pack ../ielts-data/data/structured \\
        --id reading-official-sample --version 0.1.0 \\
        --licence "IELTS Partners - personal non-commercial use only" \\
        --out packs/
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from elk.paths import repo_root

# 科目 → 文件模式
PATTERNS = {
    "reading": ("*.json",),
    "speaking": ("*.jsonl",),
    "writing": ("*.jsonl", "*.json"),
}


def _now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def scan(src: Path) -> tuple[dict, dict]:
    """扫描源目录，返回 (按科目分组的文件, 统计)。"""
    found: dict[str, list[Path]] = {}
    counts: dict[str, int] = {"reading_items": 0, "reading_questions": 0,
                              "speaking_topics": 0, "speaking_questions": 0,
                              "writing_prompts": 0, "writing_essays": 0}
    seasons: set[str] = set()
    source_types: set[str] = set()

    for mod, pats in PATTERNS.items():
        files: list[Path] = []
        for pat in pats:
            files += [f for f in src.rglob(pat) if mod in f.parts]
        found[mod] = sorted(set(files))

    # 统计
    for f in found.get("reading", []):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if "passage" not in d or "question_groups" not in d:
            continue
        counts["reading_items"] += 1
        counts["reading_questions"] += sum(
            len(g.get("questions", [])) for g in d["question_groups"])
        st = (d.get("source") or {}).get("source_type")
        if st:
            source_types.add(st)

    for f in found.get("speaking", []):
        for ln in f.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                d = json.loads(ln)
            except json.JSONDecodeError:
                continue
            counts["speaking_topics"] += 1
            counts["speaking_questions"] += (
                len(d.get("questions") or []) + len(d.get("part3_questions") or []))
            if d.get("season"):
                seasons.add(str(d["season"]))
            st = (d.get("source") or {}).get("kind")
            if st:
                source_types.add(st)

    for f in found.get("writing", []):
        n = sum(1 for l in f.read_text(encoding="utf-8").splitlines() if l.strip())
        counts["writing_essays"] += n

    stats = {"counts": counts, "seasons": sorted(seasons),
             "source_types": sorted(source_types)}
    return found, stats


def make_pack(src: str, pack_id: str, version: str, out: str,
              licence: str | None = None, redistributable: bool | None = None,
              title: str | None = None, notes: str | None = None,
              pack: bool = False) -> Path:
    src_dir = Path(src).expanduser().resolve()
    if not src_dir.exists():
        raise FileNotFoundError(f"源目录不存在：{src_dir}")

    found, stats = scan(src_dir)
    total = sum(len(v) for v in found.values())
    if total == 0:
        raise ValueError(f"源目录里没有找到任何结构化数据：{src_dir}")

    # 未显式声明时按来源推断，宁可保守
    if redistributable is None:
        redistributable = "official_sample" not in stats["source_types"] and \
            "third_party" not in stats["source_types"]

    manifest = {
        "pack_id": pack_id,
        "pack_version": version,
        "schema_version": "1.0.0",
        "title": title or pack_id,
        "modules": [m for m, v in found.items() if v],
        "licence": {"name": licence or "unspecified",
                    "redistributable": bool(redistributable)},
        "redistributable": bool(redistributable),
        "contents": stats,
        "notes": notes or "",
    }

    dest_root = Path(out).expanduser().resolve() / pack_id
    if dest_root.exists():
        shutil.rmtree(dest_root)
    (dest_root / "data").mkdir(parents=True, exist_ok=True)

    for mod, files in found.items():
        if not files:
            continue
        for f in files:
            rel = f.relative_to(src_dir)
            target = dest_root / "data" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)

    for name in ("SOURCES.md", "LICENSE", "LICENSE.md", "NOTICE", "README.md"):
        f = src_dir / name
        if f.exists():
            shutil.copy2(f, dest_root / name)

    (dest_root / "pack.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if pack:
        import shutil as sh
        archive = sh.make_archive(str(dest_root), "zip", root_dir=dest_root.parent,
                                  base_dir=dest_root.name)
        return Path(archive)
    return dest_root


def main() -> int:
    ap = argparse.ArgumentParser(prog="elk pack")
    ap.add_argument("src", help="源目录（含 reading/ speaking/ writing/ 等）")
    ap.add_argument("--id", required=True, help="数据包 id，如 reading-official")
    ap.add_argument("--version", default="1.0.0")
    ap.add_argument("--out", default="packs")
    ap.add_argument("--licence", help="许可名称")
    ap.add_argument("--redistributable", action="store_true",
                    help="声明为可再分发（默认按来源保守推断）")
    ap.add_argument("--title")
    ap.add_argument("--notes")
    ap.add_argument("--zip", action="store_true", help="额外产出 .zip")
    ns = ap.parse_args()

    try:
        out = make_pack(ns.src, ns.id, ns.version, ns.out,
                        licence=ns.licence,
                        redistributable=ns.redistributable or None,
                        title=ns.title, notes=ns.notes, pack=ns.zip)
    except (FileNotFoundError, ValueError) as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 1

    mf = json.loads((out / "pack.json").read_text(encoding="utf-8")) \
        if out.is_dir() else {}
    print(f"数据包已生成：{out}")
    if mf:
        c = mf["contents"]["counts"]
        print(f"  阅读 {c['reading_items']} 篇 / {c['reading_questions']} 题")
        print(f"  口语 {c['speaking_topics']} 题组 / {c['speaking_questions']} 问")
        print(f"  可再分发：{'是' if mf['redistributable'] else '否 ⚠'}")
    print(f"\n加载：elk load {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
