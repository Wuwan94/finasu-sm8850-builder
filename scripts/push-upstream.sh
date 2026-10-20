#!/usr/bin/env bash
# ==============================================================================
#  把 FinaSU 适配推回 Wuwan94/oppo_oplus_realme_sm8850（或你自己的 fork）
#
#  用法:
#    bash scripts/push-upstream.sh                  # 建分支 + 提交，问你要不要推
#    bash scripts/push-upstream.sh --push           # 建分支 + 提交 + push
#    bash scripts/push-upstream.sh --push --pr      # 再开一个 PR（需要 gh）
#    bash scripts/push-upstream.sh --repo git@github.com:me/repo.git --push
#    bash scripts/push-upstream.sh --dry-run        # 只做到 commit，不碰远程
#
#  会做三件事:
#    1. 克隆上游（默认 main），新建分支 feat/finasu-support
#    2. 注入 FinaSU 支持（14 个文件：7 个 workflow + 7 个本地脚本）
#       优先 git apply 补丁，没有补丁就用 scripts/apply_finasu.py 现算（两者输出等价）
#    3. 附带 local/pack_ak3.sh（Image → AK3 打包脚本），一起提交
#
#  注意: 改 .github/workflows 需要 token 带 workflow 权限；用 HTTPS 时
#        credential helper 里的 PAT 要勾 workflow。
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PATCH="$REPO_ROOT/patches/wuwan94-add-finasu.patch"
# 本仓库里的补丁位置（如果是从 finasu-sm8850-builder 里跑）
[ -f "$PATCH" ] || PATCH="$REPO_ROOT/../patches/wuwan94-add-finasu.patch"

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
info() { printf '\033[1;34m  ·\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

REPO_URL="${UPSTREAM_URL:-https://github.com/Wuwan94/oppo_oplus_realme_sm8850.git}"
BRANCH="${BRANCH:-feat/finasu-support}"
DO_PUSH=0
DO_PR=0
WORK="${WORKDIR:-$REPO_ROOT/work/upstream}"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --push) DO_PUSH=1; shift ;;
        --pr) DO_PR=1; DO_PUSH=1; shift ;;
        --dry-run) DO_PUSH=0; shift ;;
        --repo) REPO_URL="$2"; shift 2 ;;
        --branch) BRANCH="$2"; shift 2 ;;
        -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "未知参数：$1" ;;
    esac
done

[ -f "$PATCH" ] || die "找不到补丁：$PATCH（先在 finasu-sm8850-builder 的上一级目录找 patches/）"

log "准备上游工作副本"
mkdir -p "$(dirname "$WORK")"
if [ ! -d "$WORK/.git" ]; then
    git clone --depth=50 "$REPO_URL" "$WORK" || die "克隆 $REPO_URL 失败"
else
    info "复用 $WORK"
    git -C "$WORK" fetch --depth=50 origin || true
fi
cd "$WORK"

git checkout -q -B "$BRANCH" origin/HEAD 2>/dev/null || git checkout -q -B "$BRANCH"
info "分支：$BRANCH（基于 $(git rev-parse --short HEAD)）"

log "注入 FinaSU 支持"
git checkout -q -- . 2>/dev/null || true
if [ -f "${PATCH:-}" ] && git apply --check "$PATCH" 2>/dev/null; then
    git apply "$PATCH"
    info "已应用补丁 $PATCH"
elif command -v python3 >/dev/null 2>&1; then
    info "没有可用补丁，改用 scripts/apply_finasu.py 现场注入"
    python3 "$SCRIPT_DIR/apply_finasu.py" . || die "注入失败：上游结构变了，看上面的 [FAIL] 行"
else
    die "既没有可用的补丁，也没有 python3"
fi
info "已修改 $(git diff --name-only | wc -l) 个文件"

log "附带 AK3 打包脚本"
mkdir -p local
cp "$SCRIPT_DIR/pack-ak3.sh" local/pack_ak3.sh
chmod +x local/pack_ak3.sh
info "local/pack_ak3.sh"

log "提交"
git add -A
git -c core.fileMode=false commit -q -m "feat: 支持 FinaSU(FinaSU) 分支 + 新增 Image 转 AK3 脚本

- workflow / 本地脚本新增 finasu 选项（7 个 fastbuild + 7 个 builder）
- 内核侧：克隆 LyraVoid/FinaSU 指定 tag，校验 KSU_VERSION / UAPI / 管理器签名
- 签名：KSU_EXPECTED_SIZE / KSU_EXPECTED_HASH 由 APK 实测得出（v1.0.4 = 0x103 / f7cecef2…）
- SUSFS：FinaSU 内核树已重构，强制关闭，避免产出假 SUSFS 包
- local/pack_ak3.sh：只把已有 Image 打成 AnyKernel3 包，不重新编译"
info "$(git log -1 --oneline)"

if [ "$DO_PUSH" != "1" ]; then
    log "已停在本地（未推送）"
    cat <<EOF
检查一下：
  cd $WORK
  git show --stat
确认没问题后：
  git push -u origin $BRANCH
  （或者重跑：bash $0 --push）
EOF
    exit 0
fi

log "推送到 $REPO_URL"
git push -u origin "$BRANCH" || die "推送失败：检查 git 凭据 / 是否有 workflow 权限"

if [ "$DO_PR" = "1" ]; then
    if command -v gh >/dev/null 2>&1; then
        log "创建 PR"
        gh pr create --fill --base main --head "$BRANCH" || info "PR 创建失败，可以手动开"
    else
        info "没有 gh，手动开 PR：$(git remote get-url origin | sed 's/\.git$//')/compare/$BRANCH?expand=1"
    fi
fi

log "完成"
info "分支已推送：$BRANCH"
info "Actions 里对应 workflow 的 KSU分支 下拉框会多出 finasu"
