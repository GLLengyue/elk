"""
paths.py — 统一路径解析

为什么单独抽一层
----------------
数据流水线的脚本散在 fetch/parse/build/qc 四个包里，各自用
`Path(__file__).resolve().parents[2]` 推断根目录也没错——但那样就把
"仓库必须叫某个名字、必须放在某个位置"写死进了代码。

发布成开源项目后，别人可能 clone 到任意路径、可能想让数据落在另一个盘。
所以这里集中管三件事：

    1. `repo_root()`   —— 随仓分发的静态资源（schemas/ rubrics/ prompts/）
    2. `data_root()`   —— 用户数据，默认 `<repo>/data`，可用环境变量覆盖
    3. `ensure_dirs()` —— 首次运行时把目录骨架建出来（幂等）

环境变量（全部可选，给了就用，不给就用默认值）
------------------------------------------
    ELK_ROOT      覆盖仓库根（静态资源位置）
    ELK_DATA_DIR  覆盖数据目录（默认 <repo_root>/data）

不设任何环境变量也能跑，这是开箱即用的前提。
"""

from __future__ import annotations

import os
from pathlib import Path

_ENV_ROOT = "ELK_ROOT"
_ENV_DATA = "ELK_DATA_DIR"

# 目录骨架：首次运行时创建，已存在则跳过
SUBDIRS = (
    "raw/reading", "raw/writing", "raw/speaking",
    "interim", "structured", "eval", "index",
)


def repo_root() -> Path:
    """随仓分发的静态资源根目录（schemas/ rubrics/ prompts/ 在这里）。"""
    env = os.getenv(_ENV_ROOT)
    if env:
        return Path(env).expanduser().resolve()
    # src/elk/paths.py → parents[0]=elk, [1]=src, [2]=repo
    return Path(__file__).resolve().parents[2]


def data_root() -> Path:
    """数据根目录。默认 <repo>/data，可用 ELK_DATA_DIR 覆盖。"""
    env = os.getenv(_ENV_DATA)
    if env:
        return Path(env).expanduser().resolve()
    return repo_root() / "data"


def schemas_dir() -> Path:
    return repo_root() / "schemas"


def rubrics_dir() -> Path:
    return repo_root() / "rubrics"


def prompts_dir() -> Path:
    return repo_root() / "prompts"


def state_dir() -> Path:
    return data_root() / "state"


def db_path() -> Path:
    return state_dir() / "elk.db"


def index_path() -> Path:
    return data_root() / "index.jsonl"


def structured_dir() -> Path:
    return data_root() / "structured"


def ensure_dirs() -> list[Path]:
    """创建数据目录骨架。幂等，返回本次新建的目录列表。"""
    made = []
    root = data_root()
    for sub in SUBDIRS:
        p = root / sub
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            made.append(p)
    st = state_dir()
    if not st.exists():
        st.mkdir(parents=True, exist_ok=True)
        made.append(st)
    return made
