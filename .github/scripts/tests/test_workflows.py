from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[3]
INTAKE = "fbc46b8e8d3c2ab8c412bea637172643bc6b4a3c"


class Workflows(unittest.TestCase):
    def test_exact_manual_controls_and_immutable_checkout(self):
        workflows = {path.name: path.read_text() for path in
                     (ROOT / ".github/workflows").glob("*.yml")}
        self.assertLessEqual({"bump-version.yml", "release.yml"}, set(workflows))
        bump, build = workflows["bump-version.yml"], workflows["release.yml"]
        self.assertRegex(bump, r"(?m)^name: Bump$")
        self.assertRegex(build, r"(?m)^name: Build$")
        self.assertRegex(build, r"(?m)^on:\n  workflow_dispatch:\s*\n")
        self.assertNotIn("inputs:", build)
        self.assertNotRegex(build + bump, r"(?m)^  (push|pull_request|workflow_call|schedule):")
        self.assertEqual(re.findall(r"(?m)^      (\w+):$", bump), ["mode"])
        for token in ("type: choice", "required: true", "default: patch",
                      "- patch", "- minor", "- major"):
            self.assertIn(token, bump)
        for text in (bump, build):
            checkouts = list(re.finditer(r"uses: actions/checkout@v4", text))
            self.assertEqual(len(checkouts), 1)
            self.assertIn("ref: ${{ github.sha }}", text)
        self.assertNotIn("inputs.tag", build)
        self.assertNotIn("github.ref_name", build)

    def test_original_triggers_and_actual_topology_immutable(self):
        original = subprocess.check_output(
            ["git", "show", f"{INTAKE}:.github/workflows/release.yml"], cwd=ROOT).decode()
        self.assertIn("name: Build & Release", original)
        self.assertRegex(original, r"(?m)^  push:\n    tags:\n      - 'v\*\.\*\.\*'")
        self.assertIn("Existing tag to build", original)
        self.assertNotIn("pull_request:", original)
        self.assertNotIn("branches:", original)
        jobs = original.split("\njobs:\n", 1)[1]
        self.assertEqual(re.findall(r"(?m)^  ([a-z-]+):$", jobs), ["version", "build-macos"])
        self.assertNotIn("windows-latest", jobs)
        self.assertNotIn("body:", original)
        self.assertNotIn("generate_release_notes:", original)


if __name__ == "__main__":
    unittest.main()
