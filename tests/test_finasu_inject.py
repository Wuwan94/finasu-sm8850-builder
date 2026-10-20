#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/apply_finasu.py 的单元测试。

重点验证两件事：
  1. 注入确实发生了（选项、env、配置块都到位）
  2. **锚点对不上时会失败而不是静默改坏文件**（fail-fast 是这个脚本的核心设计）

不联网，用构造出来的最小 fixture：
    python3 -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import apply_finasu  # noqa: E402


WORKFLOW_FIXTURE = """name: test

env:
  TZ: Asia/Shanghai
  KERNEL_VERSION: '6.12'
  CCACHE_KEY: ccache-ecsv4-6.12.23

on:
  workflow_dispatch:
    inputs:
      ksu_type:
        description: 'KernelSU分支(BakaSU/SukiSU Ultra/KernelSU Next/KSU(原版)/无内置KSU,默认BakaSU)'
        required: true
        type: choice
        default: 'bakasu'
        options:
          - 'bakasu'
          - 'sukisu'
          - 'ksunext'
          - 'ksu'
          - 'none'

jobs:
  build:
    steps:
      - name: 添加KernelSU
        run: |
          if [[ ${{ github.event.inputs.ksu_type }} == "ksunext" ]]; then
            echo x
          elif [[ ${{ github.event.inputs.ksu_type }} == "ksu" ]]; then
            echo "正在配置原版 KernelSU (tiann/KernelSU)..."
          fi
      - name: 应用 KernelSU & SUSFS 补丁
        if: inputs.susfs_enable
        run: echo a
      - name: 添加SUSFS 配置项
        if: inputs.susfs_enable
        run: echo b
      - name: 添加 KSU & 其他配置项
        run: |
          if [[ "${{ github.event.inputs.susfs_enable }}" == "false" ]]; then
            echo "CONFIG_KSU_SUSFS=n"
          fi
      - name: 克隆 AnyKernel3 并打包
        run: |
          if [[ ${{ github.event.inputs.ksu_type }} == "sukisu" ]]; then
            KSU_TYPENAME="SukiSU"
          elif [[ ${{ github.event.inputs.ksu_type }} == "ksunext" ]]; then
            KSU_TYPENAME="KSUNext"
          elif [[ ${{ github.event.inputs.ksu_type }} == "ksu" ]]; then
            KSU_TYPENAME="KSU"
          fi
      - name: 汇总
        run: |
          if [[ ${{ github.event.inputs.ksu_type }} == "sukisu" ]]; then
            KSU_TYPENAME="SukiSU Ultra"
          elif [[ ${{ github.event.inputs.ksu_type }} == "ksunext" ]]; then
            KSU_TYPENAME="KernelSU Next"
          elif [[ ${{ github.event.inputs.ksu_type }} == "ksu" ]]; then
            KSU_TYPENAME="KSU"
          fi
"""

BUILDER_FIXTURE = """#!/bin/bash
set -e
MANIFEST=${MANIFEST:-oppo+oplus+realme}
read -p "KSU分支版本(r=BakaSU, y=SukiSU Ultra, n=KernelSU Next, k=KSU, l=lkm模式(无内置KSU), 默认：r): " KSU_BRANCH
if [[ "$KSU_BRANCH" == "r" || "$KSU_BRANCH" == "R" ]]; then
  KSU_TYPE="BakaSU"
elif [[ "$KSU_BRANCH" == "k" || "$KSU_BRANCH" == "K" ]]; then
  KSU_TYPE="KernelSU"
else
  KSU_TYPE="no KSU"
fi

echo
echo "===== 配置信息 ====="
echo "适用机型: $MANIFEST"

if [[ "$KSU_BRANCH" == "r" || "$KSU_BRANCH" == "R" ]]; then
  echo x
elif [[ "$KSU_BRANCH" == "k" || "$KSU_BRANCH" == "K" ]]; then
  echo y
else
  echo "已选择无内置KernelSU模式，跳过配置..."
fi

export OBJCOPY=llvm-objcopy OBJDUMP=llvm-objdump OBJSIZE=llvm-size STRIP=llvm-strip

ZIP_NAME="Anykernel3-${MANIFEST}"
"""


class Cfg:
    tag = "v1.0.4"
    version = "32960"
    uapi = "6"
    cert_size = "0x103"
    cert_hash = "f7cecef261a422eb70f7cb63272ec28c28c2b29856e51ba5ad229935ddc1d833"


class InjectTest(unittest.TestCase):
    def _write(self, tmp, name, content):
        path = os.path.join(tmp, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return path

    def test_workflow_injection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, ".github/workflows/fastbuild_x.yml", WORKFLOW_FIXTURE)
            self.assertTrue(apply_finasu.patch_workflow(path, Cfg()))
            out = open(path, encoding="utf-8").read()
            self.assertIn("- 'finasu'", out)
            self.assertIn("FOLKSU_TAG", out.replace("FINASU_TAG", "FOLKSU_TAG"))
            self.assertIn("FINASU_CERT_SIZE", out)
            self.assertIn("inputs.ksu_type != 'finasu'", out)

    def test_workflow_is_fail_fast_on_drift(self):
        """上游结构变了 -> 返回 False，而不是把文件改坏"""
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, ".github/workflows/fastbuild_x.yml", "name: totally different\n")
            self.assertFalse(apply_finasu.patch_workflow(path, Cfg()))
            # 文件内容不应被改动
            self.assertEqual(open(path, encoding="utf-8").read(), "name: totally different\n")

    def test_builder_injection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "local/builder_x.sh", BUILDER_FIXTURE)
            self.assertTrue(apply_finasu.patch_builder(path, Cfg()))
            out = open(path, encoding="utf-8").read()
            self.assertIn("FINASU_TAG", out)
            self.assertIn('KSU_TYPE="FinaSU"', out)
            self.assertIn("FOLKSU", out.replace("FINASU", "FOLKSU"))

    def test_builder_is_fail_fast_on_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "local/builder_x.sh", "#!/bin/bash\necho hi\n")
            self.assertFalse(apply_finasu.patch_builder(path, Cfg()))


if __name__ == "__main__":
    unittest.main()
