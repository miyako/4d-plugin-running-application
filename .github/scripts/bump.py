#!/usr/bin/env python3
"""Commit and push one source-only bump, never a tag or release."""

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode = True
from version import SOURCE_PATHS, bump, read
from release import ReleaseError, workspace

TRAILER = "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"


def git(*args, env=None):
    return subprocess.check_output(["git", *args], text=True, env=env).strip()


def guard(path, expected=None):
    if not path.is_file() or path.is_symlink() or not os.access(path, os.X_OK):
        raise ValueError("Mandatory pre-push guard is missing or not executable")
    raw = path.read_bytes()
    if expected is not None and raw != expected:
        raise ValueError("Pre-push guard changed")
    return raw


def run(mode, sha, ref):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected immutable event SHA")
    if not ref.startswith("refs/heads/"):
        raise ValueError("Bump requires the selected branch, not a tag")
    git("check-ref-format", ref)
    if git("rev-parse", "HEAD") != sha:
        raise ValueError("Checkout does not match the selected event SHA")
    if git("status", "--porcelain", "--untracked-files=all", "--ignored=matching"):
        raise ValueError("Source bump requires a clean checkout")
    if any(entry and not entry.startswith("H ") for entry in git("ls-files", "-v").splitlines()):
        raise ValueError("Source bump rejects hidden index flags")
    workspace(Path(".").resolve(), sha)
    hook_dir = Path(".github/hooks").resolve()
    hook = hook_dir / "pre-push"
    original_guard = guard(hook)
    advertised = git("ls-remote", "--refs", "origin", ref).split()
    if advertised != [sha, ref]:
        raise ValueError("Selected branch has moved or is missing")
    read()
    new_version = bump(mode)
    paths = [str(path) for path in SOURCE_PATHS]
    changed = git("diff", "--name-only").splitlines()
    if not changed or not set(changed) <= set(paths):
        raise ValueError("Expected a nonempty source-only version change")
    git("add", "--", *paths)
    git("-c", "user.name=github-actions[bot]",
        "-c", "user.email=github-actions[bot]@users.noreply.github.com",
        "commit", "-m", f"chore: bump version to {new_version}\n\n{TRAILER}")
    new_sha = git("rev-parse", "HEAD")
    if git("rev-list", "--parents", "-n", "1", new_sha).split() != [new_sha, sha]:
        raise ValueError("Generated commit must have exactly the selected direct parent")
    if not git("diff", "--name-only", sha, new_sha):
        raise ValueError("Generated commit must not be empty")
    if git("status", "--porcelain", "--untracked-files=all", "--ignored=matching"):
        raise ValueError("Generated source commit left unexpected dirt")
    workspace(Path(".").resolve(), new_sha)
    guard(hook, original_guard)
    env = dict(os.environ, BUMP_OLD=sha, BUMP_NEW=new_sha, BUMP_REF=ref)
    git("-c", "push.followTags=false", "-c", f"core.hooksPath={hook_dir}",
        "push", "--no-follow-tags", "origin", f"HEAD:{ref}", env=env)
    if git("ls-remote", "--refs", "origin", ref).split() != [new_sha, ref]:
        raise ValueError("Remote branch does not match the generated commit")
    print(f"Bumped source to {new_version}: {new_sha}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("patch", "minor", "major"))
    parser.add_argument("--sha", required=True)
    parser.add_argument("--ref", required=True)
    args = parser.parse_args()
    try:
        run(args.mode, args.sha, args.ref)
    except (ValueError, OSError, ReleaseError, subprocess.CalledProcessError) as error:
        sys.exit(f"Source bump failed: {error}")
