#!/usr/bin/env bash
# ==============================================================================
#  Folksu 集成库：把 FinaSU(FinaSU) 的 kernel/ 挂进内核树，并做版本 / UAPI / 签名校验
#  由 scripts/build.sh source，不单独执行（也可 `bash scripts/finasu.sh --check` 自检）
# ==============================================================================

finasu_die() { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }
finasu_log() { printf '\033[1;36m  ·\033[0m %s\n' "$*"; }
finasu_warn() { printf '\033[1;33m  !\033[0m %s\n' "$*" >&2; }

# 把 FinaSU 内核侧代码放进 "$1"（工作目录），并挂到内核树上
# 用法：finasu_integrate <workdir> <kernel_src_dir_abs>
finasu_integrate() {
    local root="$1" ksrc="$2"
    [ -d "$ksrc/drivers" ] || finasu_die "内核源码目录异常：$ksrc/drivers 不存在"
    [ -f "$ksrc/drivers/Makefile" ] || finasu_die "内核源码不完整：$ksrc/drivers/Makefile 不存在"
    [ -f "$ksrc/drivers/Kconfig" ] || finasu_die "内核源码不完整：$ksrc/drivers/Kconfig 不存在"

    # --- 1. 取源码（必须保留完整提交历史：kernel/Kbuild 用 rev-list --count 算版本号）
    if [ -d "$root/KernelSU/.git" ]; then
        finasu_log "复用已存在的 $root/KernelSU"
    else
        finasu_log "克隆 $FINASU_REPO（blobless，只取提交历史足够算版本号）"
        git clone --filter=blob:none --quiet "$FINASU_REPO" "$root/KernelSU" \
            || finasu_die "克隆 FinaSU 失败（检查网络/代理）"
    fi
    local ksu="$root/KernelSU"

    # --- 2. 切到与管理器配套的 tag
    git -C "$ksu" fetch --tags --quiet || true
    if git -C "$ksu" rev-parse -q --verify "refs/tags/$FINASU_TAG" >/dev/null; then
        git -C "$ksu" checkout --quiet "$FINASU_TAG"
    elif [ "$FINASU_TAG" = "master" ] || [ "$FINASU_TAG" = "main" ]; then
        git -C "$ksu" checkout --quiet "$FINASU_TAG"
    else
        finasu_die "FinaSU 仓库里没有 tag：$FINASU_TAG"
    fi

    # --- 3. 版本 / UAPI 校验
    local ksu_version ksu_uapi
    ksu_version=$(( 30000 + $(git -C "$ksu" rev-list --count HEAD) ))
    ksu_uapi=$(sed -n 's/.*KERNEL_SU_UAPI_VERSION *= *\([0-9]\+\).*/\1/p' "$ksu/uapi/supercall.h" | head -1)
    finasu_log "内核侧 KSU_VERSION=$ksu_version（目标管理器 $FINASU_MANAGER_VERSION）"
    finasu_log "内核侧 KERNEL_SU_UAPI_VERSION=$ksu_uapi（管理器期望 $FINASU_UAPI）"
    [ "$ksu_uapi" = "$FINASU_UAPI" ] \
        || finasu_die "UAPI 不一致：内核 $ksu_uapi vs 管理器 $FINASU_UAPI。请改 FINASU_TAG 或 FINASU_UAPI"
    [ "$ksu_version" = "$FINASU_MANAGER_VERSION" ] \
        || finasu_die "版本不一致：内核 $ksu_version vs 管理器 $FINASU_MANAGER_VERSION。请改 FINASU_TAG / FINASU_MANAGER_VERSION"

    # --- 4. uapi 布局：kernel/include/uapi -> ../../uapi（上游已带软链，缺失时补）
    # uapi 头文件：直接把仓库顶层 uapi/ 物化进 kernel/include/uapi。
    # 不用软链 —— 上游那个软链在经过 symlink 挂载/权限/检出设置异常时会让
    # #include <uapi/app_profile.h> 找不到文件（CI 上真踩过一次）。
    rm -rf "$ksu/kernel/include/uapi"
    mkdir -p "$ksu/kernel/include/uapi"
    cp -a "$ksu/uapi/." "$ksu/kernel/include/uapi/"
    [ -f "$ksu/kernel/include/uapi/app_profile.h" ] \
        || finasu_die "uapi 头文件物化失败：$ksu/kernel/include/uapi 里没有 app_profile.h"
    finasu_log "uapi 头文件：$(ls "$ksu/kernel/include/uapi" | tr '\n' ' ')"

    # --- 5. 按 KernelSU 官方 setup.sh 的同款方式挂进内核树
    ln -sfn "$ksu/kernel" "$ksrc/drivers/kernelsu"
    finasu_log "kernelsu 挂载点：$(readlink -f "$ksrc/drivers/kernelsu")"
    grep -q 'kernelsu' "$ksrc/drivers/Makefile" \
        || printf '\nobj-$(CONFIG_KSU) += kernelsu/\n' >> "$ksrc/drivers/Makefile"
    grep -q 'drivers/kernelsu/Kconfig' "$ksrc/drivers/Kconfig" \
        || sed -i '/^endmenu/i source "drivers/kernelsu/Kconfig"' "$ksrc/drivers/Kconfig"

    # --- 6. 导出给 kbuild 的签名期望值（make 会读到同名环境变量）
    export KSU_EXPECTED_SIZE="$FINASU_CERT_SIZE"
    export KSU_EXPECTED_HASH="$FINASU_CERT_HASH"
    finasu_log "信任管理器签名：size=$FINASU_CERT_SIZE sha256=${FINASU_CERT_HASH:0:16}…"
}

# 编译完成后核对：版本号真的编进去了、签名期望值真的生效了
# 用法：finasu_verify <build.log> <Image>
finasu_verify() {
    local log="$1" img="$2" bad=0

    local v
    v=$(grep -a -o -- '-- KernelSU version: [0-9]*' "$log" | tail -1 | awk '{print $NF}')
    if [ -z "$v" ]; then
        finasu_warn "编译日志里没有 '-- KernelSU version:'，FinaSU 可能根本没参与编译"
        bad=1
    elif [ "$v" != "$FINASU_MANAGER_VERSION" ]; then
        finasu_warn "编进内核的 KSU_VERSION=$v，与管理器 $FINASU_MANAGER_VERSION 不一致"
        bad=1
    else
        finasu_log "校验通过：KSU_VERSION=$v"
    fi

    local s
    s=$(grep -a -o -- '-- KernelSU Manager signature size: 0x[0-9a-fA-F]*' "$log" | tail -1 | awk '{print $NF}')
    if [ -n "$s" ] && [ "$s" != "$FINASU_CERT_SIZE" ]; then
        finasu_warn "签名长度没生效：Kbuild 用的是 $s，期望 $FINASU_CERT_SIZE"
        bad=1
    fi

    if [ -f "$img" ]; then
        if grep -a -q "$FINASU_CERT_HASH" "$img"; then
            finasu_log "Image 里已包含信任的管理器证书哈希"
        else
            finasu_warn "Image 里没找到证书哈希字符串（可能被 LTO 优化，请以管理器实测为准）"
        fi
    fi
    return $bad
}

# ---------------------------------------------------------------- 自检模式
if [ "${BASH_SOURCE[0]}" = "$0" ] && [ "${1:-}" = "--check" ]; then
    conf="$(cd "$(dirname "$0")/.." && pwd)/config/finasu.conf"
    # shellcheck disable=SC1090
    . "$conf"
    tmp="$(mktemp -d)"; trap 'rm -rf "$tmp" || true' EXIT
    mkdir -p "$tmp/ksu/drivers"
    : > "$tmp/ksu/drivers/Makefile"
    : > "$tmp/ksu/drivers/Kconfig"
    # 只做克隆 + 版本/UAPI/签名比对，不编译
    finasu_integrate "$tmp" "$tmp/ksu"
    echo "自检通过：tag=$FINASU_TAG version=$FINASU_MANAGER_VERSION uapi=$FINASU_UAPI cert=$FINASU_CERT_SIZE"
fi
