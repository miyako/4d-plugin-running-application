#!/usr/bin/env python3
"""Fail-closed, create-only publication of the selected immutable source commit."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from urllib.parse import quote

sys.dont_write_bytecode = True
import version


BUNDLE = Path("test/Plugins/Running Application.bundle")
GENERATED = {
    str(BUNDLE / "Contents" / path)
    for path in (
        "Info.plist",
        "MacOS/Running Application",
        "Resources/English.lproj/InfoPlist.strings",
        "_CodeSignature/CodeResources",
    )
}
INTERMEDIATES = (
    "build/Running Application.build/Default/Running Application.build/",
    "build/XCBuildData/",
)
SOURCE_CONFIG_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".m", ".mm",
    ".py", ".sh", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".config",
    ".xcconfig", ".pbxproj", ".vcxproj", ".strings", ".xlf",
}


class ReleaseError(RuntimeError):
    pass


def run(args, root, data=None):
    result = subprocess.run(args, cwd=root, input=data, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    if result.returncode:
        raise ReleaseError(
            f"{args[0]} {args[1]} failed ({result.returncode}): "
            f"{result.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return result.stdout


def git(root, *args):
    return run(["git", *args], root)


def workspace(root, sha, generated=False, artifacts=()):
    """Compare index objects and unfiltered on-disk bytes, not Git's stat cache."""
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha):
        raise ReleaseError("Selected SHA must be a full lowercase commit object ID")
    if git(root, "rev-parse", "--verify", "HEAD").decode().strip() != sha:
        raise ReleaseError("HEAD differs from selected immutable SHA")
    if git(root, "cat-file", "-t", sha).strip() != b"commit":
        raise ReleaseError("Selected SHA is not a commit")
    tree = {}
    for entry in git(root, "ls-tree", "-rz", "--full-tree", sha).split(b"\0"):
        if not entry:
            continue
        meta, raw_path = entry.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        path = os.fsdecode(raw_path)
        if kind != "blob" or mode not in ("100644", "100755", "120000"):
            raise ReleaseError(f"Unsupported immutable tree entry: {path}")
        tree[path] = (mode, oid)
    index = {}
    for entry in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not entry:
            continue
        meta, raw_path = entry.split(b"\t", 1)
        mode, oid, stage = meta.decode().split()
        path = os.fsdecode(raw_path)
        if stage != "0" or path in index:
            raise ReleaseError(f"Unmerged index: {path}")
        index[path] = (mode, oid)
    if tree.keys() != index.keys():
        raise ReleaseError("Index adds or removes immutable tracked paths")
    algorithm = git(root, "rev-parse", "--show-object-format").decode().strip()
    allowed = GENERATED if generated else set()
    for path, expected in tree.items():
        if path in allowed:
            continue
        if index[path] != expected:
            raise ReleaseError(f"Staged immutable input differs from selected tree: {path}")
        disk = root / path
        for parent in disk.relative_to(root).parents:
            if (root / parent).is_symlink():
                raise ReleaseError(f"Symlink directory in immutable input: {path}")
        try:
            info = disk.lstat()
            if stat.S_ISLNK(info.st_mode):
                mode, raw = "120000", os.fsencode(os.readlink(disk))
            elif stat.S_ISREG(info.st_mode):
                mode = "100755" if info.st_mode & 0o111 else "100644"
                raw = disk.read_bytes()
            else:
                raise ReleaseError(f"Non-file immutable input: {path}")
        except FileNotFoundError as exc:
            raise ReleaseError(f"Missing immutable input: {path}") from exc
        digest = hashlib.new(algorithm, b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if (mode, digest) != expected:
            raise ReleaseError(f"Working immutable input differs from selected tree: {path}")
    extra = set()
    for flags in (("--others", "--exclude-standard"),
                  ("--others", "--ignored", "--exclude-standard")):
        extra.update(os.fsdecode(path) for path in
                     git(root, "ls-files", "-z", *flags).split(b"\0") if path)
    for path in extra:
        permitted = generated and (
            path in artifacts or (
                any(path.startswith(prefix) for prefix in INTERMEDIATES)
                and Path(path).suffix.casefold() not in SOURCE_CONFIG_SUFFIXES
            )
        )
        if not permitted:
            raise ReleaseError(f"Unexpected untracked or ignored input: {path}")
        candidate = root / path
        if candidate.is_symlink() or any(
            (root / parent).is_symlink() for parent in Path(path).parents
        ):
            raise ReleaseError(f"Symlink output/intermediate: {path}")


def integer(value):
    return type(value) is int and value > 0


def json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError(f"Non-JSON constant: {value}")


def validate_built(root):
    for path in GENERATED:
        candidate = root / path
        if (candidate.is_symlink() or not candidate.is_file()
                or any((root / parent).is_symlink() for parent in Path(path).parents)):
            raise ReleaseError(f"Expected regular generated bundle output: {path}")
    version.validate_built(root / BUNDLE, root)


class GitHub:
    def __init__(self, root):
        self.root = root
        self.repo = os.environ.get("GITHUB_REPOSITORY", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repo):
            raise ReleaseError("GITHUB_REPOSITORY must explicitly identify owner/repository")
        self.release_ids = set()

    def authenticate(self):
        run(["gh", "auth", "status", "--hostname", "github.com"], self.root)

    def api(self, method, endpoint, payload=None, upload=None):
        args = ["gh", "api", "--hostname", "github.com", "--method", method, endpoint]
        data = None
        if payload is not None:
            args += ["--input", "-"]
            data = json.dumps(payload).encode()
        if upload is not None:
            args += ["--header", "Content-Type: application/octet-stream", "--input", str(upload)]
        raw = run(args, self.root, data)
        try:
            return json.loads(raw, object_pairs_hook=json_object,
                              parse_constant=invalid_constant)
        except (ValueError, UnicodeError) as exc:
            raise ReleaseError("GitHub returned malformed JSON") from exc

    def available(self, tag):
        seen = set()
        page = 1
        while True:
            releases = self.api(
                "GET", f"repos/{self.repo}/releases?per_page=100&page={page}"
            )
            if not isinstance(releases, list) or len(releases) > 100:
                raise ReleaseError("Malformed release enumeration page")
            for release in releases:
                if (not isinstance(release, dict) or not integer(release.get("id"))
                        or not isinstance(release.get("tag_name"), str)
                        or not release["tag_name"]
                        or type(release.get("draft")) is not bool
                        or type(release.get("prerelease")) is not bool
                        or release["id"] in seen):
                    raise ReleaseError("Malformed or unstable release enumeration")
                seen.add(release["id"])
                if release["tag_name"] == tag:
                    raise ReleaseError(f"Release already exists (including drafts): {tag}")
            if not releases:
                break
            page += 1
        self.release_ids = seen
        refs = self.api("GET", f"repos/{self.repo}/git/matching-refs/tags/{quote(tag, safe='')}")
        if not isinstance(refs, list):
            raise ReleaseError("Malformed matching-refs response")
        seen_refs = set()
        for ref in refs:
            if (not isinstance(ref, dict) or not isinstance(ref.get("ref"), str)
                    or not ref["ref"].startswith(f"refs/tags/{tag}")
                    or ref["ref"] in seen_refs
                    or not isinstance(ref.get("object"), dict)
                    or ref["object"].get("type") not in ("commit", "tag")
                    or not isinstance(ref["object"].get("sha"), str)
                    or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", ref["object"]["sha"])):
                raise ReleaseError("Malformed matching-refs entry")
            seen_refs.add(ref["ref"])
            if ref["ref"] == f"refs/tags/{tag}":
                raise ReleaseError(f"GitHub tag already exists: {tag}")
        if git(self.root, "ls-remote", "--refs", "origin", f"refs/tags/{tag}").strip():
            raise ReleaseError(f"Remote tag already exists: {tag}")

    def publish(self, sha, tag, assets):
        ref = f"refs/tags/{tag}"
        # A failed mutation may still have reached the server. Never roll it back or retry.
        try:
            created = self.api("POST", f"repos/{self.repo}/git/refs",
                               {"ref": ref, "sha": sha})
            if (not isinstance(created, dict) or created.get("ref") != ref
                    or not isinstance(created.get("object"), dict)
                    or created["object"].get("sha") != sha
                    or created["object"].get("type") != "commit"):
                raise ReleaseError("Malformed created tag response")
            fetched = self.api("GET", f"repos/{self.repo}/git/ref/tags/{quote(tag, safe='')}")
            if (not isinstance(fetched, dict) or fetched.get("ref") != ref
                    or not isinstance(fetched.get("object"), dict)
                    or fetched["object"].get("sha") != sha
                    or fetched["object"].get("type") != "commit"):
                raise ReleaseError("Fresh GitHub tag does not match selected SHA")
            remote = git(self.root, "ls-remote", "--refs", "origin", ref).decode().splitlines()
            if len(remote) != 1 or remote[0].split() != [sha, ref]:
                raise ReleaseError("Created remote tag does not match selected SHA")
            release = self.api("POST", f"repos/{self.repo}/releases", {
                "tag_name": tag,
                "name": tag,
                "target_commitish": sha,
                "draft": False,
                "prerelease": False,
                "body": "",
                "generate_release_notes": False,
            })
            if (not isinstance(release, dict) or not integer(release.get("id"))
                    or release["id"] in self.release_ids
                    or release.get("tag_name") != tag or release.get("name") != tag
                    or release.get("target_commitish") != sha
                    or release.get("draft") is not False
                    or release.get("prerelease") is not False
                    or release.get("body") != ""):
                raise ReleaseError("Malformed created release response")
            release_id = release["id"]
            upload_base = f"https://uploads.github.com/repos/{self.repo}/releases/{release_id}/assets"
            if release.get("upload_url") != upload_base + "{?name,label}":
                raise ReleaseError("Created release upload URL does not match new release ID")
            uploaded_ids = set()
            for asset in assets:
                name = asset.name.replace(" ", ".")
                response = self.api(
                    "POST", f"{upload_base}?name={quote(name, safe='')}", upload=asset,
                )
                if (not isinstance(response, dict) or not integer(response.get("id"))
                        or response["id"] in uploaded_ids
                        or response.get("name") != name
                        or response.get("state") != "uploaded"
                        or type(response.get("size")) is not int
                        or response["size"] != asset.stat().st_size):
                    raise ReleaseError(f"Malformed or incomplete asset upload: {asset.name}")
                uploaded_ids.add(response["id"])
        except (ReleaseError, OSError) as exc:
            raise ReleaseError(
                f"{exc}. Partial tag/release/assets may be retained; manual recovery required. "
                "No deletion, update, rollback, reuse, or retry was attempted."
            ) from exc


def execute(args, root=Path(".")):
    root = root.resolve()
    current = version.read(root)[0]
    workspace(root, args.sha, generated=args.command == "publish",
              artifacts=[f"Running Application-{current}.{extension}"
                         for extension in ("zip", "dmg")])
    tag = "v" + current
    assets = []
    if args.command == "publish":
        for extension, value in (("zip", args.zip), ("dmg", args.dmg)):
            expected = root / f"Running Application-{current}.{extension}"
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = root / candidate
            if candidate != expected or candidate.is_symlink() or not candidate.is_file():
                raise ReleaseError(f"Expected regular versioned artifact: {expected.name}")
            if candidate.stat().st_size <= 0:
                raise ReleaseError(f"Empty artifact: {expected.name}")
            if candidate.stat().st_nlink != 1:
                raise ReleaseError(f"Aliased artifact: {expected.name}")
            assets.append(candidate)
        workspace(root, args.sha, generated=True, artifacts=[p.name for p in assets])
        validate_built(root)
    if args.command == "preflight" and not os.environ.get("GITHUB_OUTPUT"):
        raise ReleaseError("GITHUB_OUTPUT is required")
    github = GitHub(root)
    github.authenticate()
    github.available(tag)
    workspace(root, args.sha, generated=args.command == "publish",
              artifacts=[p.name for p in assets])
    if args.command == "preflight":
        output = os.environ.get("GITHUB_OUTPUT")
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"version={current}\ntag={tag}\n")
    else:
        validate_built(root)
        github.publish(args.sha, tag, assets)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("preflight", "publish"):
        sub = commands.add_parser(command)
        sub.add_argument("--sha", required=True)
        if command == "publish":
            sub.add_argument("--zip", required=True)
            sub.add_argument("--dmg", required=True)
    try:
        execute(parser.parse_args())
    except (ReleaseError, ValueError, OSError) as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
