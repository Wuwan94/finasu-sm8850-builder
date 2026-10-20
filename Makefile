# ==============================================================================
#  FolkSU / FinaSU 适配版 SM8850 内核构建器
#    make check          只校验版本 / UAPI / 签名（不编译）
#    make build          完整编译并出 AK3 包
#    make ak3 IMAGE=...  用已有 Image 直接打包（不编译）
#    make push           提交并推送当前改动
#    make manager        从源码构建你自己的管理器 APK（需要 Android SDK/NDK）
# ==============================================================================
SHELL := /bin/bash
.DEFAULT_GOAL := help

IMAGE ?=
MSG   ?= chore: update

.PHONY: help check build ak3 push manager clean

help:
	@grep -E '^#( |$$)' $(MAKEFILE_LIST) | sed 's/^# \{0,1\}//;s/^=*$$//'

check:
	bash scripts/build.sh --check

build:
	INSTALL_DEPS=1 bash scripts/build.sh

ak3:
	@test -n "$(IMAGE)" || { echo "用法：make ak3 IMAGE=work/common/out/arch/arm64/boot/Image"; exit 1; }
	bash scripts/pack-ak3.sh "$(IMAGE)"

manager:
	bash scripts/build-manager.sh

push:
	git add -A
	git diff --cached --quiet || git commit -m "$(MSG)"
	git push

clean:
	rm -rf work dist .ak3work
