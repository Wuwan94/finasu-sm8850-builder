# FinaSU × SUSFS 兼容层

本目录是 **FinaSU 内核侧接 SUSFS 的源码级适配**，移植自
[Wuwan94/AnyKernel3](https://github.com/Wuwan94/AnyKernel3) 的 `scripts/finasu_compat/`
（原作者的 GKI 构建工程，Apache-2.0 / GPL-3.0，署名见上游仓库）。

## 为什么需要它

SUSFS 的官方 KSU 侧补丁 `10_enable_susfs_for_ksu.patch` 是按**老版 KernelSU 的目录结构**
写的，而 FinaSU 把内核侧代码重构成了 `kernel/core|feature|policy|supercall/...`，
直接 `patch` 会大面积失配。`apply.sh` 做两件事：

1. 用 `patch --forward` 应用上游补丁（允许部分 hunk 失败）
2. 按补丁意图，对 FinaSU 重构后的文件做内联修复：
   `core/init.c`（`susfs_init()`）、`feature/selinux_hide.c`（SUSFS 接管 selinuxfs 后跳过 KSU 侧 hook）、
   `include/ksu.h`（补 `ksu_late_loaded` / `ksu_bundled`）、`Kbuild`（放行 unused 告警）等

` susfs_drift_fixes.patch` 是配套的漂移修复补丁。

## 用法（build.sh 已自动调用）

```bash
KERNEL_ROOT=<工作目录> SUSFS4KSU=<susfs4ksu 目录> \
  bash scripts/finasu_compat/apply.sh KernelSU
```

其中 `SUSFS4KSU` 目录需要提供 `kernel_patches/KernelSU/10_enable_susfs_for_ksu.patch`
（我们用 `cctv18/susfs4oki` 的 `oki-android16-6.12` 分支）。

> 未启用 SUSFS 时（默认）本目录不会被执行，kernel 侧不带任何 SUSFS 代码。
