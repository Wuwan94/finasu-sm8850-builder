import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BASH = os.environ.get("TEST_BASH") or shutil.which("bash")
RELEASE = "6.12.23-android16-5-gb2a876903b49-ab14541642-4k"
FIRMWARE = "RMX5200_16.0.9.401(CN01)"


@unittest.skipUnless(BASH, "Bash is required to test the installer guard")
class DeviceGuardTests(unittest.TestCase):
    def run_guard(self, launcher=None, **changes):
        values = {
            "MODEL": "RMX5200", "DEVICE": "RE6030L1", "ANDROID": "16",
            "FIRMWARE": FIRMWARE, "RELEASE": RELEASE, "BOOTMODE": "true",
        }
        values.update(changes)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "device-compatibility.prop").write_text(
                "model=RMX5200\ndevice=RE6030L1\nandroid=16\n"
                f"firmware={FIRMWARE}\nrelease={RELEASE}\n", encoding="utf-8"
            )
            script = r'''
set -eu
if command -v cygpath >/dev/null; then
    AKHOME=$(cygpath -u "$1")
    GUARD=$(cygpath -u "$2")
else
    AKHOME=$1
    GUARD=$2
fi
abort() { echo "ABORT: $*" >&2; exit 73; }
ui_print() { echo "$*"; }
getprop() {
    case "$1" in
        ro.product.model) echo "$MODEL" ;;
        ro.product.device) echo "$DEVICE" ;;
        ro.build.version.release) echo "$ANDROID" ;;
        ro.build.display.id) echo "$FIRMWARE" ;;
    esac
}
uname() { echo "$RELEASE"; }
. "$GUARD"
check_device_compatibility
echo READY_FOR_BOOT_ACCESS
'''
            if launcher is not None:
                source = (ROOT / "ak3/META-INF/com/google/android/update-binary").read_text(
                    encoding="utf-8"
                )
                detection = source[source.index("BOOTMODE=false;"):source.index("DIR=/sdcard;")]
                script = r'''
unset BOOTMODE
ps() {
    if [ "${1-}" = "-A" ]; then
        printf '%s\n' "$MOCK_FALLBACK_PS"
    else
        printf '%s\n' "$MOCK_PRIMARY_PS"
    fi
}
''' + detection + r'''
"$BASH" --noprofile --norc -s -- "$@" <<'GUARD_CHILD'
printf 'CHILD_BOOTMODE=%s\n' "${BOOTMODE-unset}"
''' + script + "\nGUARD_CHILD\n"
                values.update(launcher)
            return subprocess.run(
                [BASH, "--noprofile", "--norc", "-s", "--", str(directory),
                 str(ROOT / "ak3/device-compatibility.sh")],
                input=script, text=True, encoding="utf-8", errors="replace",
                env={**os.environ, **values}, capture_output=True, timeout=20,
            )

    def test_reference_phone_is_allowed(self):
        result = self.run_guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("READY_FOR_BOOT_ACCESS", result.stdout)

    def test_wrong_device_or_os_is_blocked(self):
        for changes in ({"MODEL": "OTHER"}, {"DEVICE": "OTHER"}, {"ANDROID": "15"}):
            with self.subTest(changes=changes):
                result = self.run_guard(**changes)
                self.assertEqual(result.returncode, 73, result.stderr)
                self.assertNotIn("READY_FOR_BOOT_ACCESS", result.stdout)

    def test_mismatched_firmware_or_kernel_is_blocked_in_android(self):
        for changes in ({"FIRMWARE": "different-OTA"}, {"RELEASE": "6.12.38-other"}):
            with self.subTest(changes=changes):
                result = self.run_guard(**changes)
                self.assertEqual(result.returncode, 73, result.stderr)

    def test_recovery_does_not_treat_its_kernel_as_the_installed_kernel(self):
        result = self.run_guard(BOOTMODE="false", FIRMWARE="", RELEASE="recovery-kernel")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_guard(BOOTMODE="false", MODEL="OTHER")
        self.assertEqual(result.returncode, 73, result.stderr)

    def test_guard_runs_before_split_boot(self):
        source = (ROOT / "ak3/anykernel.sh").read_text(encoding="utf-8")
        self.assertLess(source.index("check_device_compatibility"), source.index("\nsplit_boot\n"))

    def test_launcher_exports_android_mode_to_child(self):
        for primary, fallback in (("123 zygote64", ""), ("", "123 zygote64")):
            with self.subTest(primary=primary, fallback=fallback):
                result = self.run_guard(launcher={
                    "MOCK_PRIMARY_PS": primary, "MOCK_FALLBACK_PS": fallback,
                })
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("CHILD_BOOTMODE=true", result.stdout)
                self.assertIn("READY_FOR_BOOT_ACCESS", result.stdout)

    def test_launcher_child_rejects_mismatched_firmware_and_kernel(self):
        for changes in ({"FIRMWARE": "different-OTA"}, {"RELEASE": "6.12.38-other"}):
            with self.subTest(changes=changes):
                result = self.run_guard(launcher={
                    "MOCK_PRIMARY_PS": "123 zygote64", "MOCK_FALLBACK_PS": "",
                }, **changes)
                self.assertEqual(result.returncode, 73, result.stderr)
                self.assertIn("CHILD_BOOTMODE=true", result.stdout)
                self.assertNotIn("READY_FOR_BOOT_ACCESS", result.stdout)

    def test_launcher_exports_recovery_mode_to_child(self):
        result = self.run_guard(launcher={"MOCK_PRIMARY_PS": "", "MOCK_FALLBACK_PS": ""},
                                FIRMWARE="", RELEASE="recovery-kernel")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CHILD_BOOTMODE=false", result.stdout)
        self.assertIn("READY_FOR_BOOT_ACCESS", result.stdout)
