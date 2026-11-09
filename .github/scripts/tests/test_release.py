"""Real Git and CLI publication tests. No network, native tools, or credentials."""

import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import unittest
import uuid


SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parents[1]
sys.path.insert(0, str(SCRIPTS))
import version

SPEC = importlib.util.spec_from_file_location("release_protocol", SCRIPTS / "release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
ANCHOR = "fbc46b8e8d3c2ab8c412bea637172643bc6b4a3c"
TRAILER = "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
from urllib.parse import parse_qs, unquote, urlsplit

home = pathlib.Path(os.environ["FAKE_HOME"])
config = json.loads((home / "config.json").read_text())
args = sys.argv[1:]
payload = None
if "--input" in args and args[args.index("--input") + 1] == "-":
    payload = json.load(sys.stdin)
with (home / "calls.jsonl").open("a") as out:
    out.write(json.dumps({"args": args, "payload": payload}) + "\n")
def fail(message):
    print(message, file=sys.stderr)
    sys.exit(1)
if args[:2] == ["auth", "status"]:
    if config.get("auth_fail"):
        fail("not authenticated")
    sys.exit(0)
if args[:1] != ["api"] or args[1:3] != ["--hostname", "github.com"]:
    fail("unexpected gh command")
method = args[args.index("--method") + 1]
endpoint = args[args.index("--method") + 2]
if config.get("api_fail"):
    fail(config["api_fail"])
if method == "GET" and "/git/matching-refs/" in endpoint:
    prefix = "refs/" + endpoint.split("/git/matching-refs/", 1)[1]
    raw = subprocess.check_output(["git", "--git-dir", os.environ["FAKE_ORIGIN"],
                                   "for-each-ref", "--format=%(refname) %(objectname) %(objecttype)",
                                   prefix]).decode()
    answer = config.get("matching_refs", [
        {"ref": ref, "object": {"sha": sha, "type": kind}}
        for ref, sha, kind in (line.split() for line in raw.splitlines())
    ])
elif method == "GET" and "/git/ref/" in endpoint:
    ref = "refs/" + endpoint.split("/git/ref/", 1)[1]
    if config.get("tag_no_create"):
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()
    else:
        sha = subprocess.check_output(["git", "--git-dir", os.environ["FAKE_ORIGIN"],
                                       "rev-parse", "--verify", ref]).decode().strip()
    answer = {"ref": ref, "object": {"sha": sha, "type": "commit"}}
    if config.get("fresh_ref_wrong"):
        answer["object"]["sha"] = "0" * len(sha)
elif method == "GET":
    page = parse_qs(urlsplit(endpoint).query)["page"][0]
    answer = config.get("pages", {}).get(page, [])
    if answer == "FAIL":
        fail("network/auth/404 failure")
    if answer == "MALFORMED":
        print("{")
        sys.exit(0)
    if answer == "DUPLICATE":
        print('[{"id":1,"id":2,"tag_name":"old","draft":false,"prerelease":false}]')
        sys.exit(0)
    if answer == "NONFINITE":
        print('[{"id":1,"tag_name":"old","draft":false,"prerelease":false,"other":NaN}]')
        sys.exit(0)
elif endpoint.endswith("/git/refs"):
    ref, sha = payload["ref"], payload["sha"]
    def create():
        return subprocess.run(["git", "--git-dir", os.environ["FAKE_ORIGIN"],
                               "update-ref", ref, sha, "0" * len(sha)],
                              capture_output=True)
    if config.get("tag_race"):
        create()
    if not config.get("tag_no_create"):
        result = create()
        if result.returncode:
            fail("422 reference already exists")
    if config.get("tag_lost_response"):
        fail("connection lost after creating tag")
    answer = {"ref": ref, "object": {"sha": sha, "type": "commit"}}
    if config.get("tag_wrong_response"):
        answer["object"]["sha"] = "0" * len(sha)
elif endpoint.endswith("/releases"):
    if config.get("release_fail"):
        fail("release creation failed")
    (home / "release-created").write_text("731")
    release_id = config.get("release_id", 731)
    answer = dict(payload, id=release_id,
                  upload_url="https://uploads.github.com/repos/fixture/plugin/releases/"
                  + str(release_id) + "/assets{?name,label}")
    if config.get("release_bad"):
        answer[config["release_bad"]] = True
elif "/releases/731/assets?" in endpoint:
    asset = pathlib.Path(args[args.index("--input") + 1])
    name = parse_qs(urlsplit(endpoint).query)["name"][0]
    count_file = home / "uploads"
    count = int(count_file.read_text()) + 1 if count_file.exists() else 1
    count_file.write_text(str(count))
    if config.get("upload_fail") == count:
        fail("upload failed")
    answer = {"id": 800 + count, "name": name, "size": asset.stat().st_size,
              "state": "uploaded"}
    bad = config.get("asset_bad")
    if bad == "size":
        answer["size"] = True
    elif bad == "id":
        answer["id"] = True
    elif bad == "duplicate":
        answer["id"] = 801
    elif bad == "state":
        answer["state"] = "starter"
    elif bad == "name":
        answer["name"] = name + ".unexpected"
else:
    fail("unexpected endpoint: " + endpoint)
print(json.dumps(answer))
'''


class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.area = ROOT / (".release-tests-" + uuid.uuid4().hex)
        cls.area.mkdir()
        cls.seed = cls.area / "seed"
        cls.seed.mkdir()
        # History is pinned to intake, never the moving implementation HEAD.
        paths = {
            *map(str, version.SOURCE_PATHS), *release.GENERATED,
            "4DPlugin-Running-Application.cpp", ".gitignore",
            ".github/workflows/release.yml", "constants.xlf", "constants.json",
            "test/Plugins/Running Application.bundle/Contents/Resources/constants.xlf",
            "test/Plugins/Running Application.bundle/Contents/Resources/manifest.json",
            "test/Plugins/Running Application.bundle/Contents/QuickLook/Thumbnail.png",
            "test/Plugins/Running Application.bundle/Contents/Windows/Running Application.4DX",
            "test/Plugins/Running Application.bundle/Contents/Windows64/Running Application.4DX",
        }
        for path in paths:
            raw = subprocess.check_output(["git", "show", f"{ANCHOR}:{path}"], cwd=ROOT)
            target = cls.seed / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        # Parent's in-progress coordinated version policy is fixture input.
        for path in version.SOURCE_PATHS:
            (cls.seed / path).write_bytes((ROOT / path).read_bytes())
        (cls.seed / ".github/scripts").mkdir(parents=True)
        for name in ("release.py", "version.py"):
            shutil.copyfile(SCRIPTS / name, cls.seed / ".github/scripts" / name)
        cls.call_git(cls.seed, "init", "-q")
        cls.call_git(cls.seed, "config", "user.name", "Release Fixture")
        cls.call_git(cls.seed, "config", "user.email", "fixture@example.invalid")
        cls.call_git(cls.seed, "add", ".")
        cls.call_git(cls.seed, "commit", "-qm", "Publication fixture\n\n" + TRAILER)
        cls.sha = cls.call_git(cls.seed, "rev-parse", "HEAD").strip()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.area)

    @staticmethod
    def call_git(root, *args):
        return subprocess.check_output(["git", *args], cwd=root,
                                       stderr=subprocess.STDOUT).decode()

    def setUp(self):
        self.home = self.area / uuid.uuid4().hex
        self.home.mkdir()
        self.origin = self.home / "origin.git"
        self.repo = self.home / "repo"
        self.call_git(self.home, "clone", "--bare", "-q", str(self.seed), str(self.origin))
        self.call_git(self.home, "clone", "-q", str(self.origin), str(self.repo))
        self.bin = self.home / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        self.config = {}
        self.current = version.read(self.repo)[0]
        self.tag = "v" + self.current
        self.env = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "GITHUB_REPOSITORY": "fixture/plugin",
            "GITHUB_OUTPUT": str(self.home / "output"),
            "FAKE_HOME": str(self.home),
            "FAKE_ORIGIN": str(self.origin),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN"):
            self.env.pop(key, None)

    def invoke(self, command="preflight", success=True, sha=None):
        (self.home / "config.json").write_text(json.dumps(self.config))
        args = [sys.executable, ".github/scripts/release.py", command,
                "--sha", sha or self.sha]
        if command == "publish":
            args += ["--zip", f"Running Application-{self.current}.zip",
                     "--dmg", f"Running Application-{self.current}.dmg"]
        result = subprocess.run(args, cwd=self.repo, env=self.env, capture_output=True,
                                text=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stderr)
        return result

    def calls(self):
        path = self.home / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def build(self, binary=True):
        bundle = self.repo / release.BUNDLE
        current, settings, _, source, localized = version.read(self.repo)
        data = dict(source[0])
        for key in ("CFBundleShortVersionString", "CFBundleVersion"):
            if key in data:
                data[key] = version.resolve(data[key], settings)
        (bundle / "Contents/Info.plist").write_bytes(
            plistlib.dumps(data, fmt=plistlib.FMT_BINARY if binary else plistlib.FMT_XML))
        localized_data = {
            key: f"Running Application version {current}, fixture" if key == "CFBundleGetInfoString"
            else current
            for key, _ in localized[Path("English.lproj/InfoPlist.strings")][1]
        }
        (bundle / "Contents/Resources/English.lproj/InfoPlist.strings").write_bytes(
            plistlib.dumps(localized_data, fmt=plistlib.FMT_BINARY))
        (bundle / "Contents/MacOS/Running Application").write_bytes(b"new universal binary")
        (bundle / "Contents/_CodeSignature/CodeResources").write_bytes(b"new signature")
        for extension in ("zip", "dmg"):
            (self.repo / f"Running Application-{current}.{extension}").write_bytes(
                b"packaged " + extension.encode())
        for prefix in release.INTERMEDIATES:
            directory = self.repo / prefix
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "intermediate.dat").write_bytes(b"xcode")

    def test_preflight_clean_outputs(self):
        self.invoke()
        self.assertEqual((self.home / "output").read_text(),
                         f"version={self.current}\ntag={self.tag}\n")
        self.assertEqual(len(self.calls()), 3)
        self.assertEqual(self.calls()[0]["args"], ["auth", "status", "--hostname", "github.com"])
        self.assertEqual(self.calls()[1]["args"],
                         ["api", "--hostname", "github.com", "--method", "GET",
                          "repos/fixture/plugin/releases?per_page=100&page=1"])
        self.assertEqual(self.calls()[2]["args"][-1],
                         f"repos/fixture/plugin/git/matching-refs/tags/{self.tag}")

    def test_preflight_does_not_generate_untracked_bytecode(self):
        self.env.pop("PYTHONDONTWRITEBYTECODE")
        self.invoke()
        self.assertFalse((self.repo / ".github/scripts/__pycache__").exists())

    def test_protocol_environment_missing_fails_before_api(self):
        for key in ("GITHUB_OUTPUT", "GITHUB_REPOSITORY"):
            with self.subTest(key=key):
                original = self.env.pop(key)
                self.invoke(success=False)
                self.assertEqual(self.calls(), [])
                self.env[key] = original

    def test_publish_retains_actual_generated_bytes_and_create_only_payloads(self):
        self.build()
        original = {p: (self.repo / p).read_bytes() for p in release.GENERATED}
        self.invoke("publish")
        self.assertEqual(original, {p: (self.repo / p).read_bytes() for p in release.GENERATED})
        calls = self.calls()
        mutations = [call for call in calls if "--method" in call["args"]
                     and call["args"][call["args"].index("--method") + 1] != "GET"]
        self.assertEqual(len(mutations), 4)
        self.assertEqual(mutations[0]["payload"], {"ref": f"refs/tags/{self.tag}", "sha": self.sha})
        self.assertEqual(mutations[1]["payload"], {
            "tag_name": self.tag, "name": self.tag, "target_commitish": self.sha,
            "draft": False, "prerelease": False, "body": "", "generate_release_notes": False,
        })
        for call, extension in zip(mutations[2:], ("zip", "dmg")):
            endpoint = call["args"][call["args"].index("--method") + 2]
            self.assertEqual(endpoint, "https://uploads.github.com/repos/fixture/plugin/"
                             f"releases/731/assets?name=Running.Application-{self.current}.{extension}")
        self.assertEqual(self.call_git(self.repo, "ls-remote", "--refs", "origin",
                                      f"refs/tags/{self.tag}").split()[0], self.sha)

    def test_xml_built_metadata_also_accepted(self):
        self.build(binary=False)
        self.invoke("publish")

    def test_initial_generated_dirt_rejected(self):
        (self.repo / next(iter(release.GENERATED))).write_bytes(b"initial dirt")
        self.invoke(success=False)
        self.assertEqual(self.calls(), [])

    def test_initial_untracked_and_ignored_rejected(self):
        for path in ("surprise.cpp", "build/ignored.cpp"):
            with self.subTest(path=path):
                target = self.repo / path
                target.parent.mkdir(exist_ok=True)
                target.write_bytes(b"source")
                self.invoke(success=False)
                self.assertEqual(self.calls(), [])
                target.unlink()

    def test_raw_hidden_worktree_and_staged_reversal_rejected(self):
        source = self.repo / "4DPlugin-Running-Application.cpp"
        original = source.read_bytes()
        for flag in ("--assume-unchanged", "--skip-worktree"):
            with self.subTest(flag=flag):
                self.call_git(self.repo, "update-index", flag, source.name)
                source.write_bytes(original + b"\n// changed")
                self.invoke(success=False)
                self.assertEqual(self.calls(), [])
                source.write_bytes(original)
                self.call_git(self.repo, "update-index", "--no" + flag[1:], source.name)
        source.write_bytes(original + b"\n// staged")
        self.call_git(self.repo, "add", source.name)
        source.write_bytes(original)
        self.invoke(success=False)
        self.assertEqual(self.calls(), [])

    def test_raw_bytes_not_clean_filtered_bytes(self):
        self.call_git(self.repo, "config", "core.autocrlf", "true")
        source = self.repo / "4DPlugin-Running-Application.cpp"
        source.write_bytes(source.read_bytes().replace(b"\n", b"\r\n"))
        self.invoke(success=False)
        self.assertEqual(self.calls(), [])

    def test_wrong_head_rejected_before_api(self):
        self.invoke(sha="0" * 40, success=False)
        self.assertEqual(self.calls(), [])

    def test_publish_immutable_bundle_inputs_rejected(self):
        self.build()
        for suffix in ("Resources/constants.xlf", "Resources/manifest.json",
                       "QuickLook/Thumbnail.png", "Windows/Running Application.4DX",
                       "Windows64/Running Application.4DX"):
            with self.subTest(path=suffix):
                target = self.repo / release.BUNDLE / "Contents" / suffix
                original = target.read_bytes()
                target.write_bytes(original + b"changed")
                self.invoke("publish", success=False)
                self.assertEqual(self.calls(), [])
                target.write_bytes(original)

    def test_publish_ignored_source_and_unexpected_artifacts_rejected(self):
        self.build()
        for path in ("build/evil.cpp", "extra.config", ".hidden", "Running Application-0.0.0.zip",
                     "build/Running Application.build/Default/Running Application.build/evil.cpp",
                     "build/XCBuildData/extra.config"):
            with self.subTest(path=path):
                target = self.repo / path
                target.write_bytes(b"unexpected")
                self.invoke("publish", success=False)
                self.assertEqual(self.calls(), [])
                target.unlink()

    def test_publish_staged_source_reversal_rejected(self):
        self.build()
        target = self.repo / ".github/workflows/release.yml"
        raw = target.read_bytes()
        target.write_bytes(raw + b"\n# modified")
        self.call_git(self.repo, "add", str(target.relative_to(self.repo)))
        target.write_bytes(raw)
        self.invoke("publish", success=False)
        self.assertEqual(self.calls(), [])

    def test_publish_hidden_index_source_flags_rejected(self):
        self.build()
        target = self.repo / "4DPlugin-Running-Application.cpp"
        raw = target.read_bytes()
        for flag in ("--assume-unchanged", "--skip-worktree"):
            with self.subTest(flag=flag):
                self.call_git(self.repo, "update-index", flag, target.name)
                target.write_bytes(raw + b"\n// changed")
                self.invoke("publish", success=False)
                self.assertEqual(self.calls(), [])
                target.write_bytes(raw)
                self.call_git(self.repo, "update-index", "--no" + flag[1:], target.name)

    def test_generated_symlink_and_missing_binary_rejected(self):
        self.build()
        target = self.repo / release.BUNDLE / "Contents/MacOS/Running Application"
        raw = target.read_bytes()
        target.unlink()
        self.invoke("publish", success=False)
        self.assertEqual(self.calls(), [])
        target.symlink_to(self.repo / "constants.xlf")
        self.invoke("publish", success=False)
        self.assertEqual(self.calls(), [])
        target.unlink()
        target.write_bytes(raw)

    def test_staged_generated_output_is_allowed_and_preserved(self):
        self.build()
        path = str(release.BUNDLE / "Contents/MacOS/Running Application")
        self.call_git(self.repo, "add", path)
        staged = self.call_git(self.repo, "ls-files", "--stage", path)
        self.invoke("publish")
        self.assertEqual(staged, self.call_git(self.repo, "ls-files", "--stage", path))

    def test_built_plist_and_copied_localization_must_match(self):
        self.build()
        for suffix in ("Info.plist", "Resources/English.lproj/InfoPlist.strings"):
            with self.subTest(suffix=suffix):
                target = self.repo / release.BUNDLE / "Contents" / suffix
                raw = target.read_bytes()
                data = plistlib.loads(raw)
                data["CFBundleShortVersionString"] = "99.99.99"
                target.write_bytes(plistlib.dumps(data))
                self.invoke("publish", success=False)
                self.assertEqual(self.calls(), [])
                target.write_bytes(raw)

    def test_pagination_including_draft_collision(self):
        self.config["pages"] = {
            "1": [{"id": i + 1, "tag_name": f"old-{i}", "draft": False, "prerelease": False}
                  for i in range(100)],
            "2": [{"id": 101, "tag_name": self.tag, "draft": True, "prerelease": False}],
        }
        self.invoke(success=False)
        self.assertEqual(len(self.calls()), 3)
        self.assertFalse((self.home / "output").exists())

    def test_all_pages_success(self):
        self.config["pages"] = {
            "1": [{"id": i + 1, "tag_name": f"old-{i}", "draft": True, "prerelease": False}
                  for i in range(100)],
            "2": [],
        }
        self.invoke()
        self.assertEqual(len(self.calls()), 4)

    def test_partial_release_page_does_not_end_pagination(self):
        self.config["pages"] = {
            "1": [{"id": 1, "tag_name": "old", "draft": False, "prerelease": False}],
            "2": [{"id": 2, "tag_name": self.tag, "draft": True, "prerelease": False}],
        }
        self.invoke(success=False)
        self.assertEqual(len(self.calls()), 3)

    def test_authenticated_matching_refs_exact_collision_without_remote_tag(self):
        self.config["matching_refs"] = [
            {"ref": f"refs/tags/{self.tag}", "object": {"sha": self.sha, "type": "commit"}}]
        result = self.invoke(success=False)
        self.assertIn("GitHub tag already exists", result.stderr)

    def test_matching_refs_prefix_is_not_exact_collision(self):
        self.config["matching_refs"] = [
            {"ref": f"refs/tags/{self.tag}-other", "object": {"sha": self.sha, "type": "tag"}}]
        self.invoke()

    def test_malformed_matching_refs_fails_closed(self):
        for value in ({}, [True], [{"ref": f"refs/tags/{self.tag}-other",
                                   "object": {"sha": True, "type": "commit"}}]):
            with self.subTest(value=value):
                self.config["matching_refs"] = value
                self.invoke(success=False)
                self.assertFalse((self.home / "output").exists())

    def test_auth_failure_closed(self):
        self.config["auth_fail"] = True
        self.invoke(success=False)
        self.assertEqual(len(self.calls()), 1)

    def test_http_and_malformed_pages_closed(self):
        for page in ("FAIL", "MALFORMED", "DUPLICATE", "NONFINITE", {}, [True],
                     [{"id": True, "tag_name": "old", "draft": False, "prerelease": False}],
                     [{"id": 1, "tag_name": "old", "draft": "false", "prerelease": False}],
                     [{"id": 1, "tag_name": "old", "prerelease": False}]):
            with self.subTest(page=page):
                self.config["pages"] = {"1": page}
                self.invoke(success=False)
                self.assertFalse((self.home / "output").exists())

    def test_duplicate_release_id_between_pages_fails_closed(self):
        self.config["pages"] = {
            "1": [{"id": i + 1, "tag_name": str(i), "draft": False, "prerelease": False}
                  for i in range(100)],
            "2": [{"id": 1, "tag_name": "other", "draft": False, "prerelease": False}],
        }
        self.invoke(success=False)
        self.assertFalse((self.home / "output").exists())

    def test_page_two_failure_closed(self):
        self.config["pages"] = {
            "1": [{"id": i + 1, "tag_name": str(i), "draft": False, "prerelease": False}
                  for i in range(100)], "2": "FAIL",
        }
        self.invoke(success=False)
        self.assertEqual(len(self.calls()), 3)

    def test_existing_remote_exact_tag_rejected(self):
        self.call_git(self.home, "--git-dir", str(self.origin), "update-ref",
                      f"refs/tags/{self.tag}", self.sha)
        self.invoke(success=False)
        self.assertFalse((self.home / "output").exists())

    def test_prefix_tag_is_not_collision(self):
        self.call_git(self.home, "--git-dir", str(self.origin), "update-ref",
                      f"refs/tags/{self.tag}-other", self.sha)
        self.invoke()

    def test_publish_rechecks_availability_after_preflight(self):
        self.invoke()
        self.build()
        self.config["pages"] = {"1": [
            {"id": 73, "tag_name": self.tag, "draft": False, "prerelease": False}]}
        self.invoke("publish", success=False)
        self.assertFalse(any("POST" in call["args"] for call in self.calls()))

    def test_same_sha_tag_race_fails_create_only_and_retains_tag(self):
        self.build()
        self.config["tag_race"] = True
        result = self.invoke("publish", success=False)
        self.assertIn("manual recovery required", result.stderr)
        self.assertFalse((self.home / "release-created").exists())
        self.assertEqual(self.call_git(self.home, "--git-dir", str(self.origin), "rev-parse",
                                      f"refs/tags/{self.tag}").strip(), self.sha)
        self.assertEqual(sum("POST" in c["args"] for c in self.calls()), 1)

    def test_lost_tag_response_never_retried_or_rolled_back(self):
        self.build()
        self.config["tag_lost_response"] = True
        result = self.invoke("publish", success=False)
        self.assertIn("manual recovery required", result.stderr)
        self.assertEqual(sum("POST" in c["args"] for c in self.calls()), 1)
        self.assertEqual(self.call_git(self.home, "--git-dir", str(self.origin), "rev-parse",
                                      f"refs/tags/{self.tag}").strip(), self.sha)

    def test_release_creation_failure_retains_tag(self):
        self.build()
        self.config["release_fail"] = True
        result = self.invoke("publish", success=False)
        self.assertIn("Partial tag/release/assets may be retained", result.stderr)
        self.assertEqual(sum("POST" in c["args"] for c in self.calls()), 2)

    def test_exact_remote_tag_verified_before_release_post(self):
        self.build()
        self.config["tag_no_create"] = True
        result = self.invoke("publish", success=False)
        self.assertIn("Created remote tag does not match", result.stderr)
        self.assertIn("manual recovery required", result.stderr)
        self.assertEqual(sum("POST" in c["args"] for c in self.calls()), 1)

    def test_malformed_created_tag_does_not_create_release(self):
        self.build()
        self.config["tag_wrong_response"] = True
        result = self.invoke("publish", success=False)
        self.assertIn("manual recovery required", result.stderr)
        self.assertEqual(sum("POST" in c["args"] for c in self.calls()), 1)

    def test_fresh_authenticated_ref_verification_before_release(self):
        self.build()
        self.config["fresh_ref_wrong"] = True
        result = self.invoke("publish", success=False)
        self.assertIn("Fresh GitHub tag does not match", result.stderr)
        self.assertIn("manual recovery required", result.stderr)
        self.assertEqual(sum("POST" in c["args"] for c in self.calls()), 1)

    def test_malformed_created_release_id_does_not_upload(self):
        self.build()
        self.config["release_bad"] = "id"
        result = self.invoke("publish", success=False)
        self.assertIn("manual recovery required", result.stderr)
        self.assertEqual(sum("POST" in c["args"] for c in self.calls()), 2)

    def test_reused_release_id_does_not_upload(self):
        self.build()
        self.config["release_id"] = 731
        self.config["pages"] = {"1": [
            {"id": 731, "tag_name": "old", "draft": False, "prerelease": False}]}
        result = self.invoke("publish", success=False)
        self.assertIn("Malformed created release response", result.stderr)
        self.assertEqual(sum("POST" in c["args"] for c in self.calls()), 2)

    def test_created_release_upload_url_must_bind_new_id(self):
        self.build()
        self.config["release_bad"] = "upload_url"
        result = self.invoke("publish", success=False)
        self.assertIn("upload URL does not match new release ID", result.stderr)
        self.assertEqual(sum("POST" in c["args"] for c in self.calls()), 2)

    def test_aliased_empty_and_symlink_artifacts_fail_before_api(self):
        self.build()
        archive = self.repo / f"Running Application-{self.current}.zip"
        image = self.repo / f"Running Application-{self.current}.dmg"
        archive.unlink()
        archive.hardlink_to(image)
        self.invoke("publish", success=False)
        self.assertEqual(self.calls(), [])
        archive.unlink()
        archive.symlink_to(image)
        self.invoke("publish", success=False)
        self.assertEqual(self.calls(), [])
        archive.unlink()
        archive.write_bytes(b"")
        self.invoke("publish", success=False)
        self.assertEqual(self.calls(), [])

    def test_upload_failure_retains_new_release_and_first_asset(self):
        self.build()
        self.config["upload_fail"] = 2
        result = self.invoke("publish", success=False)
        self.assertIn("manual recovery required", result.stderr)
        self.assertTrue((self.home / "release-created").exists())
        self.assertEqual((self.home / "uploads").read_text(), "2")
        self.assertEqual(sum("POST" in c["args"] for c in self.calls()), 4)

    def test_strict_asset_response_validation(self):
        self.build()
        for bad in ("size", "id", "state", "name", "duplicate"):
            with self.subTest(bad=bad):
                # Each independent attempt has a fresh remote ref; no protocol rollback.
                other = self.home / ("origin-" + bad + ".git")
                self.call_git(self.home, "clone", "--bare", "-q", str(self.seed), str(other))
                self.call_git(self.repo, "remote", "set-url", "origin", str(other))
                self.env["FAKE_ORIGIN"] = str(other)
                self.config["asset_bad"] = bad
                result = self.invoke("publish", success=False)
                self.assertIn("manual recovery required", result.stderr)


if __name__ == "__main__":
    unittest.main()
