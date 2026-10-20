# FinaSU 适配版 SM8850 内核构建器

给 OPPO / 一加 / 真我 SM8850（骁龙 8 Elite Gen5）机型造一个**能被 FinaSU 管理器直接接管**的 6.12 内核。

> **命名说明**：这个管理器原名 FolkSU，现已改名 **FinaSU**（作者 LyraVoid，仓库 `LyraVoid/FinaSU`，旧地址 `LyraVoid/FolkSU` 会自动 301 跳转）。本仓库统一用 FinaSU 这个叫法，发布包是 `FinaSU_*.apk`。

---

## 一、为什么原构建脚本刷完管理器"不认"

上游 [Wuwan94/oppo_oplus_realme_sm8850](https://github.com/Wuwan94/oppo_oplus_realme_sm8850) 只对接了 BakaSU / SukiSU / KernelSU Next / 原版 KernelSU。FinaSU 用的是自己的一套内核侧代码，卡点有三个，**缺一个管理器就显示未安装内核或必须手动授权**：

| 卡点 | 内核侧 | 管理器侧 | 不对齐的后果 |
|---|---|---|---|
| 协议版本 | `uapi/supercall.h` 的 `KERNEL_SU_UAPI_VERSION` | 管理器内置的 UAPI 期望值 | 直接判定"不兼容" |
| 构建号 | `KSU_VERSION = 30000 + git rev-list --count HEAD` | APK 的 `versionCode` | 版本不匹配，功能阉割 / 提示升级 |
| 管理器签名 | `KSU_EXPECTED_SIZE` + `KSU_EXPECTED_HASH`（证书 DER 长度与 SHA-256） | 管理器 APK 的 v2 签名证书 | 管理器拿不到 root 通道 |

本仓库验证过的组合（**实测数据，不是抄的**）：

```
管理器  FinaSU_v1.0.4_32960-release.apk
  versionCode  32960      = 30000 + 2960（v1.0.4 的提交数）
  UAPI         6
  cert size    0x103 (259 字节)
  cert sha256  f7cecef261a422eb70f7cb63272ec28c28c2b29856e51ba5ad229935ddc1d833
```

内核侧对应：tag `v1.0.4` → `KSU_VERSION=32960` → `UAPI=6` → 编译时把上面那对证书值写进 `apk_sign`。

---

## 二、仓库结构

```
finasu-sm8850-builder/
├── config/finasu.conf              ← 版本 / UAPI / 签名 的唯一真相源
├── scripts/
│   ├── build.sh                    ← 主构建脚本（拉源码、打补丁、编译、打包、校验）
│   ├── finasu.sh                   ← FinaSU 集成 + 版本/UAPI/签名校验（可单独 --check）
│   ├── apk_cert.py                 ← 从任意管理器 APK 反推 cert size / hash / 版本号
│   ├── pack-ak3.sh                 ← 已有 Image 直接打 AnyKernel3（不重新编译）
│   ├── apply_finasu.py             ← 给上游 Wuwan94 仓库注入 finasu 选项（输出与 .patch 一致）
│   ├── push-upstream.sh            ← 一键克隆上游 + 注入 + 建分支 + 推送/开 PR
│   ├── init-repo.sh                ← 一键把本目录变成 GitHub 仓库并推送
│   └── build-manager.sh            ← 可选：从源码构建你自己的管理器 APK
├── .github/workflows/build.yml     ← GitHub Actions 一键出包
└── docs/适配说明.md                ← 技术细节与证据链
```

---

## 三、两种用法

### 用法 A：直接给上游仓库打补丁（最小改动）

`patches/wuwan94-add-finasu.patch` 会给上游 **7 个 workflow + 7 个本地脚本**都加上 `FinaSU` 选项：

```bash
git clone https://github.com/Wuwan94/oppo_oplus_realme_sm8850.git
cd oppo_oplus_realme_sm8850
git apply /path/to/patches/wuwan94-add-finasu.patch
git add -A && git commit -m "feat: 适配 FinaSU(FinaSU)" && git push
```

之后在 Actions 里跑 `fastbuild_*`，`KSU分支` 下拉框会多一个 **`finasu`**：

- 自动克隆 `LyraVoid/FinaSU` 并切到 `v1.0.4`
- 校验 `KSU_VERSION=32960`、`UAPI=6`，不一致直接 fail（不会产出"看着能刷其实认不了"的包）
- 把 `KSU_EXPECTED_SIZE/HASH` 塞进构建环境
- 自动关掉 SUSFS（原因见第五节）
- 包名标记为 `FinaSU`

### 用法 B：用本仓库构建（干净、可复现）

```bash
# 只校验版本/协议/签名，不编译（30 秒）
bash scripts/build.sh --check

# 完整构建（Ubuntu 22.04+，首次约 20~30 分钟）
INSTALL_DEPS=1 bash scripts/build.sh

# 换机型基线
KERNEL_VERSION=6.12.58 bash scripts/build.sh

# 用自己的管理器 APK：签名和版本号直接从 APK 反推，不怕抄错
MANAGER_APK=./FinaSU_v1.0.4_32960-release.apk bash scripts/build.sh
```

产物：`dist/AnyKernel3_FinaSU_<内核版本>_<后缀>_<特性>_<日期>.zip`（+ `.sha256`），直接进 recovery / KernelFlasher 刷入。

GitHub Actions 用法：推到自己的仓库 → Actions → **Build SM8850 Kernel (FinaSU / FinaSU)** → 选内核基线、填 `finasu_tag` / `finasu_version` → Run。想省事就把管理器 APK 直链填进 `manager_apk_url`，签名自动反推。

---

## 四、换管理器版本怎么做

管理器每发一版，`versionCode` 就变了，内核侧必须跟着换。三步：

```bash
# 1. 下载新 APK，读出它的真实签名与版本号
python3 scripts/apk_cert.py FinaSU_v1.0.x_3xxxx-release.apk

# 2. 在 config/finasu.conf 里同步四项
FINASU_TAG=v1.0.x
FINASU_MANAGER_VERSION=3xxxx
FINASU_UAPI=<看新 tag 的 uapi/supercall.h>
FINASU_CERT_SIZE / FINASU_CERT_HASH=<第 1 步的输出>

# 3. 先 --check 再编译
bash scripts/build.sh --check
```

`--check` 会真去克隆对应 tag，算 `30000+提交数`、读 UAPI、比对签名，三样全过才继续。**这一步能挡住 90% 的"刷完不认识管理器"**。

---

## 五、已知限制（重要，别踩）

1. **SUSFS 默认关闭，且本脚本不做适配。**
   FinaSU 的内核树已重构成 `kernel/feature/*` 结构，`susfs4ksu` 的 `10_enable_susfs_for_ksu.patch`（给老版 KernelSU 写的）打不上；它的 Kconfig 里也没有 `CONFIG_KSU_SUSFS` 系列项。强行 `patch -F3 || true` 会得到"日志里打了、实际没编进去"的假象。隐藏需求请用 FinaSU 自带的 Folk-Mount / NoMount / 内核级隐藏能力。
2. **`CONFIG_HEADERS_INSTALL=n` 会让 KSU 的 preinit 走 `KSU_NO_PREINIT`。** 内置 KSU（不走 LKM）时无影响；要 LKM 模式请自行打开 header 安装。
3. **签名不匹配并不是绝路。** FinaSU 有 `CONFIG_KSU_DYNAMIC_MANAGER=y`：非预置签名的管理器会被识别为候选，管理器里手动授权即可。这正是"换了自己签名的管理器"时的退路。
4. **只保证 SM8850 系列。** MT6993 分支没在本仓库覆盖；同平台不同机型/固件的 `vermagic` 差异靠自己确认，刷前务必备份 boot。
5. **没有真机验证。** 版本号、UAPI、签名三项都是对着源码和 APK 实测出来的（见 `docs/适配说明.md`），编译流程沿用上游配方；但"能不能开机"取决于你选的基线是否与机型匹配。第一次建议只开 LZ4 + BBG，跑通再逐项加。

---

## 六、打包 AK3 与推送仓库

### 只想把已有 Image 打成 AK3

```bash
# 最短一条：其余全用默认值（FinaSU / 32960 / 6.12.23 / 默认后缀）
bash scripts/pack-ak3.sh work/common/out/arch/arm64/boot/Image

# 或者
make ak3 IMAGE=work/common/out/arch/arm64/boot/Image

# 想改包名/附带模块
KSU_TYPENAME=FinaSU KSU_VERSION=32960 KERNEL_VERSION=6.12.23 \
WITH_LZ4KD=1 WITH_KPM=0 bash scripts/pack-ak3.sh /path/to/Image
```

它会拉 `cctv18/AnyKernel3` 模板、塞 Image、写 `ak3.txt` 注释、出 `dist/*.zip` + `.sha256`；
系统里没有 `zip` 命令时自动用 python 兜底（Windows 也能跑）。

> `scripts/build.sh` 在编译完成后本来就会自带这一步，`pack-ak3.sh` 是给"Image 已经有了、不想再等 20 分钟"的场景。

### 推到自己仓库

```bash
bash scripts/init-repo.sh finasu-sm8850-builder     # 建 public 仓库并推送
PRIVATE=1 bash scripts/init-repo.sh                 # 建私有仓库
REMOTE_URL=git@github.com:me/xxx.git bash scripts/init-repo.sh   # 推到已有仓库
```

没有 `gh` 命令时它会打印手动两条命令，不会瞎折腾。装好仓库后，Actions 里的
**Build SM8850 Kernel (FinaSU / FinaSU)** 点一下就直接产出 AK3 包（Artifact 里下载）。

### 把适配推回上游 Wuwan94 仓库

```bash
git clone https://github.com/Wuwan94/oppo_oplus_realme_sm8850.git
cd oppo_oplus_realme_sm8850
git checkout -b feat/finasu-support
git apply /path/to/patches/wuwan94-add-finasu.patch
git commit -am "feat: 支持 FinaSU(FinaSU) 分支"
git push -u origin feat/finasu-support
```

## 七、刷完怎么确认成功

```bash
# 管理器里应显示：内核版本 ...-<你的后缀>，KSU 版本 32960，UAPI 6
# 命令行侧：
su -c 'cat /proc/version'
su -c 'ls /data/adb/ksu'
```

管理器首页若显示"未安装"，按顺序查：UAPI 是否 6 → 内核 `KSU_VERSION` 是否 32960 → 签名是否匹配（`dmesg | grep -i kernelsu` 能看到 manager 匹配日志）。

---

## 八、致谢

构建配方来自 [cctv18](https://github.com/cctv18) / [Wuwan94](https://github.com/Wuwan94) 的 SM8850 自动化脚本，Root 方案来自 [LyraVoid/FinaSU](https://github.com/LyraVoid/FinaSU)，SUSFS 生态来自 [ShirkNeko/susfs4ksu](https://github.com/ShirkNeko/susfs4ksu)。本仓库只做了 FinaSU 对接层与校验层，未改动内核与管理器本体。
