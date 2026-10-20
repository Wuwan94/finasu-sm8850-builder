#!/usr/bin/env bash
# ==============================================================================
#  同步仓库内自维护的 AnyKernel3 模板（ak3/）到上游最新
#
#  用法:
#    bash scripts/update-ak3.sh              # 拉上游 main 并覆盖 ak3/
#    bash scripts/update-ak3.sh --check      # 只看差异，不写入
#
#  流程：克隆上游 → 覆盖 ak3/ → 打印差异 → 提醒你 review 后再提交
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
AK3_DIR="$REPO_ROOT/ak3"
UPSTREAM_URL="${AK3_TEMPLATE_URL:-https://github.com/cctv18/AnyKernel3.git}"

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
info() { printf '\033[1;34m  ·\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

command -v git >/dev/null 2>&1 || die "需要 git"
[ -d "$AK3_DIR" ] || die "找不到 $AK3_DIR（模板目录不该被删）"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

log "拉取上游模板：$UPSTREAM_URL"
git clone --depth=1 -q "$UPSTREAM_URL" "$TMP/upstream" || die "克隆失败"
SHA="$(git -C "$TMP/upstream" rev-parse HEAD)"
info "上游提交：$SHA"

if [ "$CHECK_ONLY" = "1" ]; then
    log "与仓库内模板的差异（--check 不写入）"
    diff -rq "$AK3_DIR" "$TMP/upstream" || true
    exit 0
fi

log "覆盖 ak3/"
# 只覆盖上游有的文件，保留我们自己的 UPSTREAM.md
( cd "$TMP/upstream" && tar cf - --exclude=.git . ) | ( cd "$AK3_DIR" && tar xf - )
rm -rf "$AK3_DIR/.git"

# 记录同步来源
cat > "$AK3_DIR/UPSTREAM.md" <<EOF
# AK3 模板来源与维护

本目录是 **AnyKernel3 刷机模板**，由本仓库自己维护（构建时不再临时 clone 第三方仓库）。
好处：模板内容固定、可离线构建、上游改版不会突然把我们的包带偏。

| 项 | 值 |
|---|---|
| 上游仓库 | $UPSTREAM_URL （派生自 https://github.com/osm0sis/AnyKernel3） |
| 同步时的提交 | \`$SHA\` |
| 许可 | 见本目录 LICENSE（GPL-3.0） |

## 怎么更新

\`\`\`bash
bash scripts/update-ak3.sh          # 覆盖 ak3/，然后 review diff 再提交
bash scripts/update-ak3.sh --check  # 只看差异
\`\`\`

## 我们改了什么

- \`anykernel.sh\` 里的 \`kernel.string\` 由构建脚本自动改写成 FinaSU 品牌 + 版本信息
- 其余文件与上游保持一致，避免自己维护出分叉
EOF

log "差异（review 后再提交）"
cd "$REPO_ROOT"
git status --short ak3/ || true
info "确认无误后：git add ak3/ && git commit -m 'chore(ak3): 同步 AnyKernel3 模板到 $SHA'"
