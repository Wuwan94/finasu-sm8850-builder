#!/usr/bin/env python3
"""Capture stock module ABI requirements and check a replacement ARM64 Image.

These checks detect build incompatibilities; they do not establish that a kernel
boots on a device. Vendor-to-vendor module dependencies remain explicitly listed.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import tarfile
import zlib


MAX_MODULE_SIZE = 512 * 1024 * 1024
MAX_CONFIG_SIZE = 16 * 1024 * 1024
ABI_CONFIG_KEYS = (
    "CONFIG_64BIT", "CONFIG_ARM64", "CONFIG_ARM64_4K_PAGES",
    "CONFIG_ARM64_16K_PAGES", "CONFIG_ARM64_64K_PAGES", "CONFIG_PGTABLE_LEVELS",
    "CONFIG_ARM64_VA_BITS", "CONFIG_ARM64_PA_BITS", "CONFIG_SMP",
    "CONFIG_MODULES", "CONFIG_MODVERSIONS", "CONFIG_GENDWARFKSYMS",
    "CONFIG_EXTENDED_MODVERSIONS", "CONFIG_MODULE_UNLOAD", "CONFIG_MODULE_SIG",
    "CONFIG_MODULE_SIG_FORCE", "CONFIG_RUST",
    "CONFIG_CFI", "CONFIG_CFI_CLANG", "CONFIG_CFI_ICALL_NORMALIZE_INTEGERS",
    "CONFIG_CFI_PERMISSIVE", "CONFIG_SHADOW_CALL_STACK", "CONFIG_SCS",
    "CONFIG_LTO", "CONFIG_LTO_CLANG", "CONFIG_LTO_CLANG_THIN",
    "CONFIG_LTO_CLANG_FULL", "CONFIG_PREEMPT", "CONFIG_PREEMPT_NONE",
    "CONFIG_PREEMPT_VOLUNTARY", "CONFIG_PREEMPT_DYNAMIC", "CONFIG_PREEMPT_RT",
    "CONFIG_RANDSTRUCT", "CONFIG_RANDSTRUCT_FULL", "CONFIG_RANDSTRUCT_PERFORMANCE",
    "CONFIG_KALLSYMS", "CONFIG_KALLSYMS_ALL",
    "CONFIG_ANDROID_VENDOR_HOOKS", "CONFIG_ANDROID_BINDER_IPC",
    "CONFIG_ANDROID_BINDER_IPC_RUST", "CONFIG_ARM64_BTI_KERNEL",
    "CONFIG_ARM64_PTR_AUTH_KERNEL", "CONFIG_ARM64_PTR_AUTH",
)


class CompatibilityError(ValueError):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def config_bytes(path):
    data = Path(path).read_bytes()
    if data.startswith(b"\x1f\x8b"):
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            decoded = decoder.decompress(data, MAX_CONFIG_SIZE + 1)
        except zlib.error as exc:
            raise CompatibilityError("Invalid gzip config: " + str(exc)) from exc
        if len(decoded) > MAX_CONFIG_SIZE or not decoder.eof:
            raise CompatibilityError("Truncated or oversized gzip config")
        if decoder.unused_data:
            raise CompatibilityError("Unexpected data after gzip config")
        data = decoded
    if len(data) > MAX_CONFIG_SIZE:
        raise CompatibilityError("Config is too large")
    return data


def parse_config(data):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CompatibilityError("Config is not UTF-8") from exc
    result = {}
    for line in text.splitlines():
        unset = re.fullmatch(r"# (CONFIG_[A-Za-z0-9_]+) is not set", line)
        setting = re.fullmatch(r"(CONFIG_[A-Za-z0-9_]+)=(.*)", line)
        if unset:
            key, value = unset.group(1), "n"
        elif setting:
            key, value = setting.groups()
        else:
            continue
        if key in result and result[key] != value:
            raise CompatibilityError("Conflicting config entries: " + key)
        result[key] = value
    if not result:
        raise CompatibilityError("No kernel config entries found")
    return result


def normalized_config_hash(config):
    return sha256("".join(f"{key}={config[key]}\n" for key in sorted(config)).encode())


def page_size_from_config(config):
    sizes = [size for size in (4, 16, 64)
             if config.get(f"CONFIG_ARM64_{size}K_PAGES") == "y"]
    if len(sizes) != 1:
        raise CompatibilityError("Config must select exactly one ARM64 page size")
    return sizes[0] * 1024


def _bounded(data, offset, size, description):
    if offset < 0 or size < 0 or offset > len(data) or size > len(data) - offset:
        raise CompatibilityError("ELF range outside file: " + description)
    return data[offset:offset + size]


def _symbol(data):
    try:
        name = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise CompatibilityError("Non-ASCII module symbol") from exc
    if not re.fullmatch(r"[A-Za-z0-9_.$]+", name):
        raise CompatibilityError("Invalid or empty module symbol")
    return name


def _merge_crc(result, name, crc):
    if name in result and result[name] != crc:
        raise CompatibilityError("Conflicting CRCs for symbol: " + name)
    result[name] = crc


def module_imports(data):
    """Read modversion imports without executing or extracting the module."""
    if len(data) < 64 or data[:7] != b"\x7fELF\x02\x01\x01":
        raise CompatibilityError("Module must be ELF64 little-endian")
    header = struct.unpack_from("<16sHHIQQQIHHHHHH", data)
    if header[1] != 1 or header[2] != 183 or header[3] != 1 or header[8] != 64:
        raise CompatibilityError("Module must be an AArch64 ELF relocatable object")
    section_offset, entry_size, section_count, names_index = (
        header[6], header[11], header[12], header[13])
    if entry_size != 64 or section_offset < 64:
        raise CompatibilityError("Invalid ELF section table")
    first = struct.unpack("<IIQQQQIIQQ", _bounded(data, section_offset, 64, "section zero"))
    if section_count == 0:
        section_count = first[5]
    if names_index == 0xffff:
        names_index = first[6]
    if not 1 <= section_count <= 65536 or not 0 < names_index < section_count:
        raise CompatibilityError("Invalid ELF section count or string-table index")
    table = _bounded(data, section_offset, section_count * 64, "section table")
    sections = [struct.unpack_from("<IIQQQQIIQQ", table, i * 64)
                for i in range(section_count)]
    name_section = sections[names_index]
    if name_section[1] != 3:
        raise CompatibilityError("ELF section names are not a string table")
    names = _bounded(data, name_section[4], name_section[5], "section names")
    version_sections = {}
    for section in sections[1:]:
        name_offset = section[0]
        if name_offset >= len(names):
            raise CompatibilityError("ELF section name is outside string table")
        end = names.find(b"\0", name_offset)
        if end < 0:
            raise CompatibilityError("Unterminated ELF section name")
        try:
            name = names[name_offset:end].decode("ascii")
        except UnicodeDecodeError as exc:
            raise CompatibilityError("Non-ASCII ELF section name") from exc
        content = b"" if section[1] == 8 else _bounded(
            data, section[4], section[5], "section " + name)
        if name in ("__versions", "__version_ext_names", "__version_ext_crcs"):
            if name in version_sections or section[1] != 1:
                raise CompatibilityError("Invalid or duplicate modversion section: " + name)
            version_sections[name] = content
    result = {}
    traditional = version_sections.get("__versions", b"")
    if len(traditional) % 64:
        raise CompatibilityError("Traditional modversion section has a partial entry")
    for offset in range(0, len(traditional), 64):
        crc = struct.unpack_from("<Q", traditional, offset)[0] & 0xffffffff
        field = traditional[offset + 8:offset + 64]
        end = field.find(b"\0")
        if end < 0:
            raise CompatibilityError("Unterminated traditional modversion symbol")
        _merge_crc(result, _symbol(field[:end]), crc)
    has_names = "__version_ext_names" in version_sections
    has_crcs = "__version_ext_crcs" in version_sections
    if has_names != has_crcs:
        raise CompatibilityError("Extended modversion section pair is incomplete")
    if has_names:
        raw_names = version_sections["__version_ext_names"]
        raw_crcs = version_sections["__version_ext_crcs"]
        if raw_names and not raw_names.endswith(b"\0"):
            raise CompatibilityError("Unterminated extended modversion symbol")
        # LLVM may retain zero padding after the final name in this section.
        unpadded_names = raw_names.rstrip(b"\0")
        symbols = unpadded_names.split(b"\0") if unpadded_names else []
        if len(raw_crcs) % 4 or len(raw_crcs) // 4 != len(symbols):
            raise CompatibilityError("Extended modversion name/CRC counts differ")
        for index, symbol in enumerate(symbols):
            crc = struct.unpack_from("<I", raw_crcs, index * 4)[0]
            _merge_crc(result, _symbol(symbol), crc)
    return result


def snapshot(args):
    raw_config = config_bytes(args.config)
    config = parse_config(raw_config)
    imports, modules = {}, []
    digest = hashlib.sha256()
    with tarfile.open(args.modules_tar, "r:*") as archive:
        members = sorted((m for m in archive.getmembers() if m.name.endswith(".ko")),
                         key=lambda member: member.name)
        seen = set()
        for member in members:
            if member.name in seen or not member.isfile() or not 0 < member.size <= MAX_MODULE_SIZE:
                raise CompatibilityError("Invalid or duplicate module member: " + member.name)
            seen.add(member.name)
            stream = archive.extractfile(member)
            if stream is None:
                raise CompatibilityError("Cannot read module: " + member.name)
            with stream:
                data = stream.read(MAX_MODULE_SIZE + 1)
            if len(data) != member.size:
                raise CompatibilityError("Truncated module: " + member.name)
            try:
                symbols = module_imports(data)
                for name, crc in symbols.items():
                    _merge_crc(imports, name, crc)
            except CompatibilityError as exc:
                raise CompatibilityError(member.name + ": " + str(exc)) from exc
            module_hash = sha256(data)
            digest.update(member.name.encode("utf-8") + b"\0" + bytes.fromhex(module_hash))
            modules.append({"path": member.name, "sha256": module_hash,
                            "importCount": len(symbols)})
    if not modules or not imports:
        raise CompatibilityError("Stock snapshot must include modules with versioned imports")
    return {
        "schemaVersion": 1, "kind": "stock-kernel-compatibility",
        "model": args.model, "device": args.device, "firmware": args.firmware,
        "release": args.release, "pageSize": page_size_from_config(config),
        "configSha256": sha256(raw_config),
        "normalizedConfigSha256": normalized_config_hash(config),
        "configValues": dict(sorted(config.items())),
        "abiConfig": {key: config.get(key, "n") for key in ABI_CONFIG_KEYS},
        "moduleCount": len(modules), "modulesSha256": digest.hexdigest(),
        "modules": modules, "importCount": len(imports),
        "imports": {name: f"0x{crc:08x}" for name, crc in sorted(imports.items())},
    }


def embedded_config(image):
    start_marker, end_marker = b"IKCFG_ST", b"IKCFG_ED"
    start = image.find(start_marker)
    if start < 0:
        raise CompatibilityError("Image has no embedded IKCONFIG")
    if image.find(start_marker, start + len(start_marker)) >= 0:
        raise CompatibilityError("Image contains multiple IKCONFIG start markers")
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        data = decoder.decompress(image[start + len(start_marker):], MAX_CONFIG_SIZE + 1)
    except zlib.error as exc:
        raise CompatibilityError("Invalid embedded IKCONFIG gzip") from exc
    if len(data) > MAX_CONFIG_SIZE or not decoder.eof:
        raise CompatibilityError("Truncated or oversized embedded IKCONFIG")
    if not decoder.unused_data.startswith(end_marker):
        raise CompatibilityError("Embedded IKCONFIG end marker is missing")
    return parse_config(data)


MIN_PACKABLE_IMAGE_BYTES = 1024 * 1024
MAX_PACKABLE_IMAGE_BYTES = 512 * 1024 * 1024


def arm64_image_info(image):
    """Check the fixed ARM64 Image header and return the fields safe to inspect.

    The header's image_size is an effective in-memory size, not necessarily the
    on-disk file length, so it must not be compared directly with len(image).
    """
    if len(image) < 64:
        raise CompatibilityError("Image is shorter than the 64-byte ARM64 header")
    if image[56:60] != b"ARMd":
        raise CompatibilityError("Image has no ARM64 Linux Image header")
    flags = struct.unpack_from("<Q", image, 24)[0]
    if flags & 1:
        raise CompatibilityError("Image is big-endian; Android targets require little-endian ARM64")
    page_code = (flags >> 1) & 3
    page_size = {1: 4096, 2: 16384, 3: 65536}.get(page_code)
    if page_size is None:
        raise CompatibilityError("Image header does not declare a supported ARM64 page size")
    effective_size = struct.unpack_from("<Q", image, 16)[0]
    if effective_size and effective_size < 64:
        raise CompatibilityError("Image header declares an invalid effective image size")
    releases = sorted(set(match.decode("ascii") for match in
                          re.findall(rb"Linux version ([^\s\x00]+)", image)))
    if len(releases) != 1:
        raise CompatibilityError("Image must contain exactly one unambiguous Linux release string")
    return {"fileBytes": len(image), "effectiveImageBytes": effective_size or None,
            "pageSize": page_size, "release": releases[0]}


def packable_image_info(image, kernel_version, suffix):
    if len(image) < MIN_PACKABLE_IMAGE_BYTES:
        raise CompatibilityError(
            f"Image is too short for a complete Android kernel ({len(image)} bytes; "
            f"minimum {MIN_PACKABLE_IMAGE_BYTES})")
    if len(image) > MAX_PACKABLE_IMAGE_BYTES:
        raise CompatibilityError("Image exceeds the 512 MiB packer safety limit")
    base_version = kernel_version.removesuffix("-gki")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", base_version):
        raise CompatibilityError("Kernel version must be a supported numeric version")
    if suffix and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}", suffix):
        raise CompatibilityError("Kernel suffix must be 1-64 safe filename/version characters")
    expected_release = base_version + ("-" + suffix if suffix else "")
    info = arm64_image_info(image)
    if info["release"] != expected_release:
        raise CompatibilityError(
            f"Image release {info['release']} does not match requested build {expected_release}")
    info["expectedRelease"] = expected_release
    return info


def read_symvers(path):
    result = {}
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) < 3 or not re.fullmatch(r"0x[0-9a-fA-F]{1,8}", fields[0]):
            raise CompatibilityError(f"Malformed symvers line {number}")
        _merge_crc(result, _symbol(fields[1].encode("ascii")), int(fields[0], 16))
    if not result:
        raise CompatibilityError("Symvers contains no exported symbols")
    return result


def verify(args):
    report = {
        "schemaVersion": 1, "kind": "kernel-compatibility-report",
        "passed": False, "stableRequested": bool(args.stable),
        "bootValidation": "not_performed", "errors": [], "checks": {},
    }
    errors, checks = report["errors"], report["checks"]
    if args.stable != 1:
        errors.append("GKI build contract requires KBUILD_GENDWARFKSYMS_STABLE=1 (--stable 1)")
    try:
        reference = json.loads(Path(args.reference).read_text(encoding="utf-8"))
        if (not isinstance(reference, dict) or reference.get("schemaVersion") != 1
                or reference.get("kind") != "stock-kernel-compatibility"):
            raise CompatibilityError("Unsupported stock reference format")
        if not isinstance(reference.get("release"), str) or not reference["release"]:
            raise CompatibilityError("Stock reference has no kernel release")
        stock_config, raw_imports = reference.get("abiConfig"), reference.get("imports")
        if (not isinstance(stock_config, dict) or set(stock_config) != set(ABI_CONFIG_KEYS)
                or not all(isinstance(value, str) for value in stock_config.values())):
            raise CompatibilityError("Stock reference ABI config is incomplete")
        if not isinstance(raw_imports, dict) or not raw_imports:
            raise CompatibilityError("Stock reference has no module import CRCs")
        imports = {}
        for name, crc in raw_imports.items():
            if not isinstance(crc, str) or not re.fullmatch(r"0x[0-9a-fA-F]{8}", crc):
                raise CompatibilityError("Invalid stock import CRC")
            imports[_symbol(name.encode("ascii"))] = int(crc, 16)
        if reference.get("importCount") != len(imports):
            raise CompatibilityError("Stock reference import count is inconsistent")
        required_symbols = reference.get("requiredKernelSymbols", [])
        if not isinstance(required_symbols, list) or not all(
                isinstance(name, str) for name in required_symbols):
            raise CompatibilityError("Stock reference requiredKernelSymbols must be a symbol-name list")
        required_exports = {"module_layout"}
        for name in required_symbols:
            required_exports.add(_symbol(name.encode("ascii")))
        config = parse_config(config_bytes(args.config))
        stock_values = reference.get("configValues", {})
        if not isinstance(stock_values, dict):
            raise CompatibilityError("Stock reference config values are invalid")
        checks["allConfigDifferences"] = {
            key: {"stock": stock_values.get(key, "n"), "built": config.get(key, "n")}
            for key in sorted(set(stock_values) | set(config))
            if stock_values.get(key, "n") != config.get(key, "n")
        } if stock_values else {}
        image = Path(args.image).read_bytes()
        report["imageSha256"] = sha256(image)
        report["reference"] = {key: reference.get(key) for key in (
            "model", "device", "firmware", "release", "configSha256", "modulesSha256")}
        if len(image) < 64 or image[56:60] != b"ARMd":
            raise CompatibilityError("Image has no ARM64 Linux Image header")
        flags = struct.unpack_from("<Q", image, 24)[0]
        page_code = (flags >> 1) & 3
        image_page = {1: 4096, 2: 16384, 3: 65536}.get(page_code)
        checks["imagePageSize"] = image_page
        config_page = page_size_from_config(config)
        if flags & 1:
            errors.append("Image is big-endian; target requires little-endian ARM64")
        if image_page != 4096 or reference.get("pageSize") != 4096 or config_page != 4096:
            errors.append("Image, build config and stock reference must all select 4096-byte pages")
        releases = sorted(set(match.decode("ascii") for match in
                              re.findall(rb"Linux version ([^\s\x00]+)", image)))
        checks["imageReleases"] = releases
        if releases != [reference["release"]]:
            errors.append("Actual Image kernel release does not exactly match stock reference")
        extracted = embedded_config(image)
        differences = sorted(key for key in set(config) | set(extracted)
                             if config.get(key, "n") != extracted.get(key, "n"))
        checks["embeddedConfigDifferences"] = differences
        if differences:
            errors.append("Embedded IKCONFIG differs from supplied build .config")
        abi_differences = {key: {"stock": stock_config[key], "built": config.get(key, "n")}
                           for key in ABI_CONFIG_KEYS
                           if stock_config[key] != config.get(key, "n")}
        checks["abiConfigDifferences"] = abi_differences
        if abi_differences:
            errors.append("ABI-critical kernel config differs from stock")
        exports = read_symvers(args.symvers)
        missing_required = sorted(required_exports - set(exports))
        checks["requiredKernelExports"] = {
            "requiredCount": len(required_exports),
            "presentCount": len(required_exports) - len(missing_required),
            "missing": missing_required,
        }
        if missing_required:
            errors.append("missing_required_kernel_export: " + ", ".join(missing_required))
        compared = sorted(set(imports) & set(exports))
        unresolved = sorted(set(imports) - set(exports))
        mismatches = {name: {"stock": f"0x{imports[name]:08x}",
                             "built": f"0x{exports[name]:08x}"}
                      for name in compared if imports[name] != exports[name]}
        checks["moduleImports"] = {
            "referenceCount": len(imports), "comparedCount": len(compared),
            "matchedCount": len(compared) - len(mismatches),
            "coveragePercent": round(100 * len(compared) / len(imports), 2),
            "unresolvedCount": len(unresolved), "unresolved": unresolved,
            "crcMismatches": mismatches,
            "scope": "vmlinux exports only; imports supplied by other stock modules are unresolved",
            "verification": "partial" if unresolved else "all_reference_imports_compared",
        }
        if not compared:
            errors.append("No stock module imports overlap vmlinux.symvers")
        if mismatches:
            errors.append("Stock module import CRCs do not match rebuilt vmlinux exports")
    except (CompatibilityError, OSError, ValueError, TypeError, UnicodeError) as exc:
        errors.append(str(exc))
    report["passed"] = not errors
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("snapshot", help="Read stock config and module ABI requirements")
    for flag in ("config", "modules-tar", "output", "model", "device", "firmware", "release"):
        capture.add_argument("--" + flag, required=True)
    check = commands.add_parser("verify", help="Validate a built Image against stock evidence")
    for flag in ("image", "config", "symvers", "reference", "output"):
        check.add_argument("--" + flag, required=True)
    check.add_argument("--stable", type=int, choices=(0, 1), default=0)
    pack_check = commands.add_parser("check-image", help="Reject incomplete or mismatched Android Image files")
    pack_check.add_argument("--image", required=True)
    pack_check.add_argument("--kernel-version", required=True)
    pack_check.add_argument("--suffix", default="")
    pack_check.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.command == "snapshot":
        try:
            report = snapshot(args)
            status = 0
        except (CompatibilityError, OSError, ValueError, tarfile.TarError) as exc:
            report = {"schemaVersion": 1, "kind": "stock-kernel-compatibility-error",
                      "passed": False, "errors": [str(exc)]}
            status = 1
    elif args.command == "verify":
        report = verify(args)
        status = 0 if report["passed"] else 1
    else:
        try:
            info = packable_image_info(Path(args.image).read_bytes(),
                                       args.kernel_version, args.suffix)
            report = {"schemaVersion": 1, "kind": "arm64-image-package-check",
                      "passed": True, "checks": info, "errors": []}
            status = 0
        except (CompatibilityError, OSError, ValueError, UnicodeError) as exc:
            report = {"schemaVersion": 1, "kind": "arm64-image-package-check",
                      "passed": False, "checks": {}, "errors": [str(exc)]}
            status = 1
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    if status:
        for error in report["errors"]:
            print("ERROR: " + error, file=sys.stderr)
    elif args.command == "snapshot":
        print(f"Captured {report['moduleCount']} modules and {report['importCount']} import CRCs")
    elif args.command == "check-image":
        checks = report["checks"]
        print(f"Packable ARM64 Image: {checks['fileBytes']} bytes; release {checks['release']}; "
              f"page size {checks['pageSize']} bytes")
    else:
        coverage = report["checks"]["moduleImports"]
        print(f"Static checks passed: {coverage['comparedCount']}/{coverage['referenceCount']} imports compared; "
              f"{coverage['unresolvedCount']} unresolved; device boot not tested")
    return status


if __name__ == "__main__":
    sys.exit(main())
