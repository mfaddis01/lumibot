"""Offline behavioral tests; no framework imports or project setup execution."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class SourceRootsTests(unittest.TestCase):
    def test_ast_extracts_literal_mandatory_roots_without_executing_setup(self):
        import dependency_candidate as candidate
        source = 'raise RuntimeError("must not execute")\nsetuptools.setup(install_requires=["a>=1", "b[crypto]==2"], extras_require={"unused": ["c"]})'
        self.assertEqual(candidate.extract_roots(source), ["a>=1", "b[crypto]==2"])
        with self.assertRaises(ValueError):
            candidate.extract_roots('setuptools.setup(install_requires=dynamic())')


class ClosureTests(unittest.TestCase):
    def test_transitive_extras_reach_fixed_point_with_target_markers(self):
        import dependency_candidate as candidate
        # b is visited before its crypto extra is discovered via c.
        report = {"install": [
            {"metadata": {"name": "a", "version": "1", "requires_dist": ["b", "c"]}},
            {"metadata": {"name": "b", "version": "1", "requires_dist": ['d>=2; extra == "crypto"', 'wrong; platform_machine == "aarch64"']}},
            {"metadata": {"name": "c", "version": "1", "requires_dist": ["b[crypto]"]}},
            {"metadata": {"name": "d", "version": "2", "requires_python": ">=3.12"}},
        ]}
        env = dict(candidate.TARGET)
        result = candidate.close_graph(report, ["a"], env)
        self.assertEqual(result["reachable"], ["a", "b", "c", "d"])
        self.assertEqual(result["enabled_extras"], {"b": ["crypto"]})
        self.assertEqual(len(result["active_edges"]), 4)
        self.assertEqual(result["errors"], [])
        report["install"][-1]["metadata"]["version"] = "1"
        self.assertTrue(candidate.close_graph(report, ["a"], env)["errors"])
        report["install"].pop()
        self.assertTrue(candidate.close_graph(report, ["a"], env)["errors"])


class LockTests(unittest.TestCase):
    def test_lock_keeps_enabled_extras_exact_hashes_and_sdist_exception(self):
        import dependency_candidate as candidate
        report = {"install": [
            {"metadata": {"name": "PyJWT", "version": "2.15.1"}, "download_info": {"url": "https://files.pythonhosted.org/pyjwt-2.15.1-py3-none-any.whl", "archive_info": {"hashes": {"sha256": "a" * 64}}}},
            {"metadata": {"name": "ibapi", "version": "9.81.1.post1"}, "download_info": {"url": "file:///fresh/ibapi-9.81.1.post1-py3-none-any.whl", "archive_info": {"hashes": {"sha256": "b" * 64}}}},
        ]}
        closure = {"errors": [], "enabled_extras": {"pyjwt": ["crypto"]}}
        text = candidate.render_lock(report, closure, {"ibapi": ["c" * 64]})
        self.assertIn("pyjwt[crypto]==2.15.1 --hash=sha256:" + "a" * 64, text)
        self.assertIn("ibapi==9.81.1.post1 --hash=sha256:" + "b" * 64 + " --hash=sha256:" + "c" * 64, text)
        self.assertNotIn("file:///", text)
        closure["errors"].append("missing edge")
        with self.assertRaises(ValueError):
            candidate.render_lock(report, closure)
        closure["errors"] = []
        report["install"][0]["download_info"]["archive_info"]["hashes"]["sha256"] = "bad"
        with self.assertRaises(ValueError):
            candidate.render_lock(report, closure)


class ArtifactTests(unittest.TestCase):
    def test_rejects_arm_and_mismatched_published_artifact_hashes(self):
        import dependency_candidate as candidate
        name = "demo-1.0-cp312-cp312-manylinux_2_17_x86_64.whl"
        url = "https://files.pythonhosted.org/" + name
        item = {"metadata": {"name": "demo", "version": "1.0"},
                "download_info": {"url": url, "archive_info": {"hashes": {"sha256": "a" * 64}}}}
        release = {"urls": [{"filename": name, "url": url, "digests": {"sha256": "a" * 64}, "size": 123, "yanked": False, "packagetype": "bdist_wheel"}]}
        self.assertEqual(candidate.verify_artifact(item, release)["source"], "published-pypi-wheel")
        release["urls"][0]["digests"]["sha256"] = "b" * 64
        with self.assertRaises(ValueError):
            candidate.verify_artifact(item, release)
        item["download_info"]["url"] = url.replace("x86_64", "aarch64")
        with self.assertRaises(ValueError):
            candidate.verify_artifact(item, release)


if __name__ == "__main__":
    unittest.main()
