"""python -B -m strategy INPUT.json (or '-' for stdin), offline only."""
import argparse
import json
from pathlib import Path
import sys
from .replay import replay
from .input import loads


def main():
    parser = argparse.ArgumentParser(description="Offline, disarmed gold signal replay; no trading or network.")
    parser.add_argument("input", help="Local JSON path, or '-' for stdin")
    args = parser.parse_args()
    try:
        text = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
        result = replay(loads(text))
        print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
        return 0
    except (ValueError, TypeError, KeyError, OSError, ArithmeticError):
        print(json.dumps({"error":"INVALID_OFFLINE_REPLAY_INPUT", "execution_enabled":False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
