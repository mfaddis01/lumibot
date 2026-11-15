"""Offline fixtures only; no framework import, credentials, or setup execution."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "personal_deploy" / "source_bundle.py"


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.TemporaryDirectory()
        self.addCleanup(self.sandbox.cleanup)
        self.root = Path(self.sandbox.name) / "source"
        self.root.mkdir()
        self.put("lumibot/__init__.py", "# fixture framework\n")
        self.put("personal_trading/__init__.py", "# fixture overlay\n")
        self.put("personal_deploy/source_bundle.py", "# fixture builder\n")
        self.put("setup.py", "raise RuntimeError('MUST NOT EXECUTE')\n")
        self.put("LICENSE", "fixture license\n")
        self.put("README.md", "fixture readme\n")
        self.git("init", "-q")
        self.git("remote", "add", "origin", "https://github.com/mfaddis01/lumibot.git")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
        self.commit = self.git("rev-parse", "HEAD").strip()

    def put(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, text=True, stderr=subprocess.PIPE)

    def builder(self):
        self.assertTrue(MODULE.is_file(), "source bundle builder is not implemented")
        spec = importlib.util.spec_from_file_location("source_bundle_test", MODULE)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def build(self, **kwargs):
        return self.builder().build_bundle(self.root, self.commit, Path(self.sandbox.name) / "bundle.tar.gz", **kwargs)

    def unpack(self, output):
        with tarfile.open(output, "r:gz") as archive:
            files = {}
            for member in archive.getmembers():
                stream = archive.extractfile(member)
                assert stream is not None
                files[member.name] = stream.read()
            return files

    def test_allowlist_excludes_secrets_runtime_data_and_symlinks(self):
        forbidden = [".env", ".secrets/key.py", ".gitignore", "runtime/orders.py",
                     "lumibot/.env", "lumibot/.secrets/token.py", "lumibot/runtime/orders.py",
                     "lumibot/cache/cache.py", "personal_trading/state/orders.py",
                     "personal_trading/logs/trades.py", "personal_trading/settings.json",
                     "lumibot/resources/ThetaTerminal.jar", "data/fills.csv"]
        for name in forbidden:
            self.put(name, "SENSITIVE_FIXTURE_DO_NOT_SHIP\n")
        self.put("lumibot/resources/conf.yaml", "fixture: true\n")
        self.put("lumibot/components/agents/skills/fixture/SKILL.md", "fixture skill\n")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture exclusions")
        self.commit = self.git("rev-parse", "HEAD").strip()
        self.build()
        files = self.unpack(Path(self.sandbox.name) / "bundle.tar.gz")
        self.assertTrue(set(forbidden).isdisjoint(files))
        self.assertIn("lumibot/resources/conf.yaml", files)
        self.assertIn("lumibot/components/agents/skills/fixture/SKILL.md", files)
        self.assertNotIn(b"SENSITIVE_FIXTURE", b"".join(files.values()))

    def test_rejects_unpinned_or_wrong_repository_identity(self):
        for commit in ["HEAD", self.commit[:12], "0" * 40, self.commit.upper()]:
            with self.subTest(commit=commit), self.assertRaises(ValueError):
                self.builder().build_bundle(self.root, commit, Path(self.sandbox.name) / "bad.tar.gz")
        self.git("remote", "set-url", "origin", "https://github.com/other/repo.git")
        with self.assertRaises(ValueError):
            self.build()

    def test_preview_is_explicit_and_never_mislabeled_as_committed(self):
        self.put("personal_trading/new.py", "# uncommitted fixture\n")
        self.put("lumibot/__init__.py", "# changed fixture\n")
        import inspect
        self.assertIn("preview", inspect.signature(self.builder().build_bundle).parameters)
        self.build(preview=True)
        files = self.unpack(Path(self.sandbox.name) / "bundle.tar.gz")
        manifest = json.loads(files["SOURCE-MANIFEST.json"])
        self.assertEqual(manifest["source_mode"], "working-tree-preview")
        self.assertEqual(manifest["source_commit"], self.commit)
        self.assertFalse(manifest["release_eligible"])
        self.assertIn("personal_trading/new.py", files)
        self.assertEqual(files["lumibot/__init__.py"], b"# changed fixture\n")

    def test_rejects_symlinks_in_allowlisted_payload_in_both_modes(self):
        outside = Path(self.sandbox.name) / "outside.txt"
        outside.write_text("SENSITIVE_FIXTURE\n")
        (self.root / "personal_trading/linked.py").symlink_to(outside)
        self.git("add", "personal_trading/linked.py")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture link")
        self.commit = self.git("rev-parse", "HEAD").strip()
        for preview in [False, True]:
            with self.subTest(preview=preview), self.assertRaises(ValueError):
                self.build(preview=preview)

    def test_missing_overlay_never_produces_a_framework_only_bundle(self):
        self.git("rm", "personal_trading/__init__.py")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture missing overlay")
        self.commit = self.git("rev-parse", "HEAD").strip()
        with self.assertRaises(ValueError):
            self.build()

    def test_deterministic_committed_bytes_ignore_workspace_changes(self):
        first = self.build()
        self.put("lumibot/__init__.py", "# local changes must not enter committed archive\n")
        self.put("personal_trading/extra.py", "# not committed\n")
        other = Path(self.sandbox.name) / "different-name.tar.gz"
        second = self.builder().build_bundle(self.root, self.commit, other)
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertEqual((Path(self.sandbox.name) / "bundle.tar.gz").read_bytes(), other.read_bytes())
        with tarfile.open(other) as archive:
            for member in archive.getmembers():
                self.assertEqual((member.uid, member.gid, member.mtime, member.mode), (0, 0, 0, 0o644))
                self.assertEqual((member.uname, member.gname), ("", ""))

    def test_manifest_separates_source_inputs_from_unqualified_target_runtime(self):
        self.build()
        manifest = json.loads(self.unpack(Path(self.sandbox.name) / "bundle.tar.gz")["SOURCE-MANIFEST.json"])
        self.assertEqual(manifest.get("schema_version"), 1)
        self.assertEqual(manifest.get("artifact_kind"), "source-bundle")
        self.assertEqual(manifest.get("dependency_resolution"), "not-performed")
        self.assertEqual(manifest.get("target"), {"os": None, "architecture": None, "python_abi": None})
        self.assertEqual(manifest.get("reviewed_upstream_base"), {
            "repository": "Lumiwealth/lumibot", "tag": "v4.6.64",
            "commit": "951732e501bccfbffc3718c9e6bd27e51f07fef0"})
        self.assertEqual(len(manifest.get("builder_sha256", "")), 64)

    def test_verifier_requires_external_digest_and_exact_source_commit(self):
        result = self.build()
        module = self.builder()
        self.assertTrue(callable(getattr(module, "verify_bundle", None)), "offline verifier missing")
        output = Path(self.sandbox.name) / "bundle.tar.gz"
        manifest = module.verify_bundle(output, result["sha256"], self.commit)
        self.assertEqual(manifest["source_commit"], self.commit)
        for digest, commit in [("0" * 64, self.commit), (result["sha256"], "0" * 40),
                               (result["sha256"], self.commit[:12])]:
            with self.subTest(digest=digest, commit=commit), self.assertRaises(ValueError):
                module.verify_bundle(output, digest, commit)

    def test_verifier_rejects_modified_or_unsafe_members_even_with_matching_outer_hash(self):
        import hashlib
        self.build()
        files = self.unpack(Path(self.sandbox.name) / "bundle.tar.gz")
        for change in ["tamper", "traversal", "extra", "duplicate", "symlink", "manifest"]:
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w:gz") as archive:
                for name, data in files.items():
                    if change == "tamper" and name == "personal_trading/__init__.py":
                        data = b"# altered\n"
                    if change == "manifest" and name == "SOURCE-MANIFEST.json":
                        item = json.loads(data)
                        item["payload_sha256"] = "0" * 64
                        data = json.dumps(item).encode()
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
                if change in {"traversal", "extra", "duplicate", "symlink"}:
                    name = {"traversal": "../escape.py", "extra": "personal_trading/.env",
                            "duplicate": "personal_trading/__init__.py", "symlink": "personal_trading/link.py"}[change]
                    info = tarfile.TarInfo(name)
                    if change == "symlink":
                        info.type, info.linkname = tarfile.SYMTYPE, "../../escape"
                    archive.addfile(info, io.BytesIO(b""))
            output = Path(self.sandbox.name) / (change + ".tar.gz")
            output.write_bytes(stream.getvalue())
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.builder().verify_bundle(output, hashlib.sha256(stream.getvalue()).hexdigest(), self.commit)

    def test_verifier_refuses_preview_without_explicit_preview_opt_in(self):
        result = self.build(preview=True)
        module = self.builder()
        output = Path(self.sandbox.name) / "bundle.tar.gz"
        with self.assertRaises(ValueError):
            module.verify_bundle(output, result["sha256"], self.commit)
        manifest = module.verify_bundle(output, result["sha256"], self.commit, allow_preview=True)
        self.assertFalse(manifest["release_eligible"])

    def test_cli_build_verify_round_trip_and_sanitized_failure(self):
        output = Path(self.sandbox.name) / "cli.tar.gz"
        build = subprocess.run([sys.executable, str(MODULE), "build", "--source", str(self.root),
                                "--commit", self.commit, "--output", str(output)], capture_output=True, text=True)
        self.assertEqual(build.returncode, 0, build.stderr)
        self.assertTrue(build.stdout.strip(), "builder CLI is not implemented")
        report = json.loads(build.stdout)
        self.assertEqual(report["repository"], "mfaddis01/lumibot")
        self.assertEqual(report["source_commit"], self.commit)
        check = subprocess.run([sys.executable, str(MODULE), "verify", "--artifact", str(output),
                                "--sha256", report["sha256"], "--commit", self.commit], capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertEqual(json.loads(check.stdout)["source_mode"], "committed")
        invalid = subprocess.run([sys.executable, str(MODULE), "build", "--source", str(self.root),
                                  "--commit", "HEAD", "--output", str(output)], capture_output=True, text=True)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertNotIn(str(self.root), invalid.stderr)
        self.assertNotIn("Traceback", invalid.stderr)

    def test_verifier_rejects_false_runtime_claims_and_unknown_schema(self):
        import hashlib
        self.build()
        original = self.unpack(Path(self.sandbox.name) / "bundle.tar.gz")
        for field, value in [("schema_version", 2), ("runtime_installed", True),
                             ("target_compatibility", "certified"), ("release_eligible", True),
                             ("repository", "other/repository"), ("artifact_kind", "wheel"),
                             ("dependency_resolution", "installed")]:
            files = dict(original)
            manifest = json.loads(files["SOURCE-MANIFEST.json"])
            manifest[field] = value
            files["SOURCE-MANIFEST.json"] = json.dumps(manifest).encode()
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w:gz") as archive:
                for name, data in files.items():
                    member = tarfile.TarInfo(name)
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
            output = Path(self.sandbox.name) / (field + ".tar.gz")
            output.write_bytes(stream.getvalue())
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.builder().verify_bundle(output, hashlib.sha256(stream.getvalue()).hexdigest(), self.commit)

    def test_allowlist_requires_canonical_portable_relative_paths(self):
        module = self.builder()
        for name in ["", "/lumibot/a.py", "lumibot/../a.py", "lumibot//a.py",
                     "lumibot/a\\b.py", "lumibot/a\nb.py", "lumibot/.secrets/a.py"]:
            with self.subTest(name=name):
                try:
                    accepted = module.allowed(name)
                except (IndexError, ValueError):
                    self.fail("unsafe names must be rejected explicitly")
                self.assertFalse(accepted)

    def test_verifier_rejects_nonobject_or_sourceless_manifest(self):
        import hashlib
        self.build()
        files = self.unpack(Path(self.sandbox.name) / "bundle.tar.gz")
        empty_manifest = json.loads(files["SOURCE-MANIFEST.json"])
        empty_manifest["files"] = []
        empty_manifest["payload_sha256"] = hashlib.sha256(b"[]").hexdigest()
        for index, manifest in enumerate([None, [], empty_manifest]):
            stream = io.BytesIO()
            data = json.dumps(manifest).encode()
            with tarfile.open(fileobj=stream, mode="w:gz") as archive:
                member = tarfile.TarInfo("SOURCE-MANIFEST.json")
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            output = Path(self.sandbox.name) / ("malformed-" + str(index) + ".tar.gz")
            output.write_bytes(stream.getvalue())
            with self.subTest(manifest=manifest):
                try:
                    self.builder().verify_bundle(output, hashlib.sha256(stream.getvalue()).hexdigest(), self.commit)
                except ValueError:
                    pass
                except Exception:
                    self.fail("malformed source manifests must fail explicitly without uncaught parser errors")
                else:
                    self.fail("sourceless manifest was accepted")

    def test_committed_bundle_contains_framework_overlay_and_source_identity(self):
        result = self.build()
        files = self.unpack(Path(self.sandbox.name) / "bundle.tar.gz")
        self.assertEqual(files["lumibot/__init__.py"], b"# fixture framework\n")
        self.assertEqual(files["personal_trading/__init__.py"], b"# fixture overlay\n")
        manifest = json.loads(files["SOURCE-MANIFEST.json"])
        self.assertEqual(manifest["repository"], "mfaddis01/lumibot")
        self.assertEqual(manifest["source_commit"], self.commit)
        self.assertEqual(manifest["source_mode"], "committed")
        self.assertFalse(manifest["runtime_installed"])
        self.assertEqual(manifest["target_compatibility"], "unverified")
        self.assertEqual(len(result["sha256"]), 64)
        self.assertNotIn(str(self.root).encode(), files["SOURCE-MANIFEST.json"])


if __name__ == "__main__":
    unittest.main()
