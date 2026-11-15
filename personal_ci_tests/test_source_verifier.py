"""Small synthetic source fixtures; no Git mutation, extraction or execution."""
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "personal_deploy/source_bundle.py"
COMMIT = "a" * 40


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class SourceVerifierTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("source_verifier_regression", MODULE)
        assert spec is not None and spec.loader is not None
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.sandbox = tempfile.TemporaryDirectory()
        self.addCleanup(self.sandbox.cleanup)
        self.files = {name: b"x" for name in self.module.REQUIRED_FILES}
        self.records = [{"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                        for name, data in sorted(self.files.items())]
        self.manifest = {
            "schema_version": 1, "artifact_kind": "source-bundle",
            "dependency_resolution": "not-performed",
            "target": {"os": None, "architecture": None, "python_abi": None},
            "reviewed_upstream_base": {"repository": "Lumiwealth/lumibot", "tag": "v4.6.64",
                                       "commit": "951732e501bccfbffc3718c9e6bd27e51f07fef0"},
            "builder_sha256": "b" * 64, "repository": "mfaddis01/lumibot",
            "source_commit": COMMIT, "source_mode": "committed",
            "release_eligible": False, "runtime_installed": False,
            "target_compatibility": "unverified",
            "payload_sha256": hashlib.sha256(canonical(self.records)).hexdigest(),
            "files": self.records,
        }

    def archive(self, manifest=None, extra=()):
        files = {**self.files, "SOURCE-MANIFEST.json": canonical(self.manifest) if manifest is None else manifest}
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for name, data in sorted(files.items()) + list(extra):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        data = gzip.compress(raw.getvalue(), mtime=0)
        output = Path(self.sandbox.name) / "fixture.tar.gz"
        output.write_bytes(data)
        return output, hashlib.sha256(data).hexdigest()

    def verify(self, manifest=None, **kwargs):
        output, digest = self.archive(manifest)
        return self.module.verify_bundle(output, digest, COMMIT, **kwargs)

    def test_verifier_requests_bounded_compressed_and_expanded_reads(self):
        output, digest = self.archive()
        expanded_reads = []
        original_read = gzip.GzipFile.read

        def observe_read(stream, size=-1):
            expanded_reads.append(size)
            return original_read(stream, size)

        with output.open("rb") as stream:
            with patch.object(Path, "open", return_value=stream), \
                    patch.object(stream, "read", wraps=stream.read) as compressed_read, \
                    patch.object(gzip.GzipFile, "read", observe_read):
                self.assertEqual(self.module.verify_bundle(output, digest, COMMIT), self.manifest)
                compressed_read.assert_called_once_with(self.module.MAX_COMPRESSED_BYTES + 1)
        self.assertEqual(expanded_reads, [self.module.MAX_EXPANDED_BYTES + 1])

    def test_record_fields_must_exactly_describe_sorted_archive_payload(self):
        changes = [[], self.records[::-1], self.records + [self.records[0]],
                   [None] + self.records[1:]]
        for field in ["path", "size", "sha256"]:
            changes += [[{k: v for k, v in self.records[0].items() if k != field}] + self.records[1:]]
            for value in [None, [], {}, False, "invalid"]:
                changes += [[{**self.records[0], field: value}] + self.records[1:]]
        changes += [[{**self.records[0], "unexpected": None}] + self.records[1:]]
        for index, records in enumerate(changes):
            with self.subTest(case=index), self.assertRaises(ValueError):
                self.verify(canonical({**self.manifest, "files": records}))
        for digest in [None, "", "A" * 64, [], {}, 1]:
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                self.verify(canonical({**self.manifest, "payload_sha256": digest}))

    def test_valid_source_metadata_stays_unqualified_in_both_modes(self):
        self.assertEqual(self.verify(), self.manifest)
        self.manifest["source_mode"] = "working-tree-preview"
        with self.assertRaises(ValueError):
            self.verify()
        manifest = self.verify(allow_preview=True)
        self.assertIs(manifest["release_eligible"], False)
        self.assertIs(manifest["runtime_installed"], False)
        self.assertEqual(manifest["target"], {"os": None, "architecture": None, "python_abi": None})
        self.assertEqual(manifest["target_compatibility"], "unverified")

    def test_cli_malformed_gzip_after_bounded_read_has_no_traceback(self):
        output, digest = self.archive()
        original = output.read_bytes()
        for data in [original[:-1], original[:10] + b"\x07" + b"\0" * 8, b"not-gzip"]:
            output.write_bytes(data)
            result = subprocess.run([sys.executable, str(MODULE), "verify", "--artifact", str(output),
                                     "--sha256", hashlib.sha256(data).hexdigest(), "--commit", COMMIT],
                                    capture_output=True, text=True, timeout=5)
            with self.subTest(data=data[:16]):
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertNotIn(str(output), result.stderr)
                self.assertEqual(result.stdout, "")

    def test_cli_deep_manifest_parser_failure_is_controlled_without_traceback(self):
        # Native-parser failure reproduced at this depth on Python 3.12.3;
        # 1,500 levels did not reproduce it. Do not alter the recursion limit.
        depth = 12000
        manifest = b'{"target":' + b'[' * depth + b'0' + b']' * depth + b'}'
        self.assertLessEqual(len(manifest), 64 * 1024)
        output, digest = self.archive(manifest)
        result = subprocess.run([sys.executable, "-I", "-B", str(MODULE), "verify",
                                 "--artifact", str(output), "--sha256", digest, "--commit", COMMIT],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("source manifest", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn(str(output), result.stderr)
        self.assertNotIn(str(MODULE), result.stderr)
        self.assertEqual(result.stdout, "")

    def test_member_count_limit_includes_manifest_and_accepts_boundary(self):
        output, digest = self.archive()
        count = len(self.files) + 1
        with patch.object(self.module, "MAX_MEMBERS", count - 1, create=True):
            with self.assertRaisesRegex(ValueError, "member count.*limit"):
                self.module.verify_bundle(output, digest, COMMIT)
        with patch.object(self.module, "MAX_MEMBERS", count, create=True):
            self.assertEqual(self.module.verify_bundle(output, digest, COMMIT), self.manifest)

    def test_member_size_limit_covers_manifest_and_source_before_reading(self):
        for large_source in [False, True]:
            if large_source:
                self.files["lumibot/__init__.py"] = b"x" * 4096
                self.manifest["files"] = [
                    {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                    for name, data in sorted(self.files.items())]
                self.manifest["payload_sha256"] = hashlib.sha256(canonical(self.manifest["files"])).hexdigest()
            output, digest = self.archive()
            size = max(len(canonical(self.manifest)), *(len(data) for data in self.files.values()))
            with self.subTest(large_source=large_source):
                with patch.object(self.module, "MAX_MEMBER_BYTES", size - 1, create=True):
                    with self.assertRaisesRegex(ValueError, "member.*limit"):
                        self.module.verify_bundle(output, digest, COMMIT)
                with patch.object(self.module, "MAX_MEMBER_BYTES", size, create=True):
                    self.assertEqual(self.module.verify_bundle(output, digest, COMMIT), self.manifest)

    def test_expanded_archive_limit_covers_padding_and_concatenated_gzip(self):
        output, digest = self.archive()
        original = output.read_bytes()
        expanded_size = len(gzip.decompress(original))
        for tail in [b"", gzip.compress(b"\0" * 2048, mtime=0)]:
            output.write_bytes(original + tail)
            digest = hashlib.sha256(original + tail).hexdigest()
            with self.subTest(concatenated=bool(tail)):
                limit = expanded_size if tail else expanded_size - 1
                with patch.object(self.module, "MAX_EXPANDED_BYTES", limit, create=True):
                    with self.assertRaisesRegex(ValueError, "expanded.*limit"):
                        self.module.verify_bundle(output, digest, COMMIT)
        output.write_bytes(original)
        with patch.object(self.module, "MAX_EXPANDED_BYTES", expanded_size, create=True):
            self.assertEqual(self.module.verify_bundle(output, hashlib.sha256(original).hexdigest(), COMMIT),
                             self.manifest)

    def test_compressed_archive_limit_rejects_one_byte_over_and_accepts_boundary(self):
        output, digest = self.archive()
        size = output.stat().st_size
        with patch.object(self.module, "MAX_COMPRESSED_BYTES", size - 1, create=True):
            with self.assertRaisesRegex(ValueError, "compressed.*limit"):
                self.module.verify_bundle(output, digest, COMMIT)
        with patch.object(self.module, "MAX_COMPRESSED_BYTES", size, create=True):
            self.assertEqual(self.module.verify_bundle(output, digest, COMMIT), self.manifest)

    def test_allowlist_rejects_dot_and_empty_components_before_indexing(self):
        for name in [".", "", "./", "./lumibot/a.py", "lumibot/./a.py",
                     "lumibot//a.py", "lumibot/a.py/", "/lumibot/a.py"]:
            with self.subTest(name=name):
                self.assertFalse(self.module.allowed(name))

    def test_cli_dot_member_is_controlled_refusal_without_traceback(self):
        output, digest = self.archive(extra=[(".", b"")])
        result = subprocess.run([sys.executable, str(MODULE), "verify", "--artifact", str(output),
                                 "--sha256", digest, "--commit", COMMIT],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("unsafe or duplicate archive member", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn(str(output), result.stderr)
        self.assertEqual(result.stdout, "")

    def test_file_records_require_exact_nonnegative_integer_sizes(self):
        for size in [True, 1.0, -1, "1", None]:
            records = [dict(record) for record in self.records]
            records[0]["size"] = size
            with self.subTest(size=size), self.assertRaises(ValueError):
                self.verify(canonical({**self.manifest, "files": records}))

    def test_manifest_requires_complete_closed_field_set_and_supported_shapes(self):
        for field in self.manifest:
            missing = {k: v for k, v in self.manifest.items() if k != field}
            with self.subTest(missing=field), self.assertRaises(ValueError):
                self.verify(canonical(missing))
            for value in [None, [], {}, False]:
                if value is False and field in {"release_eligible", "runtime_installed"}:
                    continue
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.verify(canonical({**self.manifest, field: value}))
        with self.assertRaises(ValueError):
            self.verify(canonical({**self.manifest, "runtime_qualified": True}))

    def test_source_target_requires_exact_null_fields_and_false_flags(self):
        target = self.manifest["target"]
        values = [None, [], "unknown", {}, {"arbitrary": "qualified"}, {**target, "extra": None}]
        for field in target:
            values += [{k: v for k, v in target.items() if k != field},
                       {**target, field: "selected"}, {**target, field: False}]
        for value in values:
            with self.subTest(target=value), self.assertRaises(ValueError):
                self.verify(canonical({**self.manifest, "target": value}))
        for field in ["release_eligible", "runtime_installed"]:
            for value in [True, 0, 0.0, None, "false", [], {}]:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.verify(canonical({**self.manifest, field: value}))

    def test_reviewed_upstream_reference_is_exact_supported_metadata(self):
        reference = self.manifest["reviewed_upstream_base"]
        values = [None, [], "v4.6.64", {}, {**reference, "extra": "claim"}]
        for field in reference:
            values += [{k: v for k, v in reference.items() if k != field},
                       {**reference, field: "unreviewed"}, {**reference, field: None}]
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.verify(canonical({**self.manifest, "reviewed_upstream_base": value}))
        del self.manifest["reviewed_upstream_base"]
        with self.assertRaises(ValueError):
            self.verify()

    def test_builder_digest_is_mandatory_lowercase_sha256(self):
        for value in [None, "", "a" * 63, "G" * 64, "B" * 64, 1, [], {}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.verify(canonical({**self.manifest, "builder_sha256": value}))
        del self.manifest["builder_sha256"]
        with self.assertRaises(ValueError):
            self.verify()

    def test_schema_version_requires_exact_integer_one(self):
        for value in [True, 1.0, None, "1", [], {}]:
            manifest = {**self.manifest, "schema_version": value}
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.verify(canonical(manifest))

    def test_duplicate_json_keys_cannot_erase_preview_or_nested_claims(self):
        original = canonical(self.manifest)
        for data in [
            original.replace(b'"source_mode":"committed"',
                             b'"source_mode":"working-tree-preview","source_mode":"committed"'),
            original.replace(b'"os":null', b'"os":"qualified","os":null'),
            original.replace(b'"size":1', b'"size":2,"size":1', 1),
        ]:
            with self.subTest(manifest=data), self.assertRaisesRegex(ValueError, "manifest"):
                self.verify(data)


if __name__ == "__main__":
    unittest.main()
