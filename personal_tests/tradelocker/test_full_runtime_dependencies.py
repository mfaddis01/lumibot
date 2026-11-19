"""Installed closure and exact local wheels, derived from the public hash lock.

No resolver reports, account observations or machine-specific receipts needed.
This native lock is only qualified for Linux x86_64, CPython 3.12.
"""
import hashlib
import importlib.metadata as metadata
from pathlib import Path
import unittest

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename

ROOT = Path(__file__).resolve().parents[2]


class FullRuntimeDependencyTests(unittest.TestCase):
    def test_installed_dependencies_and_enabled_extras_close_with_exact_wheel_hashes(self):
        from inputs import require
        environment = default_environment()
        self.assertEqual((environment['implementation_name'], environment['python_version'],
                          environment['sys_platform'], environment['platform_machine']),
                         ('cpython', '3.12', 'linux', 'x86_64'), 'Native lock requires requalification on other targets')
        lock = (ROOT / 'personal_trading/tradelocker/requirements-full-runtime.lock.txt').read_text()
        selected = {}
        for line in lock.splitlines():
            if not line or line.startswith('#'):
                continue
            declaration, sha = line.split(' --hash=sha256:')
            requirement = Requirement(declaration)
            selected[canonicalize_name(requirement.name)] = (requirement, sha)
        self.assertEqual(len(selected), 265)
        reached = {'lumibot', 'tradelocker'}
        extras = {name: set() for name in selected}
        changed = True
        edges = set()
        while changed:
            changed = False
            for name in list(reached):
                for index, text in enumerate(metadata.requires(name) or []):
                    requirement = Requirement(text)
                    if requirement.marker and not any(
                        requirement.marker.evaluate(dict(environment, extra=extra))
                        for extra in {''} | extras[name]
                    ):
                        continue
                    # Count declared edges, including duplicate Kubernetes metadata
                    # entries, but not repeated fixed-point traversal visits.
                    edges.add((name, index, text))
                    target = canonicalize_name(requirement.name)
                    self.assertIn(target, selected, str(requirement))
                    self.assertIn(metadata.version(target), requirement.specifier, str(requirement))
                    if target not in reached:
                        reached.add(target)
                        changed = True
                    missing = set(requirement.extras) - extras[target]
                    if missing:
                        extras[target].update(missing)
                        changed = True
        self.assertEqual(reached, set(selected))
        self.assertEqual(len(edges), 627)
        self.assertEqual({name: value for name, value in extras.items() if value},
                         {name: set(req.extras) for name, (req, _) in selected.items() if req.extras})
        wheels = {}
        for path in require('wheelhouse').glob('*.whl'):
            name, version, _, _ = parse_wheel_filename(path.name)
            if name in selected and str(version) in selected[name][0].specifier:
                wheels.setdefault(name, []).append(path)
        self.assertEqual(set(wheels), set(selected))
        for name, (requirement, sha) in selected.items():
            self.assertIn(metadata.version(name), requirement.specifier)
            self.assertEqual(len(wheels[name]), 1, 'Ambiguous locked wheel: ' + name)
            self.assertEqual(hashlib.sha256(wheels[name][0].read_bytes()).hexdigest(), sha)


if __name__ == '__main__':
    unittest.main()
