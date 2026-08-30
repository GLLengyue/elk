"""
cli.py — 统一命令行入口

为什么要有统一入口
------------------
迁移前每个脚本各跑各的，路径靠 `Path(__file__).parents[2]` 推断。
发布成项目后，用户不该记住「先跑 examples/make_sample_data.py，
再把产物拷到 data/structured，再跑 build/build_index.py」这种顺序。

统一入口把常用动作收敛成几条命令：

    elk bootstrap    建目录骨架 + 装填示例数据 + 建索引（首次使用跑这个）
    elk check        跑全部门禁（契约点 / prompt / schema / 数据）
    elk validate     校验指定数据是否符合 schema
    elk index        重建索引与全文检索表
    elk prompts      列出所有 prompt 模板
    elk render       渲染一个 prompt（看最终喂给模型的是什么）
    elk paths        打印当前解析到的路径（排查路径问题用）

任何命令都不依赖本地特有路径——根目录由环境变量或包位置推断，
详见 elk/paths.py。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from elk.paths import (
    data_root, db_path, ensure_dirs, index_path, prompts_dir,
    repo_root, structured_dir,
)

VERSION = "0.1.0"


def _print_paths() -> None:
    print(f"repo_root     {repo_root()}")
    print(f"data_root     {data_root()}")
    print(f"structured    {structured_dir()}")
    print(f"index         {index_path()}")
    print(f"database      {db_path()}")
    print(f"prompts       {prompts_dir()}")


def cmd_bootstrap(_: argparse.Namespace) -> int:
    """建目录骨架 + 加载格式示范包 + 建索引。

    注意：**SKILL 本身不含题目数据。** bootstrap 只装一个
    `examples/packs/demo-pack`（凭空撰写的格式示范，1 篇 + 5 题），
    用来证明加载/校验/索引三步能跑通。

    真实题目由数据包提供，加载方式：
        elk load <你的数据包目录或 .zip>
    """
    made = ensure_dirs()
    print(f"目录骨架：新建 {len(made)} 个目录")

    demo = repo_root() / "examples" / "packs" / "demo-pack"
    if demo.exists():
        from elk.build.pack_loader import load_pack
        try:
            rec = load_pack(str(demo), force=True, reindex=False)
            print(f"格式示范包：{rec['pack_id']} "
                  f"（{rec['files']} 个文件，仅用于验证链路）")
        except (ValueError, FileExistsError) as e:
            print(f"  [跳过] 示范包未加载：{e}")
    else:
        print("  （未找到 examples/packs/demo-pack，跳过）")

    n = cmd_index(argparse.Namespace())
    if n:
        return n

    print("\nbootstrap 完成。")
    print("  当前只有格式示范包，不足以做练习或评测。")
    print("  加载真实数据包：elk load <路径>")
    print("  查看已加载：    elk packs")
    return 0


def _run_sub(argv: list[str], mod: str) -> int:
    """调用子模块的 main()。

    必须重置 sys.argv：这些模块原本是独立脚本，直接读命令行参数；
    不重置的话会把 `elk bootstrap` 里的 "bootstrap" 当成自己的参数
    （实测报 unrecognized arguments）。
    """
    old = sys.argv
    sys.argv = argv
    try:
        mod_obj = __import__(mod, fromlist=["main"])
        return mod_obj.main()
    finally:
        sys.argv = old


def cmd_index(_: argparse.Namespace) -> int:
    return _run_sub(["build_index"], "elk.build.build_index")


def cmd_check(_: argparse.Namespace) -> int:
    return _run_sub(["check_all"], "elk.build.check_all")


def cmd_validate(ns: argparse.Namespace) -> int:
    argv = ["validate"] + list(ns.patterns)
    if ns.schema:
        argv += ["--schema", ns.schema]
    return _run_sub(argv, "elk.build.validate")


def cmd_prompts(ns: argparse.Namespace) -> int:
    from elk.build.prompt_loader import list_prompts, load
    for p in list_prompts():
        try:
            m, _ = load(p)
            print(f"  {m.get('version','?'):<36} {p.relative_to(prompts_dir())}")
        except Exception as e:                       # noqa: BLE001
            print(f"  (解析失败) {p.relative_to(prompts_dir())}: {e}")
    return 0


def cmd_load(ns: argparse.Namespace) -> int:
    from elk.build.pack_loader import load_pack
    try:
        rec = load_pack(ns.path, force=ns.force)
    except (FileNotFoundError, ValueError, FileExistsError) as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 1
    print(f"已加载数据包 `{rec['pack_id']}` v{rec['pack_version']}")
    print(f"  路径       data/{rec['path']}")
    print(f"  文件       {rec['files']} 个")
    print(f"  科目       {', '.join(rec['modules']) or '-'}")
    print(f"  许可       {rec['licence'] or '未声明'}"
          f"{'' if rec['redistributable'] else '  ⚠ 不可再分发'}")
    print(f"  校验和     {rec['checksum'][:24]}…")
    return 0


def cmd_unload(ns: argparse.Namespace) -> int:
    from elk.build.pack_loader import unload_pack
    if not unload_pack(ns.pack_id):
        print(f"[提示] 未找到已加载的数据包 `{ns.pack_id}`")
        return 1
    print(f"已卸载 `{ns.pack_id}`。记得重建索引：elk index")
    return 0


def cmd_packs(_: argparse.Namespace) -> int:
    from elk.build.pack_loader import list_packs
    recs = list_packs()
    if not recs:
        print("尚未加载任何数据包。")
        print("  elk load <路径>    路径可以是目录或 .zip")
        return 0
    print(f"{'pack_id':<28}{'版本':<10}{'科目':<20}{'文件':<7}可再分发")
    print("-" * 78)
    for r in recs:
        mods = ",".join(r.get("modules") or [])
        rd = r.get("redistributable")
        flag = "是" if rd else ("否 ⚠" if rd is False else "未声明")
        print(f"  {r['pack_id']:<26}{str(r.get('pack_version') or '-'):<10}"
              f"{mods:<20}{r.get('files', 0):<7}{flag}")
    return 0


def cmd_render(ns: argparse.Namespace) -> int:
    from elk.build.prompt_loader import render
    vals = {}
    for s in (ns.set or []):
        k, _, v = s.partition("=")
        vals[k.strip()] = v
    try:
        print(render(ns.name, **vals))
    except (FileNotFoundError, ValueError) as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 1
    return 0


def cmd_pack(ns: argparse.Namespace) -> int:
    from elk.build.pack_make import make_pack
    try:
        out = make_pack(ns.src, ns.id, ns.version, ns.out,
                        licence=ns.licence,
                        redistributable=ns.redistributable or None,
                        title=ns.title, notes=ns.notes, pack=ns.zip)
    except (FileNotFoundError, ValueError) as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 1
    print(f"数据包已生成：{out}")
    return 0


def cmd_fetch(ns: argparse.Namespace) -> int:
    argv = ["fetch_official"]
    if ns.only:
        argv += ["--only", ns.only]
    if ns.force:
        argv += ["--force"]
    return _run_sub(argv, "elk.fetch.fetch_official")


def main() -> int:
    ap = argparse.ArgumentParser(prog="elk", description="SKILL 命令行工具")
    ap.add_argument("--version", action="version", version=f"elk {VERSION}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("bootstrap", help="建目录 + 装填示例数据 + 建索引")
    sub.add_parser("index", help="重建索引与全文检索表")
    sub.add_parser("check", help="跑全部门禁")
    sub.add_parser("paths", help="打印当前解析到的路径")

    p = sub.add_parser("validate", help="校验数据是否符合 schema")
    p.add_argument("patterns", nargs="+")
    p.add_argument("--schema")
    p.set_defaults(func=cmd_validate)

    sub.add_parser("prompts", help="列出所有 prompt 模板").set_defaults(func=cmd_prompts)
    sub.add_parser("packs", help="列出已加载的数据包").set_defaults(func=cmd_packs)

    p = sub.add_parser("load", help="加载数据包（目录或 .zip）")
    p.add_argument("path")
    p.add_argument("--force", action="store_true", help="覆盖同名数据包")
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("unload", help="卸载数据包")
    p.add_argument("pack_id")
    p.set_defaults(func=cmd_unload)

    p = sub.add_parser("pack", help="把结构化数据打包成数据包")
    p.add_argument("src")
    p.add_argument("--id", required=True)
    p.add_argument("--version", default="1.0.0")
    p.add_argument("--out", default="packs")
    p.add_argument("--licence")
    p.add_argument("--redistributable", action="store_true")
    p.add_argument("--title")
    p.add_argument("--notes")
    p.add_argument("--zip", action="store_true")
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser("render", help="渲染一个 prompt 模板")
    p.add_argument("name")
    p.add_argument("--set", action="append", default=[], metavar="K=V")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("fetch", help="抓取官方公开样题（需联网；仅下载到本地，不入仓）")
    p.add_argument("--only", choices=["reading", "writing", "speaking"])
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_fetch)

    ns = ap.parse_args()

    handlers = {
        "bootstrap": cmd_bootstrap, "index": cmd_index, "check": cmd_check,
        "paths": lambda _: (_print_paths(), 0)[1],
    }
    fn = getattr(ns, "func", None) or handlers[ns.cmd]
    return fn(ns)


if __name__ == "__main__":
    raise SystemExit(main())
