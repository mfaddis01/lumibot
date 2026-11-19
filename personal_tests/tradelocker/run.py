"""Offline tests. Full suite requires explicit read-only source/wheel/evidence inputs."""
import argparse
from pathlib import Path
import sys
import unittest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=('core', 'full'), default='core')
    for name in ('framework-source', 'sdk-wheel', 'wheelhouse'):
        parser.add_argument('--' + name, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root))
    import inputs
    for name in ('framework_source', 'sdk_wheel', 'wheelhouse'):
        value = getattr(args, name)
        if args.suite == 'full' and value is None:
            parser.error('--' + name.replace('_', '-') + ' is required for the full suite')
        if value is not None:
            value = value.resolve(strict=True)
            inputs.VALUES[name] = value
    excluded = {'test_full_runtime_dependencies.py', 'test_full_runtime_packaging.py', 'test_sdk_packaging.py'}
    suite = unittest.TestSuite()
    for path in sorted(root.glob('test_*.py')):
        if args.suite == 'core' and path.name in excluded:
            continue
        suite.addTests(unittest.defaultTestLoader.discover(str(root), pattern=path.name))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == '__main__':
    raise SystemExit(main())
