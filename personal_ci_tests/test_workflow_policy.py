"""Static regression guard; not a complete GitHub Actions schema validator."""
from fnmatch import fnmatchcase
from pathlib import Path
import re
import unittest


class PersonalWorkflowTests(unittest.TestCase):
    def test_only_reviewed_personal_workflow_can_be_enabled(self):
        directory = Path(__file__).resolve().parents[1] / ".github/workflows"
        names = {path.name for path in directory.iterdir()
                 if path.is_file() and path.suffix in {".yml", ".yaml"}}
        self.assertEqual(names, {"personal-checks.yml", "personal-release.yml",
                                 "personal-release-checks.yml"})

    def test_each_trigger_covers_new_unreviewed_workflow_only_changes(self):
        path = Path(__file__).resolve().parents[1] / ".github/workflows/personal-checks.yml"
        text = path.read_text()
        for event in ["pull_request", "push"]:
            block = re.search(r"^  " + event + r":\n((?:    .*\n|\n)+)", text, re.MULTILINE)
            self.assertIsNotNone(block)
            assert block is not None
            patterns = re.findall(r'^      - "([^"\n]+)"$', block.group(1), re.MULTILINE)
            with self.subTest(event=event):
                self.assertIn(".github/workflows/**", patterns)
                for changed in [".github/workflows/new-unreviewed.yml",
                                ".github/workflows/new-unreviewed.yaml",
                                ".github/workflows/nested/new.yml"]:
                    self.assertTrue(any(fnmatchcase(changed, pattern) for pattern in patterns), changed)

    def test_secret_free_pins_permissions_scope_and_commands(self):
        path = Path(__file__).resolve().parents[1] / ".github/workflows/personal-checks.yml"
        self.assertTrue(path.is_file(), "personal workflow missing")
        text = path.read_text()
        uses = re.findall(r"uses:\s*([^\s#]+)", text)
        self.assertEqual(uses, [
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1",
        ])
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("paths:", text)
        self.assertIn("branches: [main]", text)
        self.assertIn('"personal_trading/**"', text)
        self.assertIn('"lumibot/**"', text)
        self.assertIn("python -m unittest discover -s personal_tests", text)
        self.assertIn("python -m unittest discover -s personal_ci_tests", text)
        self.assertIn("source_bundle.py build", text)
        self.assertIn('$(git rev-parse HEAD)', text)
        for unsafe in ["secrets.", "contents: write", "id-token:", "pull_request_target", "workflow_run:",
                       "schedule:", "workflow_dispatch:", "pip install", "--preview", "upload-artifact", "git push"]:
            with self.subTest(unsafe=unsafe):
                self.assertNotIn(unsafe, text)


if __name__ == "__main__":
    unittest.main()
