#!/usr/bin/env bash
# setup.sh — 首次使用的一键初始化
#
#   ./scripts/setup.sh
#
# 做三件事：
#   1. 创建虚拟环境 .venv（不存在时）
#   2. 以可编辑模式安装本项目及依赖
#   3. 建目录骨架 + 装填示例数据 + 建索引（elk bootstrap）
#
# 不依赖任何本地特有路径：所有位置由仓库自身结构推断。

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "==> 仓库根目录：$REPO_ROOT"

# 1) 虚拟环境
if [ ! -d ".venv" ]; then
    echo "==> 创建虚拟环境 .venv（使用 ${PYTHON_BIN}）"
    "$PYTHON_BIN" -m venv .venv
else
    echo "==> 虚拟环境已存在，跳过创建"
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# 2) 安装
echo "==> 安装依赖（可编辑模式）"
python -m pip install --upgrade pip -q
python -m pip install -e . -q

# 3) bootstrap
echo "==> 初始化目录与示例数据"
python -m elk.cli bootstrap

cat <<'MSG'

────────────────────────────────────────────────────────────
 安装完成。

 后续每次使用请先激活环境：
     source .venv/bin/activate

 常用命令：
     elk check        跑全部门禁
     elk prompts      列出所有 prompt 模板
     elk render writing/score --set PROMPT_TEXT='...' --set ESSAY_TEXT='...'
                        渲染一个 prompt，看最终喂给模型的内容
     elk fetch --only reading
                        抓取官方公开样题（仅落本地，不入仓）

 文档：README.md / MILESTONES.md / docs/
────────────────────────────────────────────────────────────
MSG
