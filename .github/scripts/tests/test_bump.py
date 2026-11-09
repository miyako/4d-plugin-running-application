import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parents[1]
sys.path.insert(0, str(SCRIPTS))
import version

TRAILER = "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
REAL_GIT = shutil.which("git")


class Bump(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "checkout"
        self.root.mkdir()
        self.remote = self.base / "remote.git"
        home = self.base / "home"
        home.mkdir()
        self.env = dict(os.environ, HOME=str(home), GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL=os.devnull, PYTHONDONTWRITEBYTECODE="1")
        for key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                    "GIT_COMMITTER_EMAIL", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            self.env.pop(key, None)
        self.git("init", "-q")
        self.git("symbolic-ref", "HEAD", "refs/heads/selected")
        for path in (*version.SOURCE_PATHS, Path(".github/scripts/version.py"),
                     Path(".github/scripts/bump.py"), Path(".github/scripts/release.py"),
                     Path(".github/hooks/pre-push")):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / path, target)
        self.git("add", ".")
        self.commit("fixture initial")
        self.previous = self.git("rev-parse", "HEAD")
        (self.root / "unrelated.txt").write_text("unchanged\n")
        self.git("add", ".")
        self.commit("fixture selected")
        self.old = self.git("rev-parse", "HEAD")
        self.git("init", "--bare", "-q", str(self.remote))
        self.git("remote", "add", "origin", str(self.remote))
        self.git("-c", "push.followTags=false", "push", "--no-follow-tags",
                 "origin", "HEAD:refs/heads/selected")
        self.git("checkout", "--detach", "-q")

    def git(self, *args, input=None):
        return subprocess.check_output(
            [REAL_GIT, *args], cwd=self.root, env=self.env, input=input,
            text=True, stderr=subprocess.PIPE).strip()

    def commit(self, message):
        return self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "-q", "-m", f"{message}\n\n{TRAILER}")

    def helper(self, check=True):
        result = subprocess.run(
            [sys.executable, ".github/scripts/bump.py", "--mode", "patch",
             "--sha", self.old, "--ref", "refs/heads/selected"],
            cwd=self.root, env=self.env, text=True, capture_output=True)
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def remote_sha(self):
        return self.git("--git-dir=" + str(self.remote), "rev-parse", "refs/heads/selected")

    def test_detached_no_identity_bot_single_parent_nonempty_no_annotated_tags(self):
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "tag", "-a", "fixture-annotated", "-m", "must remain local")
        self.git("config", "push.followTags", "true")
        original = version.read(self.root)[0]
        self.helper()
        new = self.git("rev-parse", "HEAD")
        self.assertEqual(self.remote_sha(), new)
        self.assertEqual(self.git("rev-list", "--parents", "-n", "1", new), f"{new} {self.old}")
        changed = self.git("diff", "--name-only", self.old, new).splitlines()
        self.assertTrue(changed)
        self.assertLessEqual(set(changed), {str(p) for p in version.SOURCE_PATHS})
        self.assertNotEqual(version.read(self.root)[0], original)
        self.assertIn(TRAILER, self.git("log", "-1", "--format=%B"))
        self.assertEqual(self.git("log", "-1", "--format=%an <%ae>"),
                         "github-actions[bot] <github-actions[bot]@users.noreply.github.com>")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.git("ls-remote", "--tags", "origin"), "")

    def test_missing_executable_guard_fails_before_edits(self):
        target = self.root / ".github/hooks/pre-push"
        target.chmod(0o644)
        before = {p: (self.root / p).read_bytes() for p in version.SOURCE_PATHS}
        self.helper(False)
        self.assertEqual(before, {p: (self.root / p).read_bytes() for p in version.SOURCE_PATHS})
        self.assertEqual(self.git("rev-parse", "HEAD"), self.old)

    def test_remote_moved_before_edit_is_rejected(self):
        self.git("--git-dir=" + str(self.remote), "update-ref", "refs/heads/selected", self.previous)
        self.helper(False)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.old)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_hidden_index_flags_fail_before_edits(self):
        for flag in ("--assume-unchanged", "--skip-worktree"):
            with self.subTest(flag=flag):
                self.git("update-index", flag, str(version.PROJECT))
                before = (self.root / version.PROJECT).read_bytes()
                (self.root / version.PROJECT).write_bytes(before + b"\n")
                result = self.helper(False)
                self.assertIn("hidden index flags", result.stderr)
                self.assertEqual(self.git("rev-parse", "HEAD"), self.old)
                (self.root / version.PROJECT).write_bytes(before)
                self.git("update-index", "--no-assume-unchanged", str(version.PROJECT))
                self.git("update-index", "--no-skip-worktree", str(version.PROJECT))

    def test_guard_rechecked_after_generated_commit_before_push(self):
        self.git("config", "core.fileMode", "false")
        wrapper_dir = self.base / "bin"
        wrapper_dir.mkdir()
        wrapper = wrapper_dir / "git"
        wrapper.write_text(
            "#!" + sys.executable + "\n"
            "import os,subprocess,sys\n"
            f"git={REAL_GIT!r}\n"
            "args=sys.argv[1:]\n"
            "if args[:4]==['ls-files','-z','--others','--ignored']:\n"
            "    head=subprocess.check_output([git,'rev-parse','HEAD'],text=True).strip()\n"
            f"    if head!={self.old!r}: os.chmod('.github/hooks/pre-push',0o644)\n"
            "os.execv(git,[git,*args])\n")
        wrapper.chmod(0o755)
        self.env["PATH"] = str(wrapper_dir) + os.pathsep + os.environ["PATH"]
        result = self.helper(False)
        self.assertIn("not executable", result.stderr)
        self.assertEqual(self.remote_sha(), self.old)

    def test_nonforce_push_protects_race_after_valid_advertisement(self):
        hook = self.root / ".github/hooks/pre-push"
        hook.write_text(hook.read_text() +
                        f"\nsubprocess.check_call([{REAL_GIT!r},"
                        f"'--git-dir={self.remote}', 'update-ref',"
                        f"'refs/heads/selected', {self.previous!r}])\n")
        self.git("add", ".github/hooks/pre-push")
        self.commit("fixture late-race hook")
        self.old = self.git("rev-parse", "HEAD")
        self.git("-c", "push.followTags=false", "push", "--no-follow-tags",
                 "origin", "HEAD:refs/heads/selected")
        result = self.helper(False)
        self.assertIn("remote rejected", result.stderr)
        self.assertEqual(self.remote_sha(), self.previous)

    def install_race(self, mode):
        wrapper_dir = self.base / "bin"
        wrapper_dir.mkdir()
        wrapper = wrapper_dir / "git"
        wrapper.write_text(
            "#!" + sys.executable + "\n"
            "import os,subprocess,sys\n"
            f"git={REAL_GIT!r}\n"
            "args=sys.argv[1:]\n"
            "if 'push' in args and any(a.startswith('HEAD:') for a in args):\n"
            f"    mode={mode!r}\n"
            f"    bare={str(self.remote)!r}\n"
            "    ref='refs/heads/selected'\n"
            "    cmd=[git,'--git-dir='+bare,'update-ref']\n"
            "    if mode=='delete':\n"
            "        subprocess.check_call(cmd+['-d',ref])\n"
            "    else:\n"
            "        sha=subprocess.check_output([git,'rev-parse','HEAD'],text=True).strip()\n"
            f"        if mode=='rewind': sha={self.previous!r}\n"
            "        if mode=='advance':\n"
            "            env=dict(os.environ,GIT_AUTHOR_NAME='Fixture',"
            "GIT_AUTHOR_EMAIL='fixture@example.invalid',GIT_COMMITTER_NAME='Fixture',"
            "GIT_COMMITTER_EMAIL='fixture@example.invalid')\n"
            "            tree=subprocess.check_output([git,'rev-parse','HEAD^{tree}'],text=True).strip()\n"
            f"            sha=subprocess.check_output([git,'commit-tree',tree,'-p',{self.old!r}],"
            f"input={'fixture race' + chr(10) + chr(10) + TRAILER + chr(10)!r},text=True,env=env).strip()\n"
            "        subprocess.check_call([git,'--git-dir='+bare,'fetch','--no-tags',os.getcwd(),sha])\n"
            "        subprocess.check_call(cmd+[ref,sha])\n"
            "os.execv(git,[git,*args])\n"
        )
        wrapper.chmod(0o755)
        self.env["PATH"] = str(wrapper_dir) + os.pathsep + os.environ["PATH"]

    def test_real_bare_races_advance_rewind_delete_and_same_result(self):
        for mode in ("advance", "rewind", "delete", "same-result"):
            with self.subTest(mode=mode):
                # Each run gets a genuinely clean detached fixture.
                other = Bump()
                other.setUp()
                try:
                    other.install_race(mode)
                    result = other.helper(False)
                    self.assertIn("Source bump push rejected", result.stderr)
                    self.assertEqual(other.git("ls-remote", "--tags", "origin"), "")
                    if mode == "same-result":
                        self.assertEqual(other.remote_sha(), other.git("rev-parse", "HEAD"))
                finally:
                    other.doCleanups()

    def test_guard_rejects_zero_multiple_and_nonliteral_head_updates(self):
        self.helper()
        new = self.git("rev-parse", "HEAD")
        env = dict(self.env, BUMP_OLD=self.old, BUMP_NEW=new,
                   BUMP_REF="refs/heads/selected")
        valid = f"HEAD {new} refs/heads/selected {self.old}\n"
        for advertisement in ("", valid + valid, valid.replace("HEAD", "refs/heads/selected"),
                              valid.replace(self.old, new)):
            result = subprocess.run(
                [str(self.root / ".github/hooks/pre-push"), "origin", str(self.remote)],
                cwd=self.root, env=env, input=advertisement, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0, advertisement)


if __name__ == "__main__":
    unittest.main()
