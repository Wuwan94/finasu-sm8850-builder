# RMX5200 16.0.9.401 开机兼容性修复

## 问题与证据

用户报告以下产物刷入后无法开机，已恢复原厂内核：

- Release：`finasu-6.12.23-gki-20261020-1212`
- ZIP SHA-256：`e0fa9f6e3ea0593c0c3d1a3d02cf8b31fe32e52bf40ebcf135f3e15c7d0b26cb`
- Image SHA-256：`8ac79f797fffb5c7f05c5da76b0f445de50edf7ecdd0e88520cb302293b5e0b3`
- 对应构建：<https://github.com/Wuwan94/finasu-sm8850-builder/actions/runs/39417616700>

从 ZIP 内实际 Image 与该次构建日志确认：该包选择的是 `APPLY_KSU=0`，Image 未内置 FinaSU，但旧包名和 `ak3.txt` 仍标为 FinaSU。此次故障不能直接归因于 FinaSU 管理器签名、内置 KSU/LKM 冲突或 SUSFS。

2026-10-20 通过已授权的 ADB 只读采集恢复后的手机：

- 型号：`RMX5200`，设备代号：`RE6030L1`。
- 系统：`RMX5200_16.0.9.401(CN01)`，Android 16，页大小 4096。
- 原厂内核：`6.12.23-android16-5-gb2a876903b49-ab14541642-4k`。
- `/proc/config.gz` 与 436 个 `/vendor_dlkm/lib/modules/*.ko`。
- 得到 4,614 个唯一模块导入符号的 CRC。

仓库仅保存派生配置、CRC 与文件摘要：`config/devices/rmx5200-16.0.9.401.json`。未提交 boot 镜像、模块二进制、设备序列号或个人数据。

## 已确认的构建缺陷

旧脚本直接调用 make，但没有设置 `KBUILD_GENDWARFKSYMS_STABLE=1`。本基线启用了 `CONFIG_GENDWARFKSYMS=y`；源码 `scripts/Makefile.build` 只有在该变量存在时才传 `--stable`。Android KABI 的保留字段、类型替换等规则依赖此模式，漏设会使模块 CRC 与官方构建不同。

Android 官方构建环境也明确设置该变量：
<https://android.googlesource.com/kernel/build/+/857d2c13808a9522e9e385e57224fad7a9f42a44/_setup_env.sh>

版本后缀和源码提交匹配均不能单独证明 KMI 兼容。ABI 背景说明：
<https://source.android.com/docs/core/architecture/kernel/stable-kmi>

另修复默认变量初始化、并行下载错误遗漏、错误复用其它基线源码、并行执行配置与 Image 目标，以及覆盖 defconfig 栈保护的编译参数。

## 构建与打包门禁

`6.12.23-gki` 现在专用于上述 RMX5200 固件。打包前必须通过：

1. 实际 Image 为小端 ARM64，页大小 4096，真实内核版本与原厂参考一致。
2. Image 内的 IKCONFIG 与此次 `out/.config` 一致。
3. 页配置、CFI、Rust、modversions 等关键配置与原厂一致。
4. `vmlinux.symvers` 中出现的原厂模块依赖符号，其 CRC 全部一致；零交集也判失败。
5. 明确使用 Android KABI 稳定模式。

不匹配则只输出 `work/compatibility.json` 诊断，不生成刷机包。成功包内包含该报告、机型保护和来源信息。未内置 KSU 的包名改为 `NoKSU`。

安装器在读取或刷写 boot 前检查型号、代号和 Android 版本。在 Android 内刷入时还检查固件和正在运行的内核版本；Recovery 内核与系统内核可能不同，所以 Recovery 仅进行机型与系统版本检查。

`pack-ak3.sh` 的手动打包入口同样执行门禁，并使用仓库内固定模板。它需要明确提供 `KERNEL_CONFIG`、`KERNEL_SYMVERS` 与 `KBUILD_GENDWARFKSYMS_STABLE=1`。

## 首次验证构建

`.github/workflows/rmx5200-check.yml` 构建最小内置 FinaSU 版本，关闭 SUSFS、KPM、LZ4/ADIOS/BBG 等额外特性，并输出诊断工件。该流程不发布 Release。完整构建流程即使选择发布，也只创建带准确提交和独立运行 ID 的实验预览版。

## 验证边界

报告只比较新 vmlinux 导出与原厂模块导入的交集。其它厂商模块、GKI 模块提供的导入符号会列为 unresolved，并明确标为部分覆盖，不能当作完整 ABI 或整机启动认证。无法读取的 system_dlkm 和 pstore 未被纳入此次参考。

未用修复后的内核刷写真机。修复遗漏的稳定模式与新增兼容性门禁，不代表已证明此次不开机的唯一原因。必须先检查 Linux 构建及 CRC 结果；后续启动问题仍需同次失败启动的 pstore/串口日志定位。
