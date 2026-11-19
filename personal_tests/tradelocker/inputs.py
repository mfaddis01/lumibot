"""Explicit local read-only qualification inputs, set by run.py, never discovered."""
VALUES = {}


def require(name):
    if name not in VALUES:
        raise RuntimeError('Run personal_tests.tradelocker.run with --' + name.replace('_', '-'))
    return VALUES[name]
