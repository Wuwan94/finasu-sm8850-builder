#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给 Wuwan94/oppo_oplus_realme_sm8850 的构建脚本注入 FolkSU(FinaSU) 支持。

为什么不用 .patch：上游 7 个 workflow / 7 个本地脚本是复制粘贴出来的，
补丁文件 1500 行里大半是重复 hunk；这里用"定点替换 + 断言数量"的方式，
上游小改也能跑，改不动就直接报错（不会留下半截修改）。

用法:
    python3 scripts/apply_folksu.py /path/to/oppo_oplus_realme_sm8850
    python3 scripts/apply_folksu.py . --tag v1.0.4 --version 32960 --uapi 6 \\
        --cert-size 0x103 --cert-hash f7ce…

只动这些文件：
    .github/workflows/fastbuild_*.yml   (7)
    local/builder_*.sh                  (7)
"""

import argparse
import glob
import pathlib
import re
import sys

WF_KSU_BLOCK = """          elif [[ ${{ github.event.inputs.ksu_type }} == "folksu" ]]; then
            echo "正在配置 FolkSU (FinaSU) 内核侧代码..."
            # FolkSU 已更名为 FinaSU（仓库沿用 LyraVoid/FinaSU），内核 UAPI 与管理器版本必须成对匹配：
            #   管理器 FinaSU_v1.0.4_32960  ->  内核 tag v1.0.4 / KSU_VERSION=32960 / UAPI=6
            # 注意：不能删 .git —— kernel/Kbuild 靠 git rev-list --count HEAD 算 KSU_VERSION(=30000+提交数)
            git clone --filter=blob:none https://github.com/LyraVoid/FinaSU.git KernelSU
            cd ./KernelSU
            git checkout "${{ env.FOLKSU_TAG }}"
            cd ..
            # 顶层 uapi/ 需要能被 kernel/ 引用（上游仓库已带软链，缺失时补上）
            [ -L ./KernelSU/kernel/include/uapi ] || ln -sfn ../../uapi ./KernelSU/kernel/include/uapi
            # 与 KernelSU 官方 setup.sh 完全一致的挂载方式
            ln -sfn ../KernelSU/kernel ./common/drivers/kernelsu
            grep -q 'kernelsu' ./common/drivers/Makefile || printf '\\nobj-$(CONFIG_KSU) += kernelsu/\\n' >> ./common/drivers/Makefile
            grep -q 'drivers/kernelsu/Kconfig' ./common/drivers/Kconfig || sed -i '/^endmenu/i source "drivers/kernelsu/Kconfig"' ./common/drivers/Kconfig
            KSU_VERSION=$(expr $(git -C ./KernelSU rev-list --count HEAD) + 30000)
            KSU_UAPI=$(sed -n 's/.*KERNEL_SU_UAPI_VERSION *= *\\([0-9]\\+\\).*/\\1/p' ./KernelSU/uapi/supercall.h | head -1)
            echo "KSUVER=$KSU_VERSION" >> $GITHUB_ENV
            echo "ksuver=$KSU_VERSION" >> $GITHUB_OUTPUT
            echo "KSU_UAPI=$KSU_UAPI" >> $GITHUB_ENV
            if [ "$KSU_UAPI" != "${{ env.FOLKSU_UAPI }}" ]; then
              echo "::error::FolkSU 内核侧 UAPI=$KSU_UAPI，与管理器期望的 ${{ env.FOLKSU_UAPI }} 不一致"
              exit 1
            fi
            if [ "$KSU_VERSION" != "${{ env.FOLKSU_MANAGER_VERSION }}" ]; then
              echo "::error::FolkSU 内核 KSU_VERSION=$KSU_VERSION，目标管理器版本=${{ env.FOLKSU_MANAGER_VERSION }}（改 FOLKSU_TAG 或 FOLKSU_MANAGER_VERSION）"
              exit 1
            fi
            # 让内核信任这个管理器签名（不动 make 命令行，env 变量对 kbuild 同样可见）
            echo "KSU_EXPECTED_SIZE=${{ env.FOLKSU_CERT_SIZE }}" >> $GITHUB_ENV
            echo "KSU_EXPECTED_HASH=${{ env.FOLKSU_CERT_HASH }}" >> $GITHUB_ENV
            echo "FolkSU 已集成：version=$KSU_VERSION uapi=$KSU_UAPI cert=${{ env.FOLKSU_CERT_SIZE }}"
"""

SH_KSU_BLOCK = """elif [[ "$KSU_BRANCH" == "f" || "$KSU_BRANCH" == "F" ]]; then
  echo ">>> 拉取 FolkSU (FinaSU) 内核侧代码 (tag $FOLKSU_TAG)..."
  # 不删 .git：kernel/Kbuild 用 git rev-list --count HEAD 计算 KSU_VERSION(=30000+提交数)
  git clone --filter=blob:none https://github.com/LyraVoid/FinaSU.git KernelSU
  cd ./KernelSU
  git checkout "$FOLKSU_TAG"
  cd ..
  # 顶层 uapi/ 需要能被 kernel/ 引用（上游仓库已带软链，缺失时补上）
  [ -L ./KernelSU/kernel/include/uapi ] || ln -sfn ../../uapi ./KernelSU/kernel/include/uapi
  # 与 KernelSU 官方 setup.sh 完全一致的挂载方式
  ln -sfn ../KernelSU/kernel ./common/drivers/kernelsu
  grep -q 'kernelsu' ./common/drivers/Makefile || printf '\\nobj-$(CONFIG_KSU) += kernelsu/\\n' >> ./common/drivers/Makefile
  grep -q 'drivers/kernelsu/Kconfig' ./common/drivers/Kconfig || sed -i '/^endmenu/i source "drivers/kernelsu/Kconfig"' ./common/drivers/Kconfig
  KSU_VERSION=$(expr $(git -C ./KernelSU rev-list --count HEAD) + 30000)
  KSU_UAPI=$(sed -n 's/.*KERNEL_SU_UAPI_VERSION *= *\\([0-9]\\+\\).*/\\1/p' ./KernelSU/uapi/supercall.h | head -1)
  echo "FolkSU 内核版本: $KSU_VERSION (管理器需为 $FOLKSU_MANAGER_VERSION), UAPI: $KSU_UAPI"
  if [ "$KSU_UAPI" != "$FOLKSU_UAPI" ]; then
    echo "!!! FolkSU 内核 UAPI=$KSU_UAPI 与管理器期望的 $FOLKSU_UAPI 不一致，构建中止"
    exit 1
  fi
  if [ "$KSU_VERSION" != "$FOLKSU_MANAGER_VERSION" ]; then
    echo "!!! FolkSU 内核 KSU_VERSION=$KSU_VERSION 与目标管理器版本 $FOLKSU_MANAGER_VERSION 不一致，构建中止"
    exit 1
  fi
"""


def edit(path, pairs):
    """按 (old, new, 期望出现次数) 逐条替换；数量不符视为上游已改动，直接失败。"""
    p = pathlib.Path(path)
    s = p.read_text(encoding="utf-8")
    orig = s
    for old, new, cnt in pairs:
        got = s.count(old)
        if got != cnt:
            print(f"[FAIL] {p.name}: 期望 {cnt} 处，实际 {got} 处 → {old.strip().splitlines()[0][:70]!r}")
            return False
        s = s.replace(old, new)
    if s != orig:
        p.write_text(s, encoding="utf-8")
    return True


def patch_workflow(f, cfg):
    env_block = (
        f"  FOLKSU_TAG: '{cfg.tag}'\n"
        f"  FOLKSU_MANAGER_VERSION: '{cfg.version}'\n"
        f"  FOLKSU_UAPI: '{cfg.uapi}'\n"
        f"  FOLKSU_CERT_SIZE: '{cfg.cert_size}'\n"
        f"  FOLKSU_CERT_HASH: '{cfg.cert_hash}'\n"
    )
    pairs = [
        ("KSU(原版)/无内置KSU,默认BakaSU)", "KSU(原版)/FolkSU/无内置KSU,默认BakaSU)", 1),
        ("          - 'ksu'\n          - 'none'", "          - 'ksu'\n          - 'folksu'\n          - 'none'", 1),
        ("        if: inputs.susfs_enable\n",
         "        if: inputs.susfs_enable && inputs.ksu_type != 'folksu'\n", 2),
        ('          if [[ "${{ github.event.inputs.susfs_enable }}" == "false" ]]; then\n',
         '          if [[ "${{ github.event.inputs.susfs_enable }}" == "false" || "${{ github.event.inputs.ksu_type }}" == "folksu" ]]; then\n', 1),
        ('          elif [[ ${{ github.event.inputs.ksu_type }} == "ksu" ]]; then\n'
         '            echo "正在配置原版 KernelSU (tiann/KernelSU)..."',
         WF_KSU_BLOCK + '          elif [[ ${{ github.event.inputs.ksu_type }} == "ksu" ]]; then\n'
         '            echo "正在配置原版 KernelSU (tiann/KernelSU)..."', 1),
        ('          elif [[ ${{ github.event.inputs.ksu_type }} == "ksunext" ]]; then\n            KSU_TYPENAME="KSUNext"\n',
         '          elif [[ ${{ github.event.inputs.ksu_type }} == "folksu" ]]; then\n            KSU_TYPENAME="FolkSU"\n'
         '          elif [[ ${{ github.event.inputs.ksu_type }} == "ksunext" ]]; then\n            KSU_TYPENAME="KSUNext"\n', 1),
        ('          elif [[ ${{ github.event.inputs.ksu_type }} == "ksunext" ]]; then\n            KSU_TYPENAME="KernelSU Next"\n',
         '          elif [[ ${{ github.event.inputs.ksu_type }} == "folksu" ]]; then\n            KSU_TYPENAME="FolkSU"\n'
         '          elif [[ ${{ github.event.inputs.ksu_type }} == "ksunext" ]]; then\n            KSU_TYPENAME="KernelSU Next"\n', 1),
    ]
    if not edit(f, pairs):
        return False
    # env 块：插在 CCACHE_KEY 之后
    p = pathlib.Path(f)
    s = p.read_text(encoding="utf-8")
    if "\n  FOLKSU_TAG: '" in s:
        return True
    m = re.search(r"^  CCACHE_KEY: .*\n", s, re.M)
    if not m:
        print(f"[FAIL] {p.name}: 找不到 CCACHE_KEY，无法插入 env")
        return False
    p.write_text(s[:m.end()] + env_block + s[m.end():], encoding="utf-8")
    return True


def patch_builder(f, cfg):
    pairs = [
        (", 默认：r): \" KSU_BRANCH", ", f=FolkSU, 默认：r): \" KSU_BRANCH", 1),
        ('elif [[ "$KSU_BRANCH" == "k" || "$KSU_BRANCH" == "K" ]]; then\n  KSU_TYPE="KernelSU"\nelse',
         'elif [[ "$KSU_BRANCH" == "k" || "$KSU_BRANCH" == "K" ]]; then\n  KSU_TYPE="KernelSU"\n'
         'elif [[ "$KSU_BRANCH" == "f" || "$KSU_BRANCH" == "F" ]]; then\n  KSU_TYPE="FolkSU"\nelse', 1),
        ('echo\necho "===== 配置信息 ====="',
         '# FolkSU/FinaSU 的内核树已重构，susfs4ksu 的 KSU 侧联动补丁与之不兼容，强制关闭 susfs\n'
         'if [[ "$KSU_BRANCH" == [fF] ]]; then\n  APPLY_SUSFS=n\nfi\n\n'
         'echo\necho "===== 配置信息 ====="', 1),
        ('else\n  echo "已选择无内置KernelSU模式，跳过配置..."',
         SH_KSU_BLOCK + 'else\n  echo "已选择无内置KernelSU模式，跳过配置..."', 1),
        ("export OBJCOPY=llvm-objcopy OBJDUMP=llvm-objdump OBJSIZE=llvm-size STRIP=llvm-strip\n",
         "export OBJCOPY=llvm-objcopy OBJDUMP=llvm-objdump OBJSIZE=llvm-size STRIP=llvm-strip\n"
         'if [[ "$KSU_BRANCH" == [fF] ]]; then\n'
         '  # 让内核信任 FolkSU/FinaSU 管理器签名（kbuild 直接读同名环境变量）\n'
         '  export KSU_EXPECTED_SIZE="$FOLKSU_CERT_SIZE"\n'
         '  export KSU_EXPECTED_HASH="$FOLKSU_CERT_HASH"\n'
         'fi\n', 1),
        ('ZIP_NAME="Anykernel3-${MANIFEST}"\n',
         'ZIP_NAME="Anykernel3-${MANIFEST}"\n'
         'if [[ "$KSU_BRANCH" == [fF] ]]; then\n  ZIP_NAME="${ZIP_NAME}-folksu"\nfi\n', 1),
    ]
    if not edit(f, pairs):
        return False
    # MANIFEST 行后插参数块（各版本 MANIFEST 值不同，用整行正则）
    p = pathlib.Path(f)
    s = p.read_text(encoding="utf-8")
    if "FOLKSU_TAG=" in s:
        return True
    block = (
        "# ===== FolkSU(FinaSU) 适配参数 =====\n"
        "# 内核侧 KSU_VERSION = 30000 + FinaSU 仓库提交数，必须与管理器 APK 的 versionCode 一致\n"
        f"FOLKSU_TAG=${{FOLKSU_TAG:-{cfg.tag}}}\n"
        f"FOLKSU_MANAGER_VERSION=${{FOLKSU_MANAGER_VERSION:-{cfg.version}}}\n"
        f"FOLKSU_UAPI=${{FOLKSU_UAPI:-{cfg.uapi}}}\n"
        "# 管理器签名证书的 (DER 长度, SHA-256)，取自 FinaSU_v1.0.4_32960-release.apk\n"
        f"FOLKSU_CERT_SIZE=${{FOLKSU_CERT_SIZE:-{cfg.cert_size}}}\n"
        f"FOLKSU_CERT_HASH=${{FOLKSU_CERT_HASH:-{cfg.cert_hash}}}\n"
    )
    s, n = re.subn(r"^(MANIFEST=\$\{MANIFEST:-[^\n]*\}\n)", lambda m: m.group(1) + block, s, count=1, flags=re.M)
    if n != 1:
        print(f"[FAIL] {p.name}: 找不到 MANIFEST 行")
        return False
    p.write_text(s, encoding="utf-8")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", help="上游仓库根目录")
    ap.add_argument("--tag", default="v1.0.4")
    ap.add_argument("--version", default="32960")
    ap.add_argument("--uapi", default="6")
    ap.add_argument("--cert-size", default="0x103")
    ap.add_argument("--cert-hash", default="f7cecef261a422eb70f7cb63272ec28c28c2b29856e51ba5ad229935ddc1d833")
    cfg = ap.parse_args()

    root = pathlib.Path(cfg.repo).resolve()
    if not (root / "local").is_dir() or not (root / ".github/workflows").is_dir():
        print(f"[x] {root} 看起来不是 oppo_oplus_realme_sm8850 仓库", file=sys.stderr)
        return 1

    ok = True
    for f in sorted(glob.glob(str(root / ".github/workflows/fastbuild_*.yml"))):
        ok &= patch_workflow(f, cfg)
    for f in sorted(glob.glob(str(root / "local/builder_*.sh"))):
        ok &= patch_builder(f, cfg)

    print("OK：14 个文件已注入 folksu 支持" if ok else "FAILED：有文件没改动，先看上面的 [FAIL]")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
