"""Offline execution of the workflow's actual Bash, with native tools stubbed."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / ".github/scripts"))
import version

CURRENT_VERSION = version.read(ROOT)[0]
WORKFLOW = ROOT / ".github/workflows/release.yml"
PRODUCT = "Running Application"
BUNDLE = Path("test/Plugins") / (PRODUCT + ".bundle")


def workflow_steps():
    """Extract this workflow's literal run blocks without a YAML dependency."""
    steps = {}
    name = None
    lines = WORKFLOW.read_text().splitlines()
    for index, line in enumerate(lines):
        if line.startswith("      - name: "):
            name = line.split(": ", 1)[1]
        if line == "        run: |":
            block = []
            for body in lines[index + 1:]:
                if body and not body.startswith("          "):
                    break
                block.append(body[10:] if body else "")
            steps[name] = "\n".join(block) + "\n"
    return steps


STUB = r'''#!PYTHON
import json
import os
from pathlib import Path
import sys

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["NATIVE_LOG"], "a") as log:
    log.write(json.dumps([tool] + args) + "\n")
failure = os.environ.get("FAIL_TOOL", "")
if failure == tool or failure == tool + ":" + " ".join(args[:2]):
    sys.exit(7)
bundle = Path(os.environ["BUNDLE_PATH"])
if tool == "base64":
    Path(args[args.index("-o") + 1]).write_bytes(b"fake certificate")
elif tool == "security" and args[0] == "find-identity":
    print('1) ABCDEF "Developer ID Application: Offline Test"')
elif tool == "xcodebuild":
    assert args[args.index("-target") + 1] == "Running Application"
    assert args[args.index("-configuration") + 1] == "Default"
    assert "ARCHS=arm64 x86_64" in args
    assert "ONLY_ACTIVE_ARCH=NO" in args
    assert "MACOSX_DEPLOYMENT_TARGET=11.0" in args
    assert "CONFIGURATION_BUILD_DIR=" + os.environ["GITHUB_WORKSPACE"] + "/test/Plugins" in args
    assert "OBJROOT=" + os.environ["GITHUB_WORKSPACE"] + "/build" in args
    assert not (bundle / "Contents/MacOS/Running Application").exists()
    assert not (bundle / "Contents/Info.plist").exists()
    assert not (bundle / "Contents/_CodeSignature").exists()
    assert not Path("build/Running Application.build/Default/Running Application.build").exists()
    if os.environ.get("STALE_SIBLING_ONLY") == "1":
        sys.exit(0)
    for relative in ("Contents/MacOS/Running Application", "Contents/Info.plist",
                     "Contents/Resources/English.lproj/InfoPlist.strings"):
        output = bundle / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("fresh native output")
elif tool == "lipo":
    assert args[0] == "-archs"
    assert Path(args[1]).read_text() == "fresh native output"
    print(os.environ.get("ARCHITECTURES", "arm64 x86_64"))
elif tool == "ditto":
    assert args[:4] == ["-c", "-k", "--keepParent", str(bundle)]
    Path(args[-1]).write_bytes(b"offline zip")
elif tool == "hdiutil":
    assert args[0] == "create" and args[args.index("-srcfolder") + 1] == str(bundle)
    Path(args[-1]).write_bytes(b"offline dmg")
elif tool == "xcrun" and args[:2] == ["notarytool", "submit"]:
    assert "--wait" in args
    assert args[args.index("--output-format") + 1] == "json"
    assert Path(args[2]).is_file()
    extension = Path(args[2]).suffix[1:].upper()
    print(os.environ.get(extension + "_NOTARY_JSON", '{"status":"Accepted"}'))
    sys.exit(int(os.environ.get(extension + "_NOTARY_EXIT", "0")))
'''


HELPER = r'''import json
import os
from pathlib import Path
import sys
args = sys.argv[1:]
with open(os.environ["NATIVE_LOG"], "a") as log:
    log.write(json.dumps(["release.py"] + args) + "\n")
assert args[1:3] == ["--sha", os.environ["GITHUB_SHA"]]
if args[0] == "preflight":
    assert len(args) == 3
    if os.environ.get("FAIL_PREFLIGHT") == "1":
        sys.exit(4)
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        current = os.environ["VERSION"]
        output.write(f"version={current}\ntag=v{current}\n")
else:
    assert args[0] == "publish"
    assert args[3:7:2] == ["--zip", "--dmg"]
    assert Path(args[4]).is_file() and Path(args[6]).is_file()
'''


class NativeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.steps = workflow_steps()
        self.work = Path(__file__).parent / (".native-" + uuid.uuid4().hex)
        self.work.mkdir()
        self.addCleanup(shutil.rmtree, self.work)
        bin_path = self.work / "bin"
        bin_path.mkdir()
        for tool in ("python3", "grep", "head", "sed"):
            source = sys.executable if tool == "python3" else shutil.which(tool)
            self.assertIsNotNone(source)
            (bin_path / tool).symlink_to(source)
        for tool in ("base64", "security", "xcodebuild", "lipo", "codesign",
                     "ditto", "hdiutil", "xcrun"):
            path = bin_path / tool
            path.write_text(STUB.replace("#!PYTHON", "#!" + sys.executable))
            path.chmod(0o755)
        helper = self.work / ".github/scripts/release.py"
        helper.parent.mkdir(parents=True)
        helper.write_text(HELPER)
        self.bundle = self.work / BUNDLE
        self.preserved = {}
        for relative in (
            "Contents/Windows/Running Application.4DX",
            "Contents/Windows64/Running Application.4DX",
            "Contents/MacOS/runtime.dylib",
            "Contents/Resources/constants.xlf",
            "Contents/Resources/manifest.json",
            "Contents/Resources/English.lproj/custom-resource",
            "Contents/QuickLook/Thumbnail.png",
        ):
            path = self.bundle / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("preserve " + relative).encode())
            self.preserved[path] = path.read_bytes()
        for relative in (
            "Contents/MacOS/Running Application",
            "Contents/Info.plist",
            "Contents/Resources/English.lproj/InfoPlist.strings",
            "Contents/_CodeSignature/CodeResources",
        ):
            path = self.bundle / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("stale")
        for relative in (
            "build/Running Application.build/Default/Running Application.build/stale.o",
            "build/Running Application.build/Development/Running Application.build/keep.o",
            "build/Other.build/keep.o",
            "test/Plugins/Default/Running Application.bundle/Contents/MacOS/Running Application",
            "test/Plugins/Default/Running Application.bundle/Contents/Resources/manifest.json",
            "test/Plugins/Default/Running Application.bundle/Contents/Windows64/Running Application.4DX",
            "test/Plugins/Default/Running Application.bundle/Contents/MacOS/runtime.dylib",
            "test/Plugins/Default/Other.bundle/Contents/MacOS/Other",
            "dependencies/keep.a",
        ):
            path = self.work / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("stale sibling or dependency")
            if relative not in (
                "build/Running Application.build/Default/Running Application.build/stale.o",
                "test/Plugins/Default/Running Application.bundle/Contents/MacOS/Running Application",
            ):
                self.preserved[path] = path.read_bytes()
        self.log = self.work / "native.jsonl"
        self.env_file = self.work / "github-env"
        self.env_file.touch()
        runner = self.work / "runner"
        runner.mkdir()
        self.env = {
            "PATH": str(bin_path),
            "HOME": str(self.work),
            "GITHUB_WORKSPACE": str(self.work),
            "GITHUB_SHA": "a" * 40,
            "GITHUB_ENV": str(self.env_file),
            "GITHUB_OUTPUT": str(self.work / "github-output"),
            "PRODUCT_NAME": PRODUCT,
            "XCODE_PROJECT": PRODUCT + ".xcodeproj",
            "MACOS_DEPLOYMENT_TARGET": "11.0",
            "BUNDLE_PATH": str(BUNDLE),
            "VERSION": CURRENT_VERSION,
            "KEYCHAIN_PATH": str(runner / "build.keychain-db"),
            "RUNNER_TEMP": str(runner),
            "CERT_BASE64": "offline",
            "CERT_PASSWORD": "offline",
            "KEYCHAIN_PASSWORD": "offline",
            "APPLE_ID": "offline",
            "TEAM_ID": "offline",
            "APP_PASSWORD": "offline",
            "NATIVE_LOG": str(self.log),
        }

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def run_step(self, name):
        script = self.steps[name]
        self.assertNotIn("${{", script)
        result = subprocess.run(
            ["/bin/bash", "--noprofile", "--norc", "-c", script],
            cwd=self.work, env=self.env, text=True, capture_output=True,
        )
        for line in self.env_file.read_text().splitlines():
            key, value = line.split("=", 1)
            self.env[key] = value
        return result

    def run_pipeline(self):
        for name in self.steps:
            result = self.run_step(name)
            if result.returncode:
                return name, result
        return None, result

    def assert_preserved(self):
        for path, content in self.preserved.items():
            self.assertEqual(path.read_bytes(), content, str(path))

    def assert_blocked(self, expected):
        name, result = self.run_pipeline()
        self.assertEqual(name, expected, result.stdout + result.stderr)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call[:2] == ["release.py", "publish"] for call in self.calls()))
        self.assert_preserved()

    def test_topology_and_protocol(self):
        text = WORKFLOW.read_text()
        self.assertRegex(text, r"(?m)^name: Build$")
        self.assertRegex(text, r"(?m)^on:\n  workflow_dispatch:\n\n")
        self.assertNotRegex(text, r"(?m)^\s+(inputs|push|pull_request|if):")
        self.assertEqual(re.findall(r"(?m)^    runs-on: (.+)$", text), ["macos-latest"])
        self.assertIn("ref: ${{ github.sha }}\n          fetch-depth: 0", text)
        self.assertIn("group: release-publication\n  cancel-in-progress: false", text)
        self.assertNotIn("github.ref", text)
        self.assertIn("id: version", text)
        self.assertIn("VERSION: ${{ steps.version.outputs.version }}", text)
        self.assertEqual(next(iter(self.steps)), "preflight")
        self.assertEqual(list(self.steps)[-1], "create GitHub Release")
        for secret in (
            "APPLE_DEVELOPER_ID_CERTIFICATE", "APPLE_DEVELOPER_ID_CERTIFICATE_PASSWORD",
            "KEYCHAIN_PASSWORD", "NOTARYTOOL_APPLE_ID", "NOTARYTOOL_TEAM_ID",
            "NOTARYTOOL_PASSWORD",
        ):
            self.assertIn("${{ secrets." + secret + " }}", text)

    def test_successful_full_pipeline_preserves_cross_platform_bundle(self):
        name, result = self.run_pipeline()
        self.assertIsNone(name, result.stdout + result.stderr)
        self.assert_preserved()
        calls = self.calls()
        self.assertEqual(calls[0], ["release.py", "preflight", "--sha", "a" * 40])
        self.assertEqual(calls[-1], [
            "release.py", "publish", "--sha", "a" * 40,
            "--zip", f"{PRODUCT}-{CURRENT_VERSION}.zip",
            "--dmg", f"{PRODUCT}-{CURRENT_VERSION}.dmg",
        ])
        self.assertEqual((self.work / "github-output").read_text(),
                         f"version={CURRENT_VERSION}\ntag=v{CURRENT_VERSION}\n")
        signatures = [call for call in calls if call[0] == "codesign"]
        self.assertIn("--options=runtime", signatures[0])
        self.assertIn("--timestamp", signatures[0])
        self.assertEqual(len(signatures), 3)
        for call in signatures[1:]:
            self.assertIn("--strict", call)
        submissions = [call for call in calls if call[:3] == ["xcrun", "notarytool", "submit"]]
        self.assertEqual([Path(call[3]).suffix for call in submissions], [".zip", ".dmg"])
        self.assertIn(["xcrun", "stapler", "staple", f"{PRODUCT}-{CURRENT_VERSION}.dmg"], calls)
        self.assertIn(["xcrun", "stapler", "validate", f"{PRODUCT}-{CURRENT_VERSION}.dmg"], calls)

    def test_preflight_failure_precedes_native_commands(self):
        self.env["FAIL_PREFLIGHT"] = "1"
        self.assert_blocked("preflight")
        self.assertEqual(len(self.calls()), 1)

    def test_cleanup_removes_only_selected_outputs_case_insensitively(self):
        english = self.bundle / "Contents/Resources/English.lproj"
        alternate = english.with_name("english.alternate")
        english.rename(alternate)
        alternate.rename(english.with_name("english.lproj"))
        for path in list(self.preserved):
            if english in path.parents:
                content = self.preserved.pop(path)
                self.preserved[english.with_name("english.lproj") / path.name] = content
        result = self.run_step("remove generated macOS outputs")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((english.with_name("english.lproj") / "InfoPlist.strings").exists())
        self.assertFalse((self.bundle / "Contents/MacOS/Running Application").exists())
        self.assertFalse((self.bundle / "Contents/Info.plist").exists())
        self.assertFalse((self.bundle / "Contents/_CodeSignature").exists())
        self.assertFalse((self.work / "build/Running Application.build/Default/Running Application.build").exists())
        self.assertFalse((self.work / "test/Plugins/Default/Running Application.bundle/Contents/MacOS/Running Application").exists())
        self.assert_preserved()

    def test_cleanup_failure_stops_before_build(self):
        info = self.bundle / "Contents/Info.plist"
        info.unlink()
        info.mkdir()
        self.assert_blocked("remove generated macOS outputs")
        self.assertFalse(any(call[0] == "xcodebuild" for call in self.calls()))

    def test_cleanup_rejects_symlink_traversal(self):
        macos = self.bundle / "Contents/MacOS"
        macos.rename(macos.with_name("MacOS-preserved"))
        macos.symlink_to(macos.with_name("MacOS-preserved"), target_is_directory=True)
        self.assert_blocked("remove generated macOS outputs")
        self.assertEqual((macos / PRODUCT).read_text(), "stale")

    def test_all_cleanup_ancestors_validated_before_any_deletion(self):
        english = self.bundle / "Contents/Resources/English.lproj"
        saved = english.with_name("English-preserved.lproj")
        english.rename(saved)
        english.symlink_to(saved, target_is_directory=True)
        before = {
            path: path.read_bytes()
            for path in self.work.rglob("*")
            if path.is_file() and not path.is_symlink()
        }
        result = self.run_step("remove generated macOS outputs")
        self.assertNotEqual(result.returncode, 0)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content, str(path))

    def test_stale_default_sibling_cannot_pass_as_fresh_canonical_output(self):
        self.env["STALE_SIBLING_ONLY"] = "1"
        self.assert_blocked("verify universal binary")
        self.assertFalse(any(call[0] == "codesign" for call in self.calls()))

    def test_architecture_tokens_are_exact(self):
        self.assertEqual(self.run_step("remove generated macOS outputs").returncode, 0)
        self.assertEqual(self.run_step("build macOS plugin (universal)").returncode, 0)
        for architectures, accepted in (
            ("arm64 x86_64", True), ("x86_64 arm64 i386", True),
            ("arm64 x86_64 arm64e", True), ("arm64e x86_64", False),
            ("arm64 x86_64h", False), ("arm64", False), ("x86_64", False), ("", False),
        ):
            with self.subTest(architectures=architectures):
                self.env["ARCHITECTURES"] = architectures
                result = self.run_step("verify universal binary")
                self.assertEqual(result.returncode == 0, accepted, result.stderr)

    def test_native_failures_prevent_publication(self):
        for tool, step in (
            ("security", "import Developer ID certificate"),
            ("xcrun:notarytool store-credentials", "store notarytool credentials"),
            ("xcodebuild", "build macOS plugin (universal)"),
            ("lipo", "verify universal binary"),
            ("codesign", "codesign"),
            ("codesign:--verify --deep", "codesign"),
            ("ditto", "package, notarize & staple"),
            ("hdiutil", "package, notarize & staple"),
            ("xcrun:stapler staple", "package, notarize & staple"),
            ("xcrun:stapler validate", "package, notarize & staple"),
        ):
            with self.subTest(tool=tool):
                self.env["FAIL_TOOL"] = tool
                self.assert_blocked(step)

    def test_both_notary_status_and_exit_are_required_for_each_artifact(self):
        for extension in ("ZIP", "DMG"):
            for response, exit_code in (
                ('{"status":"Rejected"}', "0"),
                ('{"status":"Invalid"}', "0"),
                ('{"status":"Accepted"}', "9"),
                ("not json", "0"),
                ('{}', "0"),
            ):
                with self.subTest(extension=extension, response=response, exit=exit_code):
                    self.env[extension + "_NOTARY_JSON"] = response
                    self.env[extension + "_NOTARY_EXIT"] = exit_code
                    self.assert_blocked("package, notarize & staple")
                    self.env.pop(extension + "_NOTARY_JSON")
                    self.env.pop(extension + "_NOTARY_EXIT")

    def test_final_strict_verification_failure_prevents_publication(self):
        for name in self.steps:
            if name == "package, notarize & staple":
                break
            result = self.run_step(name)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.env["FAIL_TOOL"] = "codesign:--verify --deep"
        result = self.run_step("package, notarize & staple")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("ZIP_PATH=", self.env_file.read_text())
        self.assert_preserved()


if __name__ == "__main__":
    unittest.main()
