#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 Root 管理器 APK 里取出内核侧 apk_sign 需要的两个值：

    cert size  = v2/v3 签名块里证书 DER 的字节数（十六进制）
    cert hash  = 该 DER 的 SHA-256

内核 kernel/manager/apk_sign.c 就是读 APK 签名块的这一份证书做比对，
所以这里算出来的值可以直接喂给 kbuild：KSU_EXPECTED_SIZE / KSU_EXPECTED_HASH。

用法:
    python3 scripts/apk_cert.py FinaSU_v1.0.4_32960-release.apk
    python3 scripts/apk_cert.py app.apk --shell      # 输出可直接 eval 的 KEY=VALUE

只依赖标准库，Windows / Linux / macOS 都能跑。
"""

import argparse
import hashlib
import re
import struct
import sys

APK_SIG_BLOCK_MAGIC = b"APK Sig Block 42"
V2_BLOCK_ID = 0x7109871A
V3_BLOCK_ID = 0xF05368C0


def _find_eocd(data: bytes) -> int:
    for magic in (b"PK\x05\x06", b"PK\x06\x06"):
        idx = data.rfind(magic)
        if idx >= 0:
            return idx
    raise ValueError("不是有效的 ZIP/APK（找不到 EOCD）")


def _signing_block(data: bytes):
    eocd = _find_eocd(data)
    if data[eocd:eocd + 4] == b"PK\x06\x06":  # ZIP64
        cd_off = struct.unpack_from("<Q", data, eocd + 48)[0]
    else:
        cd_off = struct.unpack_from("<I", data, eocd + 16)[0]
    if data[cd_off - 16:cd_off] != APK_SIG_BLOCK_MAGIC:
        raise ValueError("APK 没有 v2/v3 签名块（可能是 v1 签名，内核无法用）")
    block_size = struct.unpack_from("<Q", data, cd_off - 24)[0]
    start = cd_off - (block_size + 8)
    pos, end, pairs = start + 8, cd_off - 24, {}
    while pos < end:
        length = struct.unpack_from("<Q", data, pos)[0]
        pair_id = struct.unpack_from("<I", data, pos + 8)[0]
        pairs[pair_id] = data[pos + 12:pos + 8 + length]
        pos += 8 + length
    return pairs


def _first_cert(block: bytes) -> bytes:
    """v2/v3 的 signer 结构相同：signers -> signer -> signed_data -> certificates"""
    p = 0
    _signers_len = struct.unpack_from("<I", block, p)[0]; p += 4
    _signer_len = struct.unpack_from("<I", block, p)[0]; p += 4
    _signed_data_len = struct.unpack_from("<I", block, p)[0]; p += 4
    _digests_len = struct.unpack_from("<I", block, p)[0]; p += 4
    _certs_len = struct.unpack_from("<I", block, p)[0]; p += 4
    cert_len = struct.unpack_from("<I", block, p)[0]; p += 4
    return block[p:p + cert_len]


def read_apk(apk_path: str):
    with open(apk_path, "rb") as fh:
        data = fh.read()
    pairs = _signing_block(data)
    for block_id in (V2_BLOCK_ID, V3_BLOCK_ID):
        if block_id in pairs:
            cert = _first_cert(pairs[block_id])
            return cert, ("v2" if block_id == V2_BLOCK_ID else "v3")
    raise ValueError("签名块里既没有 v2 也没有 v3 签名")


def guess_version(path: str):
    """FinaSU 的发布包命名是 FinaSU_v1.0.4_32960-release.apk，直接从文件名捞版本号。"""
    m = re.search(r"_(\d{4,6})[_-]", path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1])
    return m.group(1) if m else ""


def main() -> int:
    ap = argparse.ArgumentParser(description="提取 APK 签名证书大小 / SHA-256")
    ap.add_argument("apk")
    ap.add_argument("--shell", action="store_true", help="输出 KEY=VALUE，便于 eval")
    args = ap.parse_args()
    try:
        cert, scheme = read_apk(args.apk)
    except Exception as exc:  # noqa: BLE001
        print(f"[x] {exc}", file=sys.stderr)
        return 1
    size_hex = f"0x{len(cert):x}"
    digest = hashlib.sha256(cert).hexdigest()
    version = guess_version(args.apk)
    if args.shell:
        print(f'FOLKSU_CERT_SIZE="{size_hex}"')
        print(f'FOLKSU_CERT_HASH="{digest}"')
        if version:
            print(f'FOLKSU_MANAGER_VERSION="{version}"')
        print(f'CERT_SCHEME="{scheme}"')
    else:
        print(f"签名方案 : {scheme}")
        print(f"证书长度 : {size_hex} ({len(cert)} 字节)")
        print(f"证书哈希 : {digest}")
        print(f"推断版本 : {version or '（文件名里没有版本号，请用 FOLKSU_MANAGER_VERSION 指定）'}")
        print()
        print("对应的 kbuild 参数：")
        print(f"  KSU_EXPECTED_SIZE={size_hex}")
        print(f'  KSU_EXPECTED_HASH="{digest}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
