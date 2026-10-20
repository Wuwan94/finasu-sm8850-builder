#!/usr/bin/env bash
# ==============================================================================
#  一键把本目录变成可推送的 GitHub 仓库，并把改动推上去
#
#  用法:
#    bash scripts/init-repo.sh                       # 交互式，按提示走
#    bash scripts/init-repo.sh folksu-sm8850-builder # 指定仓库名（默认 public）
#    PRIVATE=1 bash scripts/init-repo.sh             # 建私有仓库
#    REMOTE_URL=git@github.com:me/repo.git bash scripts/init-repo.sh   # 推到已有仓库
#
#  行为:
#    1. 没有 .git 就 git init（分支 main）
#    2. 提交当前全部文件（首次提交信息可改）
#    3. 有 gh 命令 → gh repo create --source=. --push
#       有 REMOTE_URL → 直接 add remote + push
#       都没有 → 打印手动创建后要跑的两条命令
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
info() { printf '\033[1;34m  ·\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

REPO_NAME="${1:-$(basename "$REPO_ROOT")}"
VISIBILITY="--public"
if [ "${PRIVATE:-0}" = "1" ]; then VISIBILITY="--private"; fi
COMMIT_MSG="${COMMIT_MSG:-feat: FolkSU(FinaSU) 适配 + AK3 构建脚本}"

# ---- 0. git 身份（缺了 commit 会直接失败，给个可读提示）
if ! git config user.name >/dev/null 2>&1 || ! git config user.email >/dev/null 2>&1; then
    die "git 还没配置身份，先跑：
    git config --global user.name  \"你的名字\"
    git config --global user.email \"你的邮箱\""
fi

# ---- 1. 初始化仓库
if [ ! -d .git ]; then
    log "初始化 git 仓库（main 分支）"
    git init -q -b main
else
    info "已是 git 仓库：$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo 无分支)"
fi

# ---- 2. 提交
log "提交改动"
git add -A
if git diff --cached --quiet; then
    info "没有新改动，跳过提交"
else
    git commit -q -m "$COMMIT_MSG"
    info "$(git log -1 --oneline)"
fi

# ---- 3. 推送
if [ -n "${REMOTE_URL:-}" ]; then
    log "推送到已有仓库：$REMOTE_URL"
    git remote get-url origin >/dev/null 2>&1 \
        && git remote set-url origin "$REMOTE_URL" \
        || git remote add origin "$REMOTE_URL"
    git push -u origin HEAD
elif command -v gh >/dev/null 2>&1; then
    log "用 gh 创建仓库并推送"
    if git remote get-url origin >/dev/null 2>&1; then
        info "origin 已存在，直接 push"
        git push -u origin HEAD
    else
        gh repo create "$REPO_NAME" $VISIBILITY --source=. --remote=origin --push
    fi
else
    log "本机没有 gh 命令，手动两步就好"
    cat <<EOF

  1) 在 GitHub 上新建一个空仓库（不要勾 README），名字随意，例如：$REPO_NAME
  2) 回到本目录执行：

     git remote add origin https://github.com/<你的用户名>/$REPO_NAME.git
     git push -u origin main

  想以后一条命令搞定，装个 gh：https://cli.github.com/
EOF
    exit 0
fi

log "完成"
info "仓库地址：$(git remote get-url origin 2>/dev/null || echo '—')"
info "下一步：Actions 里跑 “Build SM8850 Kernel (FolkSU / FinaSU)” 即可出 AK3 包"
