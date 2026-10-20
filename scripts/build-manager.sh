#!/usr/bin/env bash
# ==============================================================================
#  从源码构建 FolkSU(FinaSU) 管理器 APK（可选功能）
#
#  说明：这层脚本只做「环境预检 + 调用上游官方构建流程」，真正的编译逻辑在
#        LyraVoid/FinaSU 仓库的 build.sh 里（apk / repack 两个目标）。
#
#  前置条件（缺一不可）：
#    1. JDK 21（Gradle 需要）
#    2. Android SDK：platforms;android-36 + build-tools;36.x
#    3. Android NDK 29.0.14206865（新版 NDK 静态链接 ksuinit 会失败）
#    4. Rust + aarch64-linux-android target + cargo-ndk
#    5. 自己的签名密钥：manager/keystore.properties（格式见上游 sign.example.properties）
#
#  用法:
#    bash scripts/build-manager.sh                 # 用默认 tag
#    FOLKSU_TAG=v1.0.4 bash scripts/build-manager.sh
#
#  产物：work/manager/FinaSU/dist/*.apk
#  构建完会打印该 APK 的证书 size/hash，把它填进 config/folksu.conf 即可让内核信任你自己的管理器。
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck disable=SC1091
. "$REPO_ROOT/config/folksu.conf"

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
info() { printf '\033[1;34m  ·\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

WORK="${WORKDIR:-$REPO_ROOT/work}/manager"
mkdir -p "$WORK"

log "环境预检"
need() { command -v "$1" >/dev/null 2>&1 || die "缺少命令：$1"; }
need git; need java; need python3

JAVA_MAJOR="$(java -version 2>&1 | sed -n 's/.*version "\([0-9]*\).*/\1/p' | head -1)"
[ "${JAVA_MAJOR:-0}" -ge 21 ] || die "需要 JDK 21+，当前 ${JAVA_MAJOR:-未知}"

[ -n "${ANDROID_HOME:-}" ] || die "未设置 ANDROID_HOME"
[ -d "$ANDROID_HOME/platforms" ] || die "ANDROID_HOME 下没有 platforms"
[ -d "$ANDROID_HOME/ndk/29.0.14206865" ] || die "缺少 NDK 29.0.14206865（用 sdkmanager 安装）"
command -v cargo >/dev/null 2>&1 || die "缺少 Rust / cargo"
rustup target list --installed 2>/dev/null | grep -q aarch64-linux-android \
    || die "缺少 Rust target：rustup target add aarch64-linux-android"

log "克隆 FinaSU 源码 ($FOLKSU_TAG)"
cd "$WORK"
if [ ! -d FinaSU/.git ]; then
    git clone --filter=blob:none "$FOLKSU_REPO" FinaSU
fi
git -C FinaSU fetch --tags --quiet || true
git -C FinaSU checkout --quiet "$FOLKSU_TAG"

cd FinaSU
PROPS="manager/keystore.properties"
if [ ! -f "$PROPS" ]; then
    info "未找到 $PROPS，已从样例生成模板，请填好密钥后重跑"
    cp manager/sign.example.properties "$PROPS"
    die "$PROPS 还是空的：填入 KEYSTORE_FILE / KEYSTORE_PASSWORD / KEY_ALIAS / KEY_PASSWORD"
fi

log "构建管理器 APK（这一步要 10~20 分钟）"
./build.sh apk
log "注入 ksud 并重新签名"
./build.sh repack

APK="$(find dist -maxdepth 1 -name '*.apk' -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)"
[ -n "$APK" ] || die "没找到产物 APK（看上面 build.sh 的输出）"

log "新 APK 的签名信息"
python3 "$SCRIPT_DIR/apk_cert.py" "$APK"
cat <<EOF

把上面的值填进 config/folksu.conf：
  FOLKSU_CERT_SIZE=...
  FOLKSU_CERT_HASH=...
然后重新构建内核，你的内核才会信任这个自己签名的管理器。
注意：KSU_VERSION 仍取源码提交数（$FOLKSU_TAG → $FOLKSU_MANAGER_VERSION），换 tag 时两者要一起改。
EOF
