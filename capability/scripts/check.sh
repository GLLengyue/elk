#!/usr/bin/env bash
# check.sh — 跑全部门禁（等价于 elk check，供 CI 或 git hook 调用）
#
#   ./scripts/check.sh
#   # 或在 git 仓库里挂成 pre-commit：
#   #   ln -s ../../scripts/check.sh .git/hooks/pre-commit

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ -d ".venv" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

exec python -m elk.cli check "$@"
