"""Offline-only CLI. No command loads runtime credentials or starts a strategy."""
import argparse
import json
import sys

from .settings import Settings


class _SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's usual diagnostics echo arbitrary supplied values.
        self.exit(2, "invalid command arguments; use --help\n")


def main(argv=None):
    parser = _SafeParser(description="Paper foundation: offline validation only")
    parser.add_argument("command", choices=("validate", "readiness", "run"))
    parser.add_argument("--config", required=True)
    arguments = parser.parse_args(argv)
    try:
        settings = Settings.load(arguments.config)
    except ValueError:
        print("configuration invalid or unreadable", file=sys.stderr)
        return 2
    status = {"phase": "OFFLINE_VALIDATED", "config_identity": settings.identity,
              "config_armed": settings.as_mapping()["armed"],
              "execution": "DISARMED", "orders_enabled": False, "broker_checked": False}
    if arguments.command == "run":
        status["reason"] = "execution unavailable: policy enforcement and reconciliation not implemented"
    print(json.dumps(status, sort_keys=True))
    return 3 if arguments.command == "run" else 0
