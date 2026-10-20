#!/usr/bin/env bash
# ==============================================================================
#  把【已经编译好】的内核 Image 打成 AnyKernel3 刷机包（不重新编译，几秒钟）
#
#  用法:
#    bash pack_ak3.sh <Image 路径>
#    bash pack_ak3.sh work/common/out/arch/arm64/boot/Image
#
#  可选（环境变量，都有默认值）:
#    MANIFEST        oppo+oplus+realme   机型标识，只影响包名
#    KSU_TYPENAME    FinaSU              管理器名，只影响包名与 ak3.txt
#    KSU_VERSION     32960               内核里的 KSU 版本号
#    KERNEL_VERSION  6.12.23             内核基线
#    CUSTOM_SUFFIX   android16-5-...-4k  内核后缀
#    WITH_LZ4KD      0                    1=附带 zram.zip（lz4kd 后端）
#    WITH_KPM        0                    1=附带 kpn.zip（KPatch-Next）
#    OUT_DIR         ./dist               输出目录
#
#  产物: <OUT_DIR>/AnyKernel3_*.zip + .sha256
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
info() { printf '\033[1;34m  ·\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

IMAGE="${1:-}"
if [ -z "$IMAGE" ] || [ ! -f "$IMAGE" ]; then
    die "用法：bash $0 <Image 路径>（内核编译产物 arch/arm64/boot/Image）"
fi

MANIFEST="${MANIFEST:-oppo+oplus+realme}"
KSU_TYPENAME="${KSU_TYPENAME:-FinaSU}"
KSU_VERSION="${KSU_VERSION:-32960}"
KERNEL_VERSION="${KERNEL_VERSION:-6.12.23}"
CUSTOM_SUFFIX="${CUSTOM_SUFFIX:-android16-5-ga8f88ad96df3-ab13929693-4k}"
WITH_LZ4KD="${WITH_LZ4KD:-0}"
WITH_KPM="${WITH_KPM:-0}"
OUT_DIR="${OUT_DIR:-$SCRIPT_DIR/../dist}"
WORK="${WORKDIR:-$SCRIPT_DIR/../.ak3work}"
AK3_REPO="${AK3_REPO:-https://github.com/cctv18/AnyKernel3}"

mkdir -p "$OUT_DIR" "$WORK"
cd "$WORK"

log "准备 AnyKernel3 模板"
if [ ! -d AnyKernel3/.git ]; then
    rm -rf AnyKernel3
    git clone --depth=1 -q "$AK3_REPO" AnyKernel3 || die "克隆 AnyKernel3 失败"
fi
git -C AnyKernel3 fetch --depth=1 -q origin || true
git -C AnyKernel3 reset --hard -q origin/HEAD 2>/dev/null || true
rm -rf AnyKernel3/.git

log "放入内核镜像"
cp -f "$IMAGE" AnyKernel3/Image
info "$(du -h AnyKernel3/Image | cut -f1)  $(dirname "$IMAGE")/$(basename "$IMAGE")"

cd AnyKernel3
if [ "$WITH_LZ4KD" = "1" ]; then
    info "附带 lz4kd 的 zram 模块包"
    curl -fsSLO "https://raw.githubusercontent.com/${UPSTREAM_REPO:-Wuwan94/oppo_oplus_realme_sm8850}/main/zram.zip"
fi
if [ "$WITH_KPM" = "1" ]; then
    info "附带 KPatch-Next 的 KPM 包"
    curl -fsSLO https://github.com/cctv18/KPatch-Next/releases/latest/download/kpn.zip
fi

ZIP_NAME="AnyKernel3_${KSU_TYPENAME}_${MANIFEST}_${KSU_VERSION}_${KERNEL_VERSION}_${CUSTOM_SUFFIX}"
if [ "$WITH_LZ4KD" = "1" ]; then ZIP_NAME="${ZIP_NAME}_lz4kd"; fi
if [ "$WITH_KPM" = "1" ]; then ZIP_NAME="${ZIP_NAME}_kpm"; fi
ZIP_NAME="${ZIP_NAME}_$(date +%Y%m%d).zip"

{
    echo "Root        : ${KSU_TYPENAME} ${KSU_VERSION}"
    echo "Kernel      : ${KERNEL_VERSION} (-${CUSTOM_SUFFIX})"
    echo "Manifest    : ${MANIFEST}"
    echo "Built at    : $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
    echo "Packed by   : pack_ak3.sh"
} > ak3.txt

log "打包 $ZIP_NAME"
if command -v zip >/dev/null 2>&1; then
    zip -qr "$OUT_DIR/$ZIP_NAME" ./*
    zip -z "$OUT_DIR/$ZIP_NAME" < ak3.txt >/dev/null
else
    # 没有 zip 命令时（Windows / 精简环境）用 python 兜底，只是少了压缩包注释
    info "系统没有 zip，改用 python3 打包（不含注释）"
    python3 - "$OUT_DIR/$ZIP_NAME" <<'PY'
import os, sys, zipfile
out = sys.argv[1]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for root, _dirs, files in os.walk("."):
        for name in files:
            p = os.path.join(root, name)
            z.write(p, os.path.relpath(p, "."))
PY
fi
[ -f "$OUT_DIR/$ZIP_NAME" ] || die "打包失败：$OUT_DIR/$ZIP_NAME 不存在"

cd "$OUT_DIR"
if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$ZIP_NAME" > "$ZIP_NAME.sha256"
else
    shasum -a 256 "$ZIP_NAME" > "$ZIP_NAME.sha256"
fi

log "完成"
info "刷机包：$OUT_DIR/$ZIP_NAME"
info "校验值：$(cat "$ZIP_NAME.sha256")"
