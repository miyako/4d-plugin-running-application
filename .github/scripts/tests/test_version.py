from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parents[1]
sys.path.insert(0, str(SCRIPTS))
import version as v

INTAKE = "fbc46b8e8d3c2ab8c412bea637172643bc6b4a3c"


class Versions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for path in v.SOURCE_PATHS:
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / path, target)
        self.current = v.read(self.root)[0]

    def snapshot(self):
        return {p: (self.root / p).read_bytes() for p in v.SOURCE_PATHS}

    def change(self, path, transform):
        target = self.root / path
        target.write_bytes(transform(target.read_bytes()))

    def fails_without_writes(self):
        before = self.snapshot()
        with self.assertRaises((ValueError, KeyError)):
            v.read(self.root)
        for mode in ("patch", "minor", "major"):
            with self.assertRaises((ValueError, KeyError)):
                v.bump(mode, self.root)
            self.assertEqual(self.snapshot(), before)

    def test_intake_history_coordinated_header_and_stale_sources(self):
        original = subprocess.check_output(
            ["git", "show", f"{INTAKE}:.github/workflows/bump-version.yml"], cwd=ROOT
        ).decode()
        self.assertIn("MARKETING_VERSION/CURRENT_PROJECT_VERSION AND Info.plist", original)
        self.assertIn('KEY="MARKETING_VERSION"', original)
        project = subprocess.check_output(["git", "show", f"{INTAKE}:{v.PROJECT}"], cwd=ROOT)
        self.assertIn(b"MARKETING_VERSION = 2.1.6;", project)
        self.assertIn(b"CURRENT_PROJECT_VERSION = 2.1.2;", project)
        expected = project.replace(b"MARKETING_VERSION = 2.1.6;",
                                   f"MARKETING_VERSION = {self.current};".encode())
        expected = expected.replace(b"CURRENT_PROJECT_VERSION = 2.1.2;",
                                    f"CURRENT_PROJECT_VERSION = {self.current};".encode())
        self.assertEqual((self.root / v.PROJECT).read_bytes(), expected)
        source = subprocess.check_output(["git", "show", f"{INTAKE}:{v.LOCALIZATIONS[0]}"], cwd=ROOT)
        self.assertEqual(source.count(b"2.1.2"), 2)
        self.assertNotIn(b"\n", source)
        self.assertEqual((self.root / v.LOCALIZATIONS[0]).read_bytes(),
                         source.replace(b"2.1.2", self.current.encode()))
        original_plist = subprocess.check_output(["git", "show", f"{INTAKE}:{v.PLIST}"], cwd=ROOT)
        self.assertEqual((self.root / v.PLIST).read_bytes(), original_plist)

    def test_modes_source_derived_preserve_optional_build_and_modes(self):
        major, minor, patch = map(int, self.current.split("."))
        for mode, expected in (("patch", f"{major}.{minor}.{patch+1}"),
                               ("minor", f"{major}.{minor+1}.0"),
                               ("major", f"{major+1}.0.0")):
            with self.subTest(mode=mode):
                before = self.snapshot()
                modes = {p: (self.root / p).stat().st_mode for p in v.SOURCE_PATHS}
                self.assertEqual(v.bump(mode, self.root), expected)
                self.assertEqual(v.read(self.root)[0], expected)
                self.assertNotIn(b"CFBundleVersion", (self.root / v.PLIST).read_bytes())
                self.assertEqual((self.root / v.PLIST).read_bytes(), before[v.PLIST])
                for path in v.SOURCE_PATHS:
                    self.assertEqual((self.root / path).stat().st_mode, modes[path])
                    (self.root / path).write_bytes(before[path])

    def test_encodings_and_individual_mixed_newlines(self):
        for encoding, bom in (("utf-8", b""), ("utf-8", b"\xef\xbb\xbf"),
                              ("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff"),
                              ("utf-16-le", b""), ("utf-16-be", b"")):
            for mode in ("patch", "minor", "major"):
                with self.subTest(encoding=encoding, bom=bom, mode=mode):
                    saved = self.snapshot()
                    for path, raw in saved.items():
                        text = raw.decode("utf-8")
                        # Keep every individual newline sequence, including bare CR.
                        text = text.replace("\n", "\r\n", 1) + "\r\n/* tail */\r"
                        if path == v.PLIST:
                            text = raw.decode().replace("\n", "\r\n", 1)
                        (self.root / path).write_bytes(bom + text.encode(encoding))
                    before = self.snapshot()
                    expected = v.bump(mode, self.root)
                    for path in v.SOURCE_PATHS:
                        after = (self.root / path).read_bytes()
                        self.assertEqual(after, before[path].replace(
                            self.current.encode(encoding), expected.encode(encoding)))
                        (self.root / path).write_bytes(saved[path])

    def test_short_absent_alone_and_both_absent_zero_writes(self):
        raw = (self.root / v.PLIST).read_bytes()
        for build in (False, True):
            data = plistlib.loads(raw)
            del data["CFBundleShortVersionString"]
            if build:
                data["CFBundleVersion"] = self.current
            (self.root / v.PLIST).write_bytes(plistlib.dumps(data))
            self.fails_without_writes()

    def test_plist_build_optional_literal_and_supported_reference(self):
        for value in (self.current, "$(CURRENT_PROJECT_VERSION)", "$(MARKETING_VERSION)",
                      "${CURRENT_PROJECT_VERSION}", "${MARKETING_VERSION}"):
            data = plistlib.loads((self.root / v.PLIST).read_bytes())
            data["CFBundleVersion"] = value
            (self.root / v.PLIST).write_bytes(plistlib.dumps(data))
            self.assertEqual(v.read(self.root)[0], self.current)
            new = v.bump("patch", self.root)
            result = plistlib.loads((self.root / v.PLIST).read_bytes())
            self.assertEqual(result["CFBundleVersion"], value if value.startswith("$") else new)
            for path in v.SOURCE_PATHS:
                shutil.copy2(ROOT / path, self.root / path)

    def test_invalid_leading_zero_suffix_drift_unknown_and_cycle(self):
        original = self.snapshot()
        for bad in ("01.2.3", "1.02.3", "1.2.03", "1.2.3-beta", "1.2",
                    "$(UNKNOWN_VERSION)", "$(MARKETING_VERSION)"):
            with self.subTest(bad=bad):
                self.change(v.PROJECT, lambda raw: raw.replace(
                    f"MARKETING_VERSION = {self.current};".encode(),
                    f'MARKETING_VERSION = "{bad}";'.encode()))
                self.fails_without_writes()
                for path, raw in original.items():
                    (self.root / path).write_bytes(raw)
        self.change(v.PROJECT, lambda raw: raw.replace(
            f"CURRENT_PROJECT_VERSION = {self.current};".encode(),
            b"CURRENT_PROJECT_VERSION = 999.0.0;", 1))
        self.fails_without_writes()

    def test_inline_quoted_duplicates_after_other_assignment(self):
        for duplicate in (f"MARKETING_VERSION = {self.current};",
                          f'"MARKETING_VERSION" = "{self.current}";'):
            saved = self.snapshot()
            self.change(v.PROJECT, lambda raw: raw.replace(
                f"MARKETING_VERSION = {self.current};".encode(),
                f'OTHER = "noise"; MARKETING_VERSION = {self.current}; {duplicate}'.encode(), 1))
            self.fails_without_writes()
            for path, raw in saved.items():
                (self.root / path).write_bytes(raw)

    def test_comments_string_noise_and_inline_real_assignments(self):
        self.change(v.PROJECT, lambda raw: raw.replace(
            f"MARKETING_VERSION = {self.current};".encode(),
            (f'// MARKETING_VERSION = 99.0.0;\n'
             f'/* CURRENT_PROJECT_VERSION = 99.0.0; */ '
             f'OTHER = "MARKETING_VERSION = 99.0.0;"; '
             f'"MARKETING_VERSION" = "{self.current}";').encode(), 1))
        new = v.bump("patch", self.root)
        raw = (self.root / v.PROJECT).read_bytes()
        self.assertIn(f'"MARKETING_VERSION" = "{new}";'.encode(), raw)
        self.assertEqual(raw.count(b"99.0.0"), 3)

    def test_source_plist_duplicate_and_localized_duplicates(self):
        saved = self.snapshot()
        self.change(v.PLIST, lambda raw: raw.replace(
            b"</dict>", b"<key>CFBundleShortVersionString</key><string>1.2.3</string></dict>"))
        self.fails_without_writes()
        for path, raw in saved.items():
            (self.root / path).write_bytes(raw)
        self.change(v.LOCALIZATIONS[0], lambda raw: raw + (
            f' OTHER = "x"; "CFBundleShortVersionString" = "{self.current}";').encode())
        self.fails_without_writes()

    def test_project_reference_resolution_preserves_known_refs(self):
        saved = self.snapshot()
        for key, reference in (("MARKETING_VERSION", "CURRENT_PROJECT_VERSION"),
                               ("CURRENT_PROJECT_VERSION", "MARKETING_VERSION")):
            for alias in (f"$({reference})", "${" + reference + "}"):
                for quoted in (True, False):
                    with self.subTest(key=key, quoted=quoted, alias=alias):
                        encoded = f'"{alias}"' if quoted else alias
                        self.change(v.PROJECT, lambda raw: raw.replace(
                            f"{key} = {self.current};".encode(),
                            f"{key} = {encoded};".encode()))
                        v.bump("patch", self.root)
                        self.assertIn(f"{key} = {encoded};".encode(),
                                      (self.root / v.PROJECT).read_bytes())
                        for path, raw in saved.items():
                            (self.root / path).write_bytes(raw)
        self.change(v.PROJECT, lambda raw: raw.replace(
            f"MARKETING_VERSION = {self.current};".encode(),
            b'MARKETING_VERSION = "$(CURRENT_PROJECT_VERSION)";').replace(
            f"CURRENT_PROJECT_VERSION = {self.current};".encode(),
            b'CURRENT_PROJECT_VERSION = "$(MARKETING_VERSION)";'))
        self.fails_without_writes()

    def test_missing_key_in_one_configuration_fails_without_writes(self):
        self.change(v.PROJECT, lambda raw: raw.replace(
            f"CURRENT_PROJECT_VERSION = {self.current};".encode(), b"", 1))
        self.fails_without_writes()

    def test_render_validation_precedes_all_writes(self):
        saved = self.snapshot()
        # A scalar supported by plistlib but not by our byte-preserving XML renderer.
        data = plistlib.loads(saved[v.PLIST])
        data["CFBundleVersion"] = self.current
        raw = plistlib.dumps(data).replace(
            f"<string>{self.current}</string>".encode(),
            f"<string >{self.current}</string>".encode())
        (self.root / v.PLIST).write_bytes(raw)
        before = self.snapshot()
        with self.assertRaises(ValueError):
            v.bump("patch", self.root)
        self.assertEqual(before, self.snapshot())

    def test_built_xml_binary_plist_and_localization(self):
        bundle = self.root / "output.bundle"
        resources = bundle / "Contents/Resources/English.lproj"
        resources.mkdir(parents=True)
        for fmt in (plistlib.FMT_XML, plistlib.FMT_BINARY):
            data = plistlib.loads((self.root / v.PLIST).read_bytes())
            data["CFBundleShortVersionString"] = self.current
            (bundle / "Contents/Info.plist").write_bytes(plistlib.dumps(data, fmt=fmt))
            source = (self.root / v.LOCALIZATIONS[0]).read_bytes()
            (resources / "InfoPlist.strings").write_bytes(source)
            v.validate_built(bundle, self.root)
            data["CFBundleShortVersionString"] = "999.0.0"
            (bundle / "Contents/Info.plist").write_bytes(plistlib.dumps(data, fmt=fmt))
            with self.assertRaises(ValueError):
                v.validate_built(bundle, self.root)
            data["CFBundleShortVersionString"] = self.current
            (bundle / "Contents/Info.plist").write_bytes(plistlib.dumps(data, fmt=fmt))
            (resources / "InfoPlist.strings").write_bytes(source.replace(
                self.current.encode(), b"999.0.0"))
            with self.assertRaises(ValueError):
                v.validate_built(bundle, self.root)
            (resources / "InfoPlist.strings").write_bytes(plistlib.dumps({
                "CFBundleShortVersionString": self.current,
                "CFBundleGetInfoString": f"Running Application version {self.current}, Copyright",
            }, fmt=fmt))
            v.validate_built(bundle, self.root)


if __name__ == "__main__":
    unittest.main()
