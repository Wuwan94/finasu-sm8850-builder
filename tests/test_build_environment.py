import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts" / "build.sh"
BASH = os.environ.get("TEST_BASH") or shutil.which("bash")


@unittest.skipUnless(BASH, "Bash is required for the build environment regression tests")
class BuildEnvironmentTests(unittest.TestCase):
    def run_shell(self, script, *args, extra_env=None):
        env = os.environ.copy()
        for name in ("APPLY_SUSFS", "APPLY_KSU", "BASH_ENV", "KERNEL_VERSION", "CUSTOM_SUFFIX"):
            env.pop(name, None)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [BASH, "--noprofile", "--norc", "-s", "--", *map(str, args)],
            input=script,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )

    def test_help_works_without_feature_environment(self):
        result = self.run_shell("bash scripts/build.sh --help\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--check", result.stdout)
        self.assertNotIn("unbound variable", result.stderr)

    def test_check_reaches_clone_with_defaults_and_without_network(self):
        result = self.run_shell(
            "git() { echo MOCK_CLONE_BLOCKED >&2; return 87; }\n"
            "curl() { return 88; }\n"
            "export -f git curl\n"
            "bash scripts/build.sh --check\n"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MOCK_CLONE_BLOCKED", result.stderr)
        self.assertNotIn("unbound variable", result.stderr)

    def test_susfs_requires_builtin_ksu_after_defaults(self):
        result = self.run_shell(
            "bash scripts/build.sh --help\n",
            extra_env={"APPLY_SUSFS": "1", "APPLY_KSU": "0"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("APPLY_SUSFS=1", result.stderr)

    def test_stable_kabi_is_forced_for_both_make_phases(self):
        source = BUILD.read_text(encoding="utf-8")
        start = source.index("# ------------------------------------------------------------------ 编译")
        end = source.index('\nIMG=', start)
        build_section = source[start:end]
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "common").mkdir()
            script = (
                "set -euo pipefail\n"
                'if command -v cygpath >/dev/null; then WORKDIR=$(cygpath -u "$1"); else WORKDIR=$1; fi\n'
                "JOBS=1 USE_CCACHE=0 FINASU_CERT_SIZE=0x103 FINASU_CERT_HASH=test\n"
                "log() { :; }; info() { :; }; warn() { :; }\n"
                "make() {\n"
                '  printf "%s|%s|%s\\n" "$KBUILD_GENDWARFKSYMS_STABLE" "$KCFLAGS" "$*" >> "$WORKDIR/make-calls"\n'
                "}\n"
                + build_section
            )
            result = self.run_shell(script, work, extra_env={"KBUILD_GENDWARFKSYMS_STABLE": "0"})
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = (work / "make-calls").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(calls), 2)
            for call in calls:
                stable, flags, args = call.split("|", 2)
                self.assertEqual(stable, "1")
                self.assertIn("KBUILD_GENDWARFKSYMS_STABLE=1", args)
                self.assertNotIn("-fno-stack-protector", flags)
            self.assertIn("gki_defconfig", calls[0])
            self.assertNotIn("Image", calls[0])
            self.assertIn("Image", calls[1])
            self.assertNotIn("gki_defconfig", calls[1])

    def source_guard(self, work):
        source = BUILD.read_text(encoding="utf-8")
        start = source.index('SOURCE_MARKER=')
        end = source.index('if [ ! -d "$WORKDIR/common" ]; then', start)
        return self.run_shell(
            "set -euo pipefail\n"
            'if command -v cygpath >/dev/null; then WORKDIR=$(cygpath -u "$1"); else WORKDIR=$1; fi\n'
            "KERNEL_ZIP=https://example.invalid/pinned-source.zip\n"
            'die() { echo "$*" >&2; exit 1; }\n'
            + source[start:end],
            work,
        )

    def test_source_reuse_requires_matching_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            common = work / "common"
            common.mkdir()
            sentinel = common / "user-change"
            sentinel.write_text("preserve", encoding="utf-8")
            self.assertNotEqual(self.source_guard(work).returncode, 0)
            marker = work / ".kernel-source-url"
            marker.write_text("https://example.invalid/other-source.zip\n", encoding="utf-8")
            self.assertNotEqual(self.source_guard(work).returncode, 0)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")
            marker.write_text("https://example.invalid/pinned-source.zip\n", encoding="utf-8")
            self.assertEqual(self.source_guard(work).returncode, 0)

    def test_background_download_failure_is_not_hidden_by_last_success(self):
        source = BUILD.read_text(encoding="utf-8")
        start = source.index('    DOWNLOAD_FAILED=0')
        end = source.index('\nelse\n', start)
        result = self.run_shell(
            "set -euo pipefail\n"
            'die() { echo "$*" >&2; exit 1; }\n'
            "(exit 3) & P1=$!\n"
            "(exit 0) & P2=$!\n"
            "(exit 0) & P3=$!\n"
            "(exit 0) & P4=$!\n"
            + source[start:end]
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("下载失败", result.stderr)


if __name__ == "__main__":
    unittest.main()
