"""Regression tests for stock-module CRC and ARM64 Image compatibility gates."""

import argparse
import contextlib
import gzip
import io
import json
from pathlib import Path
import struct
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import kernel_compat as compat


RELEASE = "6.12.23-android16-5-gb2a876903b49-ab14541642-4k"
CONFIG = b"""CONFIG_64BIT=y
CONFIG_ARM64=y
CONFIG_ARM64_4K_PAGES=y
# CONFIG_ARM64_16K_PAGES is not set
# CONFIG_ARM64_64K_PAGES is not set
CONFIG_MODULES=y
CONFIG_MODVERSIONS=y
CONFIG_GENDWARFKSYMS=y
CONFIG_EXTENDED_MODVERSIONS=y
CONFIG_RUST=y
CONFIG_CFI_CLANG=y
CONFIG_IKCONFIG=y
CONFIG_IKCONFIG_PROC=y
"""


def elf_module(sections):
    names = b"\0.shstrtab\0"
    offsets = {}
    for name, _ in sections:
        offsets[name] = len(names)
        names += name.encode("ascii") + b"\0"
    payload = bytearray(b"\0" * 64)
    headers = [(0,) * 10]
    for name, content, section_type in [
            (".shstrtab", names, 3)] + [(name, data, 1) for name, data in sections]:
        offset = len(payload)
        payload.extend(content)
        headers.append((1 if name == ".shstrtab" else offsets[name], section_type,
                        0, 0, offset, len(content), 0, 0, 1, 0))
    section_offset = len(payload)
    for header in headers:
        payload.extend(struct.pack("<IIQQQQIIQQ", *header))
    payload[:64] = struct.pack("<16sHHIQQQIHHHHHH", b"\x7fELF\x02\x01\x01" + b"\0" * 9,
                               1, 183, 1, 0, 0, section_offset, 0, 64, 0, 0,
                               64, len(headers), 1)
    return bytes(payload)


def traditional(symbols):
    return elf_module([("__versions", b"".join(
        struct.pack("<Q", crc) + name.encode().ljust(56, b"\0")
        for name, crc in symbols.items()))])


def extended(symbols):
    return elf_module([
        ("__version_ext_names", b"".join(name.encode() + b"\0" for name in symbols)),
        ("__version_ext_crcs", b"".join(struct.pack("<I", crc) for crc in symbols.values())),
    ])


def image(config=CONFIG, release=RELEASE, page_size=4096):
    payload = bytearray(64)
    struct.pack_into("<Q", payload, 24, {4096: 2, 16384: 4, 65536: 6}[page_size])
    payload[56:60] = b"ARMd"
    return bytes(payload) + b"Linux version " + release.encode() + b" (builder)\0" + (
        b"IKCFG_ST" + gzip.compress(config, mtime=0) + b"IKCFG_ED")


class PackableImageTests(unittest.TestCase):
    def make_image(self, release=RELEASE, size=compat.MIN_PACKABLE_IMAGE_BYTES):
        payload = bytearray(size)
        struct.pack_into("<Q", payload, 24, 2)  # little-endian, 4 KiB pages
        payload[56:60] = b"ARMd"
        banner = b"Linux version " + release.encode("ascii") + b" (builder)\0"
        payload[64:64 + len(banner)] = banner
        return bytes(payload)

    def test_accepts_matching_android_image_and_normalizes_gki_label(self):
        info = compat.packable_image_info(
            self.make_image(), "6.12.23-gki", "android16-5-gb2a876903b49-ab14541642-4k")
        self.assertEqual(info["release"], RELEASE)
        self.assertEqual(info["expectedRelease"], RELEASE)
        self.assertEqual(info["fileBytes"], compat.MIN_PACKABLE_IMAGE_BYTES)

    def test_rejects_text_wrong_release_short_image_and_unsafe_suffix(self):
        cases = (
            (b"not a kernel" * compat.MIN_PACKABLE_IMAGE_BYTES, "6.12.23", "safe"),
            (self.make_image("6.12.23-wrong"), "6.12.23", "safe"),
            (self.make_image()[:4096], "6.12.23", "safe"),
            (self.make_image(), "6.12.23", "$(touch-pwned)"),
        )
        for data, version, suffix in cases:
            with self.subTest(version=version, suffix=suffix):
                with self.assertRaises(compat.CompatibilityError):
                    compat.packable_image_info(data, version, suffix)

    def test_rejects_bad_magic_page_size_and_truncated_header(self):
        valid = self.make_image()
        bad_magic = bytearray(valid)
        bad_magic[56:60] = b"nope"
        bad_page = bytearray(valid)
        struct.pack_into("<Q", bad_page, 24, 0)
        for data in (bytes(bad_magic), bytes(bad_page), valid[:63]):
            with self.subTest(length=len(data)):
                with self.assertRaises(compat.CompatibilityError):
                    compat.packable_image_info(data, "6.12.23", "safe")


class ModuleImportTests(unittest.TestCase):
    def test_traditional_and_extended_imports(self):
        expected = {"module_layout": 0x1234abcd, "_RNvCs_long_rust_symbol": 0xffffffff}
        self.assertEqual(compat.module_imports(traditional(expected)), expected)
        self.assertEqual(compat.module_imports(extended(expected)), expected)

    def test_rejects_non_elf_wrong_architecture_and_truncation(self):
        for data in (b"not ELF", traditional({"a": 1})[:100]):
            with self.assertRaises(compat.CompatibilityError):
                compat.module_imports(data)
        data = bytearray(traditional({"a": 1}))
        struct.pack_into("<H", data, 18, 62)
        with self.assertRaisesRegex(compat.CompatibilityError, "AArch64"):
            compat.module_imports(bytes(data))

    def test_bounds_check_section_payload(self):
        data = bytearray(traditional({"a": 1}))
        table = struct.unpack_from("<Q", data, 40)[0]
        struct.pack_into("<Q", data, table + 2 * 64 + 24, len(data) + 1)
        with self.assertRaisesRegex(compat.CompatibilityError, "outside file"):
            compat.module_imports(bytes(data))

    def test_rejects_partial_entries_and_unterminated_names(self):
        cases = [elf_module([("__versions", b"\0" * 63)]),
                 elf_module([("__versions", struct.pack("<Q", 1) + b"a" * 56)]),
                 elf_module([("__version_ext_names", b"foo"),
                             ("__version_ext_crcs", struct.pack("<I", 1))])]
        for data in cases:
            with self.subTest(data=data[-80:]):
                with self.assertRaises(compat.CompatibilityError):
                    compat.module_imports(data)

    def test_rejects_unpaired_extended_sections_and_count_mismatch(self):
        for sections in ([('__version_ext_names', b"foo\0")],
                         [('__version_ext_names', b"foo\0bar\0"),
                          ('__version_ext_crcs', struct.pack('<I', 1))]):
            with self.assertRaises(compat.CompatibilityError):
                compat.module_imports(elf_module(sections))

    def test_extended_names_allow_llvm_trailing_padding_only(self):
        data = elf_module([("__version_ext_names", b"foo\0bar\0\0\0"),
                           ("__version_ext_crcs", struct.pack("<II", 1, 2))])
        self.assertEqual(compat.module_imports(data), {"foo": 1, "bar": 2})
        malformed = elf_module([("__version_ext_names", b"foo\0\0bar\0"),
                                ("__version_ext_crcs", struct.pack("<III", 1, 2, 3))])
        with self.assertRaises(compat.CompatibilityError):
            compat.module_imports(malformed)

    def test_conflicting_duplicate_symbols_are_not_overwritten(self):
        data = elf_module([("__versions", struct.pack("<Q", 1) + b"foo".ljust(56, b"\0")),
                           ("__version_ext_names", b"foo\0"),
                           ("__version_ext_crcs", struct.pack("<I", 2))])
        with self.assertRaisesRegex(compat.CompatibilityError, "Conflicting CRCs"):
            compat.module_imports(data)


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "stock.config.gz"
        self.config_path.write_bytes(gzip.compress(CONFIG, mtime=0))
        self.tar_path = self.root / "modules.tar"
        self.write_modules({"vendor_dlkm/lib/modules/driver.ko": extended(
            {"module_layout": 0x1234abcd, "vendor_dependency": 0x99887766})})
        self.snapshot_args = argparse.Namespace(config=self.config_path,
            modules_tar=self.tar_path, model="RMX5200", device="RE6030L1",
            firmware="RMX5200_16.0.9.401(CN01)", release=RELEASE)
        self.reference = compat.snapshot(self.snapshot_args)
        self.reference_path = self.root / "reference.json"
        self.save_reference()
        self.build_config = self.root / ".config"
        self.build_config.write_bytes(CONFIG)
        self.image_path = self.root / "Image"
        self.image_path.write_bytes(image())
        self.symvers = self.root / "vmlinux.symvers"
        self.symvers.write_text("0x1234abcd\tmodule_layout\tvmlinux\tEXPORT_SYMBOL\n")
        self.args = argparse.Namespace(image=self.image_path, config=self.build_config,
            symvers=self.symvers, reference=self.reference_path, stable=1)

    def write_modules(self, modules):
        with tarfile.open(self.tar_path, "w") as archive:
            for name, data in modules.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))

    def save_reference(self):
        self.reference_path.write_text(json.dumps(self.reference), encoding="utf-8")

    def assert_failure(self, fragment):
        report = compat.verify(self.args)
        self.assertFalse(report["passed"], report)
        self.assertIn(fragment, " ".join(report["errors"]))
        return report

    def test_snapshot_captures_hashes_and_module_imports(self):
        self.assertEqual(self.reference["moduleCount"], 1)
        self.assertEqual(self.reference["importCount"], 2)
        self.assertEqual(self.reference["imports"]["module_layout"], "0x1234abcd")
        self.assertEqual(self.reference["pageSize"], 4096)
        self.assertEqual(len(self.reference["configSha256"]), 64)
        self.assertEqual(self.reference, compat.snapshot(self.snapshot_args))

    def test_snapshot_rejects_conflicts_between_modules(self):
        self.write_modules({"a.ko": traditional({"foo": 1}),
                            "b.ko": extended({"foo": 2})})
        with self.assertRaisesRegex(compat.CompatibilityError, "Conflicting CRCs"):
            compat.snapshot(self.snapshot_args)

    def test_valid_partial_coverage_does_not_claim_boot_validation(self):
        report = compat.verify(self.args)
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["bootValidation"], "not_performed")
        imports = report["checks"]["moduleImports"]
        self.assertEqual(imports["coveragePercent"], 50)
        self.assertEqual(imports["unresolved"], ["vendor_dependency"])
        self.assertEqual(imports["verification"], "partial")

    def test_changed_export_crc_and_zero_overlap_fail(self):
        self.symvers.write_text("0x1234abce module_layout vmlinux EXPORT_SYMBOL\n")
        self.assert_failure("CRCs do not match")
        self.symvers.write_text("0x1234abcd unrelated vmlinux EXPORT_SYMBOL\n")
        self.assert_failure("No stock module imports overlap")

    def test_old_reference_still_requires_module_layout_with_nonzero_overlap(self):
        self.symvers.write_text("0x99887766 vendor_dependency vmlinux EXPORT_SYMBOL\n")
        report = self.assert_failure("missing_required_kernel_export: module_layout")
        self.assertEqual(report["checks"]["moduleImports"]["comparedCount"], 1)
        self.assertEqual(report["checks"]["requiredKernelExports"]["missing"], ["module_layout"])

    def test_known_kernel_exports_cannot_be_reclassified_as_unresolved(self):
        self.reference["requiredKernelSymbols"] = ["module_layout", "vendor_dependency"]
        self.save_reference()
        report = self.assert_failure("missing_required_kernel_export: vendor_dependency")
        self.assertEqual(report["checks"]["requiredKernelExports"]["requiredCount"], 2)
        self.assertEqual(report["checks"]["moduleImports"]["unresolved"], ["vendor_dependency"])
        with self.symvers.open("a") as output:
            output.write("0x99887766 vendor_dependency vmlinux EXPORT_SYMBOL\n")
        self.assertTrue(compat.verify(self.args)["passed"])

    def test_required_exports_must_be_valid_symbol_names(self):
        for required in ("module_layout", [123], ["bad symbol"]):
            with self.subTest(required=required):
                self.reference["requiredKernelSymbols"] = required
                self.save_reference()
                report = compat.verify(self.args)
                self.assertFalse(report["passed"])

    def test_image_must_be_arm64_and_4k_with_exact_stock_banner(self):
        self.image_path.write_bytes(b"not a kernel")
        self.assert_failure("ARM64 Linux Image header")
        self.image_path.write_bytes(image(page_size=16384))
        self.assert_failure("4096-byte pages")
        self.image_path.write_bytes(image(release=RELEASE + "-wrong"))
        self.assert_failure("kernel release")

    def test_embedded_config_must_match_actual_build_config(self):
        self.image_path.write_bytes(image(config=CONFIG + b"CONFIG_SOME_DRIVER=y\n"))
        self.assert_failure("Embedded IKCONFIG differs")
        self.image_path.write_bytes(image().replace(b"IKCFG_ED", b"BAD_MARK"))
        self.assert_failure("end marker is missing")

    def test_abi_critical_changes_fail_even_when_image_and_config_agree(self):
        changed = CONFIG.replace(b"CONFIG_GENDWARFKSYMS=y", b"CONFIG_GENDWARFKSYMS=n")
        self.build_config.write_bytes(changed)
        self.image_path.write_bytes(image(config=changed))
        report = self.assert_failure("ABI-critical kernel config")
        self.assertIn("CONFIG_GENDWARFKSYMS", report["checks"]["abiConfigDifferences"])

    def test_stable_mode_must_be_explicit(self):
        self.args.stable = 0
        self.assert_failure("KBUILD_GENDWARFKSYMS_STABLE=1")

    def test_invalid_reference_is_reported(self):
        self.reference_path.write_text("[]")
        self.assert_failure("Unsupported stock reference")

    def test_cli_writes_report_on_failed_verification(self):
        report_path = self.root / "reports" / "failed.json"
        with contextlib.redirect_stderr(io.StringIO()):
            status = compat.main(["verify", "--image", str(self.image_path),
                "--config", str(self.build_config), "--symvers", str(self.symvers),
                "--reference", str(self.reference_path), "--output", str(report_path)])
        self.assertEqual(status, 1)
        self.assertFalse(json.loads(report_path.read_text())["passed"])


if __name__ == "__main__":
    unittest.main()
