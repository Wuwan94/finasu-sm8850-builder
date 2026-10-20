#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/apk_cert.py 的单元测试：构造带 v2 签名块的假 APK，验证解析结果。

不依赖真实 APK，离线可跑：
    python3 -m unittest discover -s tests -v
"""

import hashlib
import io
import os
import struct
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import apk_cert  # noqa: E402


def _u32(v: int) -> bytes:
    return struct.pack("<I", v)


def make_apk_with_v2(cert: bytes) -> bytes:
    """造一个带 v2 签名块的 APK（结构与真机一致：签名块插在中央目录之前）"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("AndroidManifest.xml", b"not-a-real-manifest")
    data = bytearray(buf.getvalue())

    eocd = data.rfind(b"PK\x05\x06")
    cd_size, cd_off = struct.unpack_from("<II", data, eocd + 12)

    # ---- 组装 v2 签名块内容（字段顺序与内核 apk_sign 解析顺序一致）
    certs_seq = _u32(len(cert)) + cert
    signed_data = _u32(0) + _u32(len(certs_seq)) + certs_seq      # digests(空) + certificates
    signer = _u32(len(signed_data)) + signed_data                 # signed data（signatures/public key 省略）
    value = _u32(1) + _u32(len(signer)) + signer                  # signers 序列

    pair = struct.pack("<Q", 4 + len(value)) + _u32(0x7109871A) + value
    # APK Signing Block 布局：[u64 size][pairs][u64 size][magic 16B]
    # size = len(pairs) + 8(尾部 size) + 16(magic)
    size_of_block = len(pair) + 24
    region = (
        struct.pack("<Q", size_of_block)
        + pair
        + struct.pack("<Q", size_of_block)
        + b"APK Sig Block 42"
    )

    # 签名块要插在「中央目录之前」
    new_cd_off = cd_off + len(region)
    data[cd_off:cd_off] = region
    # EOCD 整体后移了 len(region) 字节，它里面的 cd_offset 也要改成新值
    struct.pack_into("<I", data, eocd + len(region) + 16, new_cd_off)
    return bytes(data)


class ApkCertTest(unittest.TestCase):
    def test_extracts_certificate(self):
        cert = bytes(range(200, 256))  # 56 字节假证书
        apk = make_apk_with_v2(cert)
        with open("test.apk", "wb") as fh:
            fh.write(apk)
        try:
            got, scheme = apk_cert.read_apk("test.apk")
            self.assertEqual(got, cert)
            self.assertEqual(scheme, "v2")
        finally:
            os.remove("test.apk")

    def test_hash_matches_sha256_of_cert(self):
        cert = b"a-fake-certificate-body-for-testing-0123456789"
        apk = make_apk_with_v2(cert)
        with open("test.apk", "wb") as fh:
            fh.write(apk)
        try:
            got, _ = apk_cert.read_apk("test.apk")
            self.assertEqual(hashlib.sha256(got).hexdigest(),
                             hashlib.sha256(cert).hexdigest())
        finally:
            os.remove("test.apk")

    def test_v1_only_zip_is_rejected_with_hint(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("AndroidManifest.xml", b"x")
        with open("test-v1.apk", "wb") as fh:
            fh.write(buf.getvalue())
        try:
            with self.assertRaises(ValueError) as ctx:
                apk_cert.read_apk("test-v1.apk")
            self.assertIn("v1", str(ctx.exception))
        finally:
            os.remove("test-v1.apk")

    def test_guess_version_from_filename(self):
        self.assertEqual(
            apk_cert.guess_version("AnyKernel3_FinaSU_v1.0.4_32960-release.apk"),
            "32960",
        )
        self.assertEqual(apk_cert.guess_version("no-version-here.apk"), "")


if __name__ == "__main__":
    unittest.main()
