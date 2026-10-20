#!/usr/bin/env bash
# ==============================================================================
#  欧加真 SM8850 6.12 内核构建器（FolkSU / FinaSU 适配版）
#
#  用法:
#    bash scripts/build.sh                 # 用 config/folksu.conf 的默认值
#    KERNEL_VERSION=6.12.58 bash scripts/build.sh
#    MANAGER_APK=./FinaSU_v1.0.4_32960-release.apk bash scripts/build.sh   # 自动反推签名
#    bash scripts/build.sh --check          # 只校验版本 / UAPI / 签名，不编译
#    bash scripts/build.sh --help
#
#  依赖：bash / git / curl / unzip / zip / make / gcc / bc / flex / bison（Ubuntu 22.04+）
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
info() { printf '\033[1;34m  ·\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

# ------------------------------------------------------------------ 配置
# shellcheck disable=SC1091
. "$REPO_ROOT/config/folksu.conf"

# 上游补丁出处：钉在验证过的提交上，避免上游改动把构建带崩
UPSTREAM_REPO="${UPSTREAM_REPO:-Wuwan94/oppo_oplus_realme_sm8850}"
UPSTREAM_REF="${UPSTREAM_REF:-6c1ffdac3ebf7ebb80254427a7972262b0a8f64e}"
RAW="https://raw.githubusercontent.com/${UPSTREAM_REPO}/${UPSTREAM_REF}"

KERNEL_VERSION="${KERNEL_VERSION:-6.12.23}"
case "$KERNEL_VERSION" in
    6.12.23)
        KERNEL_ZIP="https://github.com/cctv18/android_kernel_common_oneplus_sm8850/archive/refs/heads/oneplus/sm8850_v_16.0.0_oneplus_15.zip"
        DEFAULT_SUFFIX="android16-5-ga8f88ad96df3-ab13929693-4k" ;;
    6.12.38)
        KERNEL_ZIP="https://github.com/cctv18/android_kernel_common_oneplus_sm8845/archive/refs/heads/oneplus/sm8845_b_16.0.0_ace_6t.zip"
        DEFAULT_SUFFIX="android16-5-g8c67d4274c0a-ab14275539-4k" ;;
    6.12.58)
        KERNEL_ZIP="https://github.com/cctv18/android_kernel_common_oneplus_sm8850/archive/refs/heads/oneplus/sm8850_b_16.0_pad_3_pro.zip"
        DEFAULT_SUFFIX="android16-5-ga8f88ad96df3-ab13929693-4k" ;;
    6.12.69-gki)
        KERNEL_ZIP="https://github.com/cctv18/android_gki_kernel_common/archive/refs/heads/android16-6.12-2026-03.zip"
        DEFAULT_SUFFIX="android16-5-ge7f2a9832757-ab13799791-4k" ;;
    *) die "不支持的 KERNEL_VERSION=$KERNEL_VERSION（可选 6.12.23 / 6.12.38 / 6.12.58 / 6.12.69-gki）" ;;
esac
CUSTOM_SUFFIX="${CUSTOM_SUFFIX:-$DEFAULT_SUFFIX}"

USE_KPM="${USE_KPM:-0}"                       # KPatch-Next 独立 KPM（直接改 Image）
APPLY_LZ4="${APPLY_LZ4:-1}"                   # lz4 1.10.0 + zstd 1.5.7
APPLY_LZ4KD="${APPLY_LZ4KD:-0}"
APPLY_BETTERNET="${APPLY_BETTERNET:-0}"
APPLY_BBR="${APPLY_BBR:-0}"                   # 0 / 1 / default
APPLY_DROIDSPACES="${APPLY_DROIDSPACES:-0}"   # 0 / standard / extend
APPLY_ADIOS="${APPLY_ADIOS:-1}"
APPLY_REKERNEL="${APPLY_REKERNEL:-0}"
APPLY_BBG="${APPLY_BBG:-1}"
APPLY_SUSFS=0                                 # FolkSU 内核树不兼容 susfs4ksu 的 KSU 侧补丁

JOBS="${JOBS:-$(nproc --all 2>/dev/null || echo 4)}"
WORKDIR="${WORKDIR:-$REPO_ROOT/work}"
DIST_DIR="${DIST_DIR:-$REPO_ROOT/dist}"
INSTALL_DEPS="${INSTALL_DEPS:-0}"

# ------------------------------------------------------------------ 参数
CHECK_ONLY=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --check) CHECK_ONLY=1; shift ;;
        -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "未知参数：$1（用 --help 看用法）" ;;
    esac
done

need() { command -v "$1" >/dev/null 2>&1 || die "缺少命令：$1"; }
need git
need curl

# 给了管理器 APK：以 APK 实测签名为准，并核对版本号
if [ -n "${MANAGER_APK:-}" ]; then
    [ -f "$MANAGER_APK" ] || die "找不到 MANAGER_APK：$MANAGER_APK"
    need python3
    CFG_VERSION="$FOLKSU_MANAGER_VERSION"
    log "从管理器 APK 反推签名：$MANAGER_APK"
    eval "$(python3 "$SCRIPT_DIR/apk_cert.py" "$MANAGER_APK" --shell)"
    info "cert=$FOLKSU_CERT_SIZE  sha256=${FOLKSU_CERT_HASH:0:16}…"
    if [ "${FOLKSU_MANAGER_VERSION:-}" != "$CFG_VERSION" ]; then
        die "APK 版本(${FOLKSU_MANAGER_VERSION:-未识别}) 与配置的 $CFG_VERSION 不一致：请同时改 FOLKSU_TAG / FOLKSU_MANAGER_VERSION"
    fi
fi

log "构建配置"
cat <<EOF
    内核版本   : $KERNEL_VERSION
    内核后缀   : -$CUSTOM_SUFFIX
    管理器     : FolkSU/FinaSU $FOLKSU_TAG (versionCode $FOLKSU_MANAGER_VERSION, UAPI $FOLKSU_UAPI)
    管理器签名 : $FOLKSU_CERT_SIZE / ${FOLKSU_CERT_HASH:0:16}…
    可选特性   : KPM=$USE_KPM LZ4=$APPLY_LZ4 LZ4KD=$APPLY_LZ4KD BBR=$APPLY_BBR ADIOS=$APPLY_ADIOS ReKernel=$APPLY_REKERNEL BBG=$APPLY_BBG DSS=$APPLY_DROIDSPACES NET=$APPLY_BETTERNET
    SUSFS      : 关闭（FolkSU 内核树已重构，susfs4ksu 的 KSU 侧联动补丁不兼容）
    并行度     : $JOBS    工作目录: $WORKDIR
EOF

# shellcheck disable=SC1091
. "$SCRIPT_DIR/folksu.sh"

if [ "$CHECK_ONLY" = "1" ]; then
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp" || true' EXIT
    mkdir -p "$tmp/ksu/drivers"
    : > "$tmp/ksu/drivers/Makefile"
    : > "$tmp/ksu/drivers/Kconfig"
    folksu_integrate "$tmp" "$tmp/ksu"
    log "预检通过（未编译）"
    exit 0
fi

# ------------------------------------------------------------------ 依赖
need unzip
need zip
if [ "$INSTALL_DEPS" = "1" ]; then
    log "安装编译依赖"
    SUDO=""
    if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; fi
    $SUDO apt-get update -qq
    $SUDO apt-get install -y --no-install-recommends \
        bc bison flex libssl-dev libelf-dev cpio zip unzip rsync \
        gcc binutils make python3 perl xz-utils tar ca-certificates
fi
for c in make gcc bc flex bison patch; do need "$c"; done

# ------------------------------------------------------------------ 拉取
mkdir -p "$WORKDIR" "$DIST_DIR"
cd "$WORKDIR"

fetch() { # fetch <url> <outfile>
    if [ -s "$2" ]; then info "已存在 $(basename "$2")，跳过下载"; return 0; fi
    info "下载 $(basename "$2")"
    curl -fL --retry 3 --retry-delay 2 -o "$2.part" "$1"
    mv "$2.part" "$2"
}

if [ ! -d "$WORKDIR/common" ]; then
    log "拉取内核源码 + 工具链（并行）"
    (
        fetch "$KERNEL_ZIP" "$WORKDIR/common.zip"
        rm -rf "$WORKDIR/.kx" "$WORKDIR/common"
        mkdir -p "$WORKDIR/.kx"
        unzip -q "$WORKDIR/common.zip" -d "$WORKDIR/.kx"
        # GitHub 归档都是「单层目录 + 源码」，直接把它改名成 common；
        # 千万不要先建 common 再 mv 进去 —— 那样会套成 common/<顶层目录>/arch
        top="$(ls -A "$WORKDIR/.kx" | head -1)"
        if [ -d "$WORKDIR/.kx/$top" ] && [ "$(ls -A "$WORKDIR/.kx" | wc -l)" -eq 1 ]; then
            mv "$WORKDIR/.kx/$top" "$WORKDIR/common"
        else
            mkdir -p "$WORKDIR/common"
            mv "$WORKDIR/.kx"/* "$WORKDIR/common"/
        fi
        rm -rf "$WORKDIR/.kx" "$WORKDIR/common.zip"
    ) & P1=$!
    (
        fetch "https://github.com/cctv18/oneplus_sm8650_toolchain/releases/download/LLVM-Clang19-r536225/clang-r536225.zip" "$WORKDIR/clang.zip"
        rm -rf "$WORKDIR/clang19"; mkdir -p "$WORKDIR/clang19"
        unzip -q "$WORKDIR/clang.zip" -d "$WORKDIR/clang19"
        rm -f "$WORKDIR/clang.zip"
    ) & P2=$!
    (
        fetch "https://github.com/cctv18/oneplus_sm8650_toolchain/releases/download/LLVM-Clang19-r536225/rust.zip" "$WORKDIR/rust.zip"
        rm -rf "$WORKDIR/rust"; mkdir -p "$WORKDIR/rust"
        unzip -q "$WORKDIR/rust.zip" -d "$WORKDIR/rust"
        rm -f "$WORKDIR/rust.zip"
    ) & P3=$!
    (
        fetch "https://github.com/cctv18/oneplus_sm8650_toolchain/releases/download/LLVM-Clang19-r536225/build-tools.zip" "$WORKDIR/bt.zip"
        unzip -oq "$WORKDIR/bt.zip" -d "$WORKDIR"
        rm -f "$WORKDIR/bt.zip"
    ) & P4=$!
    wait $P1 $P2 $P3 $P4
else
    info "复用已存在的内核源码：$WORKDIR/common"
fi
[ -d "$WORKDIR/common" ] || die "内核源码解压失败"
[ -f "$WORKDIR/common/arch/arm64/configs/gki_defconfig" ] || {
    echo "common/ 顶层内容："
    ls -A "$WORKDIR/common" | head -20
    die "内核源码结构异常：找不到 arch/arm64/configs/gki_defconfig"
}
[ -x "$WORKDIR/clang19/bin/clang" ] || die "clang 工具链不完整：$WORKDIR/clang19/bin/clang"

# ------------------------------------------------------------------ 版本后缀
log "锁定内核版本后缀：-$CUSTOM_SUFFIX"
SLV="$WORKDIR/common/scripts/setlocalversion"
if [ -f "$SLV" ]; then
    sed -i 's/ -dirty//g' "$SLV"
    sed -i '$i res=$(echo "$res" | sed '\''s/-dirty//g'\'')' "$SLV"
    sed -i "\$s|echo \"\\\$res\"|echo \"-${CUSTOM_SUFFIX}\"|" "$SLV"
    sed -i 's/${scm_version}//' "$SLV"
fi
DEFCONFIG="$WORKDIR/common/arch/arm64/configs/gki_defconfig"
[ -f "$DEFCONFIG" ] || die "找不到 $DEFCONFIG"
sed -i "s/^CONFIG_LOCALVERSION=.*/CONFIG_LOCALVERSION=\"-${CUSTOM_SUFFIX}\"/" "$DEFCONFIG"
echo "CONFIG_LOCALVERSION_AUTO=n" >> "$DEFCONFIG"

# ------------------------------------------------------------------ FolkSU
log "集成 FolkSU(FinaSU) 内核侧代码"
folksu_integrate "$WORKDIR" "$WORKDIR/common"

# ------------------------------------------------------------------ 补丁
apply_patch() { # apply_patch <patch> <strip> [soft]
    local p="$1" s="${2:-1}" soft="${3:-0}"
    if [ ! -s "$p" ]; then warn "补丁不存在，跳过：$p"; return 0; fi
    if patch -p"$s" -N -F 3 < "$p" >/dev/null 2>&1; then
        info "已应用 $(basename "$p")"
    elif [ "$soft" = "1" ]; then
        warn "补丁有冲突（已尽量应用）：$(basename "$p")"
    else
        die "补丁应用失败：$(basename "$p")（内核源码或上游补丁已变动）"
    fi
}
grab() { curl -fsSL -o "$2" "$1" || die "下载失败：$1"; }

log "应用补丁集合"
cd "$WORKDIR/common"
grab "$RAW/other_patch/cve-2026-43499-rtmutex-6.12.patch" cve.patch
apply_patch cve.patch 1 0

if [ "$APPLY_LZ4" = "1" ]; then
    grab "$RAW/zram_patch/001-lz4.patch" 001-lz4.patch
    grab "$RAW/zram_patch/002-zstd.patch" 002-zstd.patch
    apply_patch 001-lz4.patch 1 1
    apply_patch 002-zstd.patch 1 1
fi
if [ "$APPLY_LZ4KD" = "1" ]; then
    grab "$RAW/other_patch/lz4kd.patch" lz4kd.patch
    apply_patch lz4kd.patch 1 1
fi
if [ "$APPLY_BETTERNET" = "1" ]; then
    grab "$RAW/other_patch/config.patch" config.patch
    apply_patch config.patch 1 1
fi
if [ "$APPLY_DROIDSPACES" != "0" ]; then
    grab "$RAW/droidspaces_patch/fix_sysvipc_kabi_a16-6.12.patch" sysvipc.patch
    grab "$RAW/droidspaces_patch/fix_oplus_bsp_midas.patch" midas.patch
    grab "$RAW/droidspaces_patch/ntsync_compat_android16-6.12.patch" ntsync.patch
    apply_patch sysvipc.patch 1 1
    apply_patch midas.patch 1 1
    apply_patch ntsync.patch 1 1
fi

if [ "$APPLY_BBG" = "1" ]; then
    log "集成 Baseband-guard（防格机 LSM）"
    curl -fsSL https://github.com/vc-teahouse/Baseband-guard/raw/master/setup.sh | bash \
        || die "Baseband-guard setup.sh 执行失败"
    sed -i '/^config LSM$/,/^help$/{ /^[[:space:]]*default/ { /baseband_guard/! s/selinux/selinux,baseband_guard/ } }' security/Kconfig
fi

# ------------------------------------------------------------------ defconfig
log "写入 defconfig"
cat >> "$DEFCONFIG" <<'EOF'
CONFIG_KSU=y
CONFIG_KSU_DYNAMIC_MANAGER=y
CONFIG_KSU_SUSFS=n
CONFIG_TMPFS_XATTR=y
CONFIG_TMPFS_POSIX_ACL=y
CONFIG_CC_OPTIMIZE_FOR_PERFORMANCE=y
CONFIG_HEADERS_INSTALL=n
CONFIG_RUST=y
CONFIG_ANDROID_BINDER_IPC_RUST=m
EOF

if [ "$APPLY_LZ4KD" = "1" ]; then
    cat >> "$DEFCONFIG" <<'EOF'
CONFIG_ZSMALLOC=y
CONFIG_CRYPTO_LZ4HC=y
CONFIG_CRYPTO_LZ4K=y
CONFIG_CRYPTO_LZ4KD=y
CONFIG_CRYPTO_842=y
CONFIG_ZRAM_BACKEND_LZ4HC=y
CONFIG_ZRAM_BACKEND_LZ4K=y
CONFIG_ZRAM_BACKEND_LZ4KD=y
CONFIG_ZRAM_BACKEND_842=y
EOF
fi

if [ "$APPLY_BETTERNET" = "1" ]; then
    cat >> "$DEFCONFIG" <<'EOF'
CONFIG_NETFILTER_XT_MATCH_ADDRTYPE=y
CONFIG_NETFILTER_XT_SET=y
CONFIG_IP_SET=y
CONFIG_IP_SET_MAX=65534
CONFIG_IP_SET_BITMAP_IP=y
CONFIG_IP_SET_BITMAP_IPMAC=y
CONFIG_IP_SET_BITMAP_PORT=y
CONFIG_IP_SET_HASH_IP=y
CONFIG_IP_SET_HASH_IPMARK=y
CONFIG_IP_SET_HASH_IPPORT=y
CONFIG_IP_SET_HASH_IPPORTIP=y
CONFIG_IP_SET_HASH_IPPORTNET=y
CONFIG_IP_SET_HASH_IPMAC=y
CONFIG_IP_SET_HASH_MAC=y
CONFIG_IP_SET_HASH_NETPORTNET=y
CONFIG_IP_SET_HASH_NET=y
CONFIG_IP_SET_HASH_NETNET=y
CONFIG_IP_SET_HASH_NETPORT=y
CONFIG_IP_SET_HASH_NETIFACE=y
CONFIG_IP_SET_LIST_SET=y
CONFIG_IP6_NF_NAT=y
CONFIG_IP6_NF_TARGET_MASQUERADE=y
EOF
fi

if [ "$APPLY_BBR" != "0" ]; then
    cat >> "$DEFCONFIG" <<'EOF'
CONFIG_TCP_CONG_ADVANCED=y
CONFIG_TCP_CONG_BBR=y
CONFIG_TCP_CONG_CUBIC=y
CONFIG_TCP_CONG_VEGAS=y
CONFIG_TCP_CONG_NV=y
CONFIG_TCP_CONG_WESTWOOD=y
CONFIG_TCP_CONG_HTCP=y
CONFIG_TCP_CONG_BRUTAL=y
EOF
    if [ "$APPLY_BBR" = "default" ]; then
        echo "CONFIG_DEFAULT_TCP_CONG=bbr" >> "$DEFCONFIG"
    else
        echo "CONFIG_DEFAULT_TCP_CONG=cubic" >> "$DEFCONFIG"
    fi
fi

if [ "$APPLY_DROIDSPACES" != "0" ]; then
    cat >> "$DEFCONFIG" <<'EOF'
CONFIG_PID_NS=y
CONFIG_IPC_NS=y
CONFIG_USER_NS=y
CONFIG_SYSVIPC=y
CONFIG_DEVTMPFS=y
CONFIG_NAMESPACES=y
CONFIG_POSIX_MQUEUE=y
CONFIG_NETFILTER_XT_TARGET_LOG=y
CONFIG_NETFILTER_XT_MATCH_RECENT=y
CONFIG_NTSYNC=y
EOF
    if [ "$APPLY_DROIDSPACES" = "extend" ]; then
        printf 'CONFIG_BT_HCIVHCI=y\nCONFIG_STATIC_USERMODEHELPER=n\n' >> "$DEFCONFIG"
    fi
fi

if [ "$APPLY_ADIOS" = "1" ]; then
    printf 'CONFIG_MQ_IOSCHED_ADIOS=y\nCONFIG_MQ_IOSCHED_DEFAULT_ADIOS=y\n' >> "$DEFCONFIG"
fi
if [ "$APPLY_REKERNEL" = "1" ]; then
    printf 'CONFIG_REKERNEL=y\nCONFIG_REKERNEL_NETWORK=y\n' >> "$DEFCONFIG"
fi
if [ "$APPLY_BBG" = "1" ]; then
    echo "CONFIG_BBG=y" >> "$DEFCONFIG"
fi

sed -i 's/check_defconfig//' "$WORKDIR/common/build.config.gki"

# ------------------------------------------------------------------ 编译
log "开始编译（$JOBS 线程，预计 15~30 分钟）"
export PATH="$WORKDIR/clang19/bin:$WORKDIR/build-tools/bin:$WORKDIR/rust/bin:$PATH"
CLANG_DIR="$WORKDIR/clang19/bin"
export CC="$CLANG_DIR/clang" HOSTCC="$CLANG_DIR/clang"
export RUSTC=rustc BINDGEN=bindgen
export LIBCLANG_PATH="$WORKDIR/clang19/lib"
export LLVM=1 LLVM_IAS=1 ARCH=arm64 SUBARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu-
export LD=ld.lld HOSTLD=ld.lld AR=llvm-ar NM=llvm-nm AS=clang READELF=llvm-readelf
export OBJCOPY=llvm-objcopy OBJDUMP=llvm-objdump OBJSIZE=llvm-size STRIP=llvm-strip
export KSU_EXPECTED_SIZE="$FOLKSU_CERT_SIZE"
export KSU_EXPECTED_HASH="$FOLKSU_CERT_HASH"
export KBUILD_BUILD_USER="${KBUILD_BUILD_USER:-folksu}"
export KBUILD_BUILD_HOST="${KBUILD_BUILD_HOST:-builder}"

KCFLAGS=" -no-canonical-prefixes -O2 -pipe -Wno-error -fno-stack-protector -D__ANDROID_COMMON_KERNEL__"
KCFLAGS+=" -fdebug-prefix-map=$WORKDIR=. -fmacro-prefix-map=$WORKDIR=. -ffile-prefix-map=$WORKDIR=."
export KCFLAGS

info "clang   : $("$CLANG_DIR/clang" --version | head -1)"
info "rustc   : $(rustc -V 2>/dev/null | head -1)"
info "bindgen : $(bindgen --version 2>/dev/null | head -1)"

cd "$WORKDIR/common"
BUILD_LOG="$WORKDIR/build.log"
# shellcheck disable=SC2086
make -j"$JOBS" LLVM=1 ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
    CC="$CLANG_DIR/clang" HOSTCC="$CLANG_DIR/clang" LD=ld.lld HOSTLD=ld.lld \
    RUSTC=rustc OBJCOPY=llvm-objcopy O=out gki_defconfig Image 2>&1 | tee "$BUILD_LOG"

IMG="$WORKDIR/common/out/arch/arm64/boot/Image"
[ -f "$IMG" ] || die "编译结束但没有产出 Image"

# ------------------------------------------------------------------ 校验
log "编译产物校验"
if ! folksu_verify "$BUILD_LOG" "$IMG"; then
    die "校验未通过：这个包刷上去管理器也认不了，先别刷（详见上方提示）"
fi

# ------------------------------------------------------------------ KPM
if [ "$USE_KPM" = "1" ]; then
    log "打 KPatch-Next KPM 补丁"
    cd "$(dirname "$IMG")"
    curl -fsSLO https://github.com/KernelSU-Next/KPatch-Next/releases/latest/download/kptools-linux
    curl -fsSLO https://github.com/KernelSU-Next/KPatch-Next/releases/latest/download/kpimg-linux
    chmod +x ./kptools-linux
    ./kptools-linux -p -i ./Image -k ./kpimg-linux -o ./oImage
    mv ./oImage ./Image
fi

# ------------------------------------------------------------------ 打包
log "打包 AnyKernel3"
cd "$WORKDIR"
if [ ! -d AnyKernel3/.git ]; then git clone --depth=1 -q https://github.com/cctv18/AnyKernel3; fi
rm -rf AnyKernel3/.git
cp "$IMG" AnyKernel3/Image
cd AnyKernel3
if [ "$APPLY_LZ4KD" = "1" ]; then curl -fsSLO "$RAW/zram.zip"; fi
if [ "$USE_KPM" = "1" ]; then curl -fsSLO https://github.com/cctv18/KPatch-Next/releases/latest/download/kpn.zip; fi

ZIP_NAME="AnyKernel3_FolkSU_${KERNEL_VERSION}_${CUSTOM_SUFFIX}"
if [ "$APPLY_LZ4" = "1" ]; then ZIP_NAME="${ZIP_NAME}_lz4-zstd"; fi
if [ "$APPLY_LZ4KD" = "1" ]; then ZIP_NAME="${ZIP_NAME}_lz4kd"; fi
if [ "$APPLY_BBR" != "0" ]; then ZIP_NAME="${ZIP_NAME}_bbr"; fi
if [ "$APPLY_ADIOS" = "1" ]; then ZIP_NAME="${ZIP_NAME}_adios"; fi
if [ "$APPLY_REKERNEL" = "1" ]; then ZIP_NAME="${ZIP_NAME}_rek"; fi
if [ "$APPLY_BBG" = "1" ]; then ZIP_NAME="${ZIP_NAME}_bbg"; fi
if [ "$APPLY_DROIDSPACES" != "0" ]; then ZIP_NAME="${ZIP_NAME}_dss"; fi
if [ "$USE_KPM" = "1" ]; then ZIP_NAME="${ZIP_NAME}_kpm"; fi
ZIP_NAME="${ZIP_NAME}_$(date +%Y%m%d).zip"

{
    echo "Kernel      : $KERNEL_VERSION (-$CUSTOM_SUFFIX)"
    echo "Root        : FolkSU/FinaSU $FOLKSU_TAG"
    echo "KSU_VERSION : $FOLKSU_MANAGER_VERSION   (管理器版本号必须一致)"
    echo "KSU UAPI    : $FOLKSU_UAPI"
    echo "Manager cert: $FOLKSU_CERT_SIZE ${FOLKSU_CERT_HASH}"
    echo "Built at    : $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
} > ak3.txt
zip -qr "$DIST_DIR/$ZIP_NAME" ./*

cd "$DIST_DIR"
sha256sum "$ZIP_NAME" > "$ZIP_NAME.sha256"

log "完成"
info "刷机包 : $DIST_DIR/$ZIP_NAME"
info "签名值 : $(cat "$ZIP_NAME.sha256")"
info "管理器 : FinaSU 版本号必须是 $FOLKSU_MANAGER_VERSION（对应 $FOLKSU_TAG）"
