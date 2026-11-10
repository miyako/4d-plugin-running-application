#!/usr/bin/env python3
"""Unsigned, offline actual-source/full-SDK sanitizer regression."""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "53da23011c26c054e6d1379901b2e6819e7554ec"
SDK_BLOBS = {
    "4DPluginAPI.c": "62f745e91d81ef2c3c901c656ea8e175d3b173e8",
    "4DPluginAPI.h": "528bf1d2552df08d6a8b1bc95011a9b72934ad5c",
    "4DPluginAPI.def": "5bbdb82fb724b6f8e307feb01d9f158b802de89d",
    "EntryPoints.h": "da428d5f6ca23b0a30371c7288a01d29aa3c466a",
    "Flags.h": "7a5a35539f27430a3fd9cb711d6b789051316471",
    "PrivateTypes.h": "a3098cb662cdbc43ba574fdef72a059c4c6d1cd4",
    "PublicTypes.h": "9ae20b001bff9128f690fff3e6c3f873b6238333",
}
CASES = (
    "scalar-setters", "picture-setter", "property-getters",
    "property-mismatches", "property-errors", "stringify-errors",
    "stringify-success", "arrays", "text", "dispatcher", "blob",
)


def run(command, **kwargs):
    return subprocess.run(command, check=True, text=True, **kwargs)


def build(source, work):
    for name, expected in SDK_BLOBS.items():
        data = (source / "4D Plugin API" / name).read_bytes()
        actual = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
        if actual != expected:
            raise RuntimeError(f"Bundled SDK blob changed: {name}: {actual}")
    common = [
        "clang++", "-std=c++17", "-g", "-O1", "-fsanitize=address,undefined",
        "-fno-sanitize-recover=all", "-fno-omit-frame-pointer",
        "-Wno-deprecated-declarations", "-Wno-multichar",
        "-I" + str(source / "4D Plugin API"),
        "-I" + str(source / "4D Plugin API/Classes"), "-I" + str(source),
        "-include", str(ROOT / ".github/tests/offline_platform.h"),
        "-x", "objective-c++",
    ]
    sources = [
        source / "4D Plugin API/4DPluginAPI.c",
        *sorted((source / "4D Plugin API/Classes").glob("*.cpp")),
        source / "support/4DPlugin-JSON.cpp",
        source / "4DPlugin-Running-Application.cpp",
        ROOT / ".github/tests/ownership_host.mm",
    ]
    objects = []
    for index, path in enumerate(sources):
        obj = work / f"{index}.o"
        run(common + ["-c", str(path), "-o", str(obj)])
        objects.append(str(obj))
    imports = run(["nm", "-u", *objects], capture_output=True).stdout
    forbidden = (
        "_OBJC_CLASS_$_NSRunningApplication", "_OBJC_CLASS_$_NSWorkspace",
        "_OBJC_CLASS_$_NSBundle", "_OBJC_CLASS_$_NSImage",
        "_CFURLCreateWithFileSystemPath", "_CFURLCopyFileSystemPath",
        "_CGImageDestination", "_system", "_popen", "_exec", "_posix_spawn",
        "_kill", "_proc_", "_sysctl", "_NSCreateObjectFileImage",
    )
    for symbol in forbidden:
        if any(line.strip().startswith(symbol) for line in imports.splitlines()):
            raise RuntimeError(f"Forbidden platform import: {symbol}")
    binary = work / "ownership"
    run(["clang++", "-fsanitize=address,undefined", *objects,
         "-framework", "Cocoa", "-framework", "ImageIO",
         "-framework", "CoreServices", "-o", str(binary)])
    print(f"Compiled {len(sources)} actual translation units; "
          f"{len(SDK_BLOBS)} SDK blobs verified; forbidden imports absent", flush=True)
    return binary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="running-ownership-") as directory:
        work = Path(directory)
        source = ROOT
        if args.baseline:
            source = work / "baseline"
            source.mkdir()
            archive = work / "baseline.tar"
            with archive.open("wb") as output:
                subprocess.run(["git", "archive", BASELINE], cwd=ROOT,
                               stdout=output, check=True)
            run(["tar", "-xf", str(archive), "-C", str(source)])
        binary = build(source, work)
        failures = []
        for case in CASES:
            result = subprocess.run(
                [str(binary), case], text=True, capture_output=True,
                env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0",
                     "UBSAN_OPTIONS": "halt_on_error=1"},
            )
            print(f"{'FAIL' if result.returncode else 'PASS'} {case}", flush=True)
            print(result.stdout + result.stderr, end="", flush=True)
            if result.returncode:
                failures.append(case)
        print(f"RESULT {len(CASES) - len(failures)}/{len(CASES)} passed; failures={failures}")
        if args.baseline:
            expected = {"scalar-setters", "picture-setter", "property-getters",
                        "property-mismatches", "property-errors", "stringify-errors"}
            if set(failures) != expected:
                raise SystemExit("Baseline failure signature changed")
        elif failures:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
