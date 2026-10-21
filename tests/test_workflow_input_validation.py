import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "validate_manager_apk_url", ROOT / "scripts" / "validate_manager_apk_url.py")
url_validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(url_validator)


class ManagerApkUrlTests(unittest.TestCase):
    def test_accepts_absolute_https_url(self):
        self.assertTrue(url_validator.valid_https_url("https://example.com/builds/manager.apk"))

    def test_rejects_non_https_malformed_or_credentialed_urls(self):
        for value in (
            "http://example.com/app.apk",
            "file:///tmp/app.apk",
            "https:///missing-host.apk",
            "https://user:password@example.com/app.apk",
            "https://example.com/app.apk\n--output=/tmp/pwned",
            "https://example.com:bad/app.apk",
        ):
            with self.subTest(value=value):
                self.assertFalse(url_validator.valid_https_url(value))

    def test_workflow_keeps_the_user_value_out_of_run_script_interpolation(self):
        workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
        self.assertEqual(workflow.count("${{ inputs.manager_apk_url }}"), 1)
        self.assertIn("MANAGER_APK_URL: ${{ inputs.manager_apk_url }}", workflow)
        self.assertIn('python3 scripts/validate_manager_apk_url.py "$MANAGER_APK_URL"', workflow)
        self.assertIn("--proto '=https' --proto-redir '=https'", workflow)
        self.assertIn('--output manager.apk -- "$MANAGER_APK_URL"', workflow)


if __name__ == "__main__":
    unittest.main()
