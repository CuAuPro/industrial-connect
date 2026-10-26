"""Compatibility entrypoint; prefer examples.sqlite/postgres/oracle/mssql."""

import importlib
import sys

from ._common import run


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in {"sqlite", "postgres", "oracle", "mssql"}:
        raise SystemExit(
            "Usage: python -m examples.databases sqlite|postgres|oracle|mssql [options]"
        )
    connector = sys.argv.pop(1)
    importlib.import_module(f"examples.{connector}").main()


if __name__ == "__main__":
    run(main)
