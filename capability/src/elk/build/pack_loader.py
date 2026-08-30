"""
pack_loader.py — 数据包（Data Pack）的加载、卸载与登记

设计前提
--------
**SKILL 本身不含数据。** 所有题目由数据包提供。

这么做有两个理由：

1. **合规** —— 真实考试材料受版权约束，不能随仓分发（见 docs/compliance.md）。
2. **商业** —— 高质量合法数据集可以独立定价。把它做成"可替换的数据包"，
   同一套代码既能跑非商用的样题，也能跑正式授权数据，组织方式完全一致。

换句话说：**SKILL 卖的是契约与 QC，不是题。**

数据包形态
----------
支持两种，加载器自动识别：

    目录     packs/my-pack/          开发时用，方便 diff 与版本管理
    压缩包   packs/my-pack.zip       分发时用

解压/读取后，内容按 `pack_id` 隔离放进 `data/structured/<pack_id>/`，
因此多个包可以共存，卸载时直接删目录，不会互相污染。

清单文件 pack.json
------------------
每个数据包根目录必须有 `pack.json`，契约见 `schemas/pack.schema.json`。
关键字段：`pack_id` / `pack_version` / `licence` / `redistributable` / `contents`。

`redistributable: false` 的付费包，加载器照常工作，但会在列出时显著标注——
防止有人误把不可分發的数据当成可公开的产物。

用法
----
    elk load packs/my-pack          加载（目录或 zip 均可）
    elk load packs/my-pack --force  覆盖已存在的同名包
    elk packs                       列出已加载的数据包
    elk unload my-pack              卸载
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

from elk.paths import data_root, schemas_dir, state_dir, structured_dir

MANIFEST = "pack.json"
REGISTRY = "packs.jsonl"


def _now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def _registry_path() -> Path:
    st = state_dir()
    st.mkdir(parents=True, exist_ok=True)
    return st / REGISTRY


def _validate_manifest(manifest: dict) -> list[str]:
    """校验清单。返回错误列表，空即通过。"""
    try:
        import jsonschema
    except ImportError:
        # 没有 jsonschema 时退回必填字段检查，保证功能不瘫
        req = ("pack_id", "pack_version", "schema_version", "licence", "modules")
        return [f"缺字段 {f}" for f in req if f not in manifest]

    from elk.build.validate import REGISTRY
    schema = json.loads((schemas_dir() / "pack.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema, registry=REGISTRY)
    return [f"{'/'.join(str(p) for p in e.path)}: {e.message}"
            for e in validator.iter_errors(manifest)]


def _read_registry() -> list[dict]:
    p = _registry_path()
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
    return out


def _append_registry(rec: dict) -> None:
    # 先按 pack_id 去重：force 覆盖时旧登记仍在，直接追加会留下重复行（实测踩到）。
    # 幂等写入 —— 同 pack_id 只保留最新一条。
    existing = _read_registry()
    existing = [r for r in existing if r.get("pack_id") != rec["pack_id"]]
    existing.append(rec)
    with _registry_path().open("w", encoding="utf-8") as fh:
        for r in existing:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _resolve_pack(src: str) -> tuple[Path, bool]:
    """解析数据包路径。返回 (实际目录, 是否是临时目录需清理)。"""
    p = Path(src).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"数据包不存在：{p}")
    if p.is_dir():
        return p.resolve(), False
    if p.suffix.lower() == ".zip":
        tmp = Path(tempfile.mkdtemp(prefix="elk-pack-"))
        with zipfile.ZipFile(p) as z:
            z.extractall(tmp)
        # zip 里可能多包一层同名目录，自动下钻
        entries = list(tmp.iterdir())
        if len(entries) == 1 and entries[0].is_dir() and (entries[0] / MANIFEST).exists():
            return entries[0], True
        return tmp, True
    raise ValueError(f"不支持的数据包形态：{p}（只支持目录或 .zip）")


def _dir_checksum(root: Path) -> str:
    """对 data/ 下所有文件算聚合 sha256（按相对路径排序，保证可复现）。"""
    h = hashlib.sha256()
    for f in sorted(root.rglob("*"), key=lambda x: str(x.relative_to(root))):
        if f.is_file():
            h.update(str(f.relative_to(root)).encode("utf-8"))
            h.update(f.read_bytes())
    return h.hexdigest()


def load_pack(src: str, force: bool = False, reindex: bool = True) -> dict:
    """加载一个数据包。返回登记记录。"""
    pack_dir, is_tmp = _resolve_pack(src)
    try:
        mf_path = pack_dir / MANIFEST
        if not mf_path.exists():
            raise ValueError(f"数据包缺少清单文件 {MANIFEST}：{pack_dir}")
        manifest = json.loads(mf_path.read_text(encoding="utf-8"))

        errs = _validate_manifest(manifest)
        if errs:
            raise ValueError(f"清单校验失败：{'; '.join(errs[:5])}")

        pack_id = manifest["pack_id"]
        dest = structured_dir() / pack_id
        if dest.exists():
            if not force:
                raise FileExistsError(
                    f"数据包 `{pack_id}` 已加载于 {dest}。加 --force 覆盖。")
            shutil.rmtree(dest)

        src_data = pack_dir / "data"
        if not src_data.exists():
            raise ValueError(f"数据包缺少 data/ 目录：{pack_dir}")

        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_data, dest)

        # 附带文档（SOURCES.md / LICENSE）一并保留，便于合规追溯
        docs = []
        for name in ("SOURCES.md", "LICENSE", "LICENSE.md", "NOTICE"):
            f = pack_dir / name
            if f.exists():
                shutil.copy2(f, dest / name)
                docs.append(name)

        n_items = sum(1 for _ in dest.rglob("*") if _.is_file()) - len(docs)
        checksum = _dir_checksum(dest)

        declared = manifest.get("checksum")
        if declared and declared.startswith("sha256:") and \
                declared[7:] != checksum:
            # 不阻断加载，但明确告警——校验和不符意味着数据被改动过
            print(f"  [警告] 校验和不符：清单声明 {declared[7:][:12]}… "
                  f"实际 {checksum[:12]}…", file=sys.stderr)

        rec = {
            "pack_id": pack_id,
            "pack_version": manifest.get("pack_version"),
            "title": manifest.get("title"),
            "modules": manifest.get("modules", []),
            "redistributable": manifest.get("redistributable",
                                            manifest.get("licence", {}).get("redistributable")),
            "licence": (manifest.get("licence") or {}).get("name"),
            "source": src,
            "installed_at": _now(),
            "path": str(dest.relative_to(data_root())),
            "files": n_items,
            "checksum": f"sha256:{checksum}",
            "declared_counts": (manifest.get("contents") or {}).get("counts", {}),
        }
        _append_registry(rec)

        if reindex:
            # 必须重置 sys.argv：build_index 原本是独立脚本，会读命令行参数
            # （与 cli._run_sub 同一个坑，这里直接调就会报
            #  "unrecognized arguments: load <path>"）
            old_argv = sys.argv
            sys.argv = ["build_index"]
            try:
                from elk.build.build_index import main as _main
                _main()
            except Exception as e:                       # noqa: BLE001
                print(f"  [警告] 索引重建失败：{e}", file=sys.stderr)
            finally:
                sys.argv = old_argv
        return rec
    finally:
        if is_tmp:
            shutil.rmtree(pack_dir, ignore_errors=True)


def unload_pack(pack_id: str) -> bool:
    """卸载数据包：删目录 + 从登记中移除。"""
    dest = structured_dir() / pack_id
    removed = False
    if dest.exists():
        shutil.rmtree(dest)
        removed = True
    recs = [r for r in _read_registry() if r.get("pack_id") != pack_id]
    with _registry_path().open("w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return removed


def list_packs() -> list[dict]:
    return _read_registry()
