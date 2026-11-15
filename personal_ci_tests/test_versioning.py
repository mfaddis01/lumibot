"""Offline, fail-closed guards for the separately versioned personal application."""
from fnmatch import fnmatchcase
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BOUNDARY = "a250d6b74fac9462da36636953295378d7156899"
ACTION = "googleapis/release-please-action@45996ed1f6d02564a971a2fa1b5860e934307cf7"


class PersonalVersioningTests(unittest.TestCase):
    def test_simple_strategy_isolated_from_upstream_with_exclusive_boundary(self):
        path = ROOT / "release-please-config.json"
        self.assertTrue(path.is_file(), "personal release configuration missing")
        self.assertEqual(json.loads(path.read_text()), {
            "$schema": "https://raw.githubusercontent.com/googleapis/release-please/712fcf01effd08d7b0e7b1fd3861f2cb388bc8d1/schemas/config.json",
            "bootstrap-sha": BOUNDARY,
            "packages": {
                ".": {
                    "release-type": "simple",
                    "component": "personal-app",
                    "include-component-in-tag": True,
                    "include-v-in-tag": True,
                    "tag-separator": "-",
                    "version-file": "personal_release/VERSION",
                    "changelog-path": "personal_release/CHANGELOG.md",
                    "initial-version": "0.1.0",
                    "bump-minor-pre-major": True,
                    "bump-patch-for-minor-pre-major": False,
                    "draft": False,
                    "prerelease": False,
                },
            },
        })
        manifest = json.loads((ROOT / ".release-please-manifest.json").read_text())
        version = (ROOT / "personal_release/VERSION").read_text()
        changelog = (ROOT / "personal_release/CHANGELOG.md").read_text()
        if manifest == {}:
            # Empty means no personal release exists. 0.1.0 is only the target.
            self.assertEqual(version, "0.1.0\n")
            self.assertIn("Unreleased", changelog)
            self.assertIn("not runtime qualification", changelog)
            self.assertIn("At bootstrap", changelog)
        else:
            self.assertEqual(set(manifest), {"."})
            stable = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
            self.assertRegex(version, r"\A" + stable + r"\n\Z")
            self.assertEqual(manifest["."], version.strip())
            self.assertRegex(changelog, r"(?m)^## \[?" + re.escape(version.strip()) + r"(?:\]|\s|$)")

    def test_generated_release_metadata_remains_accepted(self):
        # A real release PR updates all three metadata files. Do not freeze the
        # initial empty manifest or VERSION in a way that blocks that PR forever.
        config = (ROOT / "release-please-config.json").read_text()
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            (fixture / "release-please-config.json").write_text(config)
            (fixture / "personal_release").mkdir()
            for version in ["0.1.0", "0.2.0", "1.0.0"]:
                with self.subTest(version=version):
                    (fixture / ".release-please-manifest.json").write_text(json.dumps({".": version}))
                    (fixture / "personal_release/VERSION").write_text(version + "\n")
                    (fixture / "personal_release/CHANGELOG.md").write_text(
                        "# Personal application changelog\n\n## " + version + "\n")
                    with patch(__name__ + ".ROOT", fixture):
                        self.test_simple_strategy_isolated_from_upstream_with_exclusive_boundary()

    def test_metadata_triggers_cover_each_versioning_only_change(self):
        text = (ROOT / ".github/workflows/personal-release-checks.yml").read_text()
        for event in ["pull_request", "push"]:
            block = re.search(r"^  " + event + r":\n((?:    .*\n|\n)+)", text, re.MULTILINE)
            self.assertIsNotNone(block)
            assert block is not None
            patterns = re.findall(r'^      - "([^"\n]+)"$', block.group(1), re.MULTILINE)
            for changed in ["release-please-config.json", ".release-please-manifest.json",
                            "personal_release/VERSION", "personal_release/CHANGELOG.md",
                            "personal_ci_tests/test_versioning.py", "personal_docs/versioning.md",
                            ".github/workflows/new-unreviewed.yml",
                            ".github/workflows/new-unreviewed.yaml"]:
                with self.subTest(event=event, changed=changed):
                    self.assertTrue(any(fnmatchcase(changed, pattern) for pattern in patterns))

    def test_release_workflow_rejects_unreviewed_privileges_sources_and_commands(self):
        text = (ROOT / ".github/workflows/personal-release.yml").read_text()
        mutations = [
            ("permissions: {}", "permissions: write-all"),
            ("contents: write", "contents: read\n      id-token: write"),
            ("  push:", "  workflow_dispatch:\n  push:"),
            ("  push:", "  pull_request_target:\n  push:"),
            ("  push:", "  workflow_run:\n  push:"),
            ("branches: [main]", "branches: ['**']"),
            ("cancel-in-progress: false", "cancel-in-progress: true"),
            (ACTION, "googleapis/release-please-action@v5"),
            (ACTION, "unreviewed/release-please-action@" + ACTION.split("@")[1]),
            ("token: ${{ github.token }}", "token: ${{ secrets.RELEASE_PAT }}"),
            ("repo-url: mfaddis01/lumibot", "repo-url: fixture-owner/other-repo"),
            ("target-branch: main", "target-branch: dev"),
            ("    if: github.repository", "    if: true || github.repository"),
            ("    steps:", "    steps:\n      - run: npm publish"),
            ("    steps:", "    steps:\n      - run: python deploy.py"),
            ("    steps:", "    steps:\n      - run: gh pr merge --auto"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            target = fixture / ".github/workflows/personal-release.yml"
            target.parent.mkdir(parents=True)
            for old, new in mutations:
                with self.subTest(mutation=new):
                    self.assertIn(old, text)
                    target.write_text(text.replace(old, new, 1))
                    with patch(__name__ + ".ROOT", fixture), self.assertRaises(AssertionError):
                        self.test_release_workflow_has_closed_trigger_permissions_and_single_pinned_action()

    def test_rejects_inconsistent_or_prerelease_metadata(self):
        config = (ROOT / "release-please-config.json").read_text()
        cases = [
            ({".": "0.2.0"}, "0.1.0\n", "## 0.2.0\n"),
            ({".": "0.2.0-rc.1"}, "0.2.0-rc.1\n", "## 0.2.0-rc.1\n"),
            ({".": "0.2.0"}, "0.2.0\n", "## 0.1.0\n"),
            ({".": "0.2.0", "upstream": "4.6.64"}, "0.2.0\n", "## 0.2.0\n"),
            ({}, "0.2.0\n", "Unreleased: At bootstrap, not runtime qualification\n"),
            ({"upstream": "4.6.64"}, "0.2.0\n", "## 0.2.0\n"),
            ({".": "00.2.0"}, "00.2.0\n", "## 00.2.0\n"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            (fixture / "release-please-config.json").write_text(config)
            (fixture / "personal_release").mkdir()
            for manifest, version, changelog in cases:
                with self.subTest(manifest=manifest, version=version):
                    (fixture / ".release-please-manifest.json").write_text(json.dumps(manifest))
                    (fixture / "personal_release/VERSION").write_text(version)
                    (fixture / "personal_release/CHANGELOG.md").write_text(changelog)
                    with patch(__name__ + ".ROOT", fixture), self.assertRaises(AssertionError):
                        self.test_simple_strategy_isolated_from_upstream_with_exclusive_boundary()

    def test_documentation_retains_activation_boundary_and_current_token_caveats(self):
        path = ROOT / "personal_docs/versioning.md"
        self.assertTrue(path.is_file(), "personal versioning documentation missing")
        text = path.read_text()
        for required in [BOUNDARY, ACTION, "personal-app-v0.1.0", "0.1.1", "0.2.0",
                         "v4.6.64", "bootstrap", "not runtime qualification",
                         "owner", "squash", "feat:", "fix:", "feat!:",
                         "GITHUB_TOKEN", "Allow GitHub Actions to create and approve pull requests",
                         "approval-required", "opened", "synchronize", "reopened",
                         "workflow_dispatch", "repository_dispatch", "no PAT",
                         "Actions remains disabled", "no deployment", "no package publishing",
                         "independent review", "Release-As", "Unreleased"]:
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_release_workflow_has_closed_trigger_permissions_and_single_pinned_action(self):
        path = ROOT / ".github/workflows/personal-release.yml"
        self.assertTrue(path.is_file(), "personal release workflow missing")
        # Exact allowlist, not a YAML parser: extra keys/jobs/steps, duplicate keys,
        # aliases, shell commands and alternate tokens all fail closed.
        self.assertEqual(path.read_text(), """name: Personal application release

on:
  push:
    branches: [main]

permissions: {}

concurrency:
  group: personal-app-release-main
  cancel-in-progress: false

jobs:
  release:
    if: github.repository == 'mfaddis01/lumibot' && github.event_name == 'push' && github.ref == 'refs/heads/main'
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    permissions:
      contents: write
      issues: write
      pull-requests: write
    steps:
      - uses: """ + ACTION + """ # v5.0.0
        with:
          token: ${{ github.token }}
          repo-url: mfaddis01/lumibot
          target-branch: main
          config-file: release-please-config.json
          manifest-file: .release-please-manifest.json
""")

    def test_metadata_only_changes_have_read_only_checks_for_both_events(self):
        path = ROOT / ".github/workflows/personal-release-checks.yml"
        self.assertTrue(path.is_file(), "personal metadata checks workflow missing")
        paths = """    paths:
      - "release-please-config.json"
      - ".release-please-manifest.json"
      - "personal_release/**"
      - "personal_ci_tests/**"
      - "personal_docs/versioning.md"
      - ".github/workflows/**"
"""
        self.assertEqual(path.read_text(), """name: Personal release metadata checks

on:
  pull_request:
    branches: [main]
""" + paths + """  push:
    branches: [main]
""" + paths + """
permissions:
  contents: read

concurrency:
  group: personal-release-checks-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  metadata:
    runs-on: ubuntu-24.04
    timeout-minutes: 5
    env:
      PYTHONDONTWRITEBYTECODE: "1"
      PYTHONNOUSERSITE: "1"
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
          fetch-depth: 1
      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6.3.0
        with:
          python-version: "3.12"
          check-latest: false
      - name: Offline version and workflow policy checks
        run: python -m unittest discover -s personal_ci_tests -p 'test_*policy.py'
      - name: Offline versioning checks
        run: python -m unittest discover -s personal_ci_tests -p test_versioning.py
""")


if __name__ == "__main__":
    unittest.main()
