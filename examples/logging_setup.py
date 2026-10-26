"""Rotating file and console logs for an equipment collector."""

import argparse
from pathlib import Path

from industrial_connect.logging import get_logger, setup_logging, shutdown_logging

from ._common import run

logger = get_logger(__name__)


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--file", type=Path, default=Path("logs/collector.log"))
    args = cli.parse_args()
    args.file.parent.mkdir(parents=True, exist_ok=True)
    setup_logging("equipment-collector", file=args.file, max_bytes=5_000_000, backup_count=5)
    try:
        logger.info("Collector started", extra={"equipment_id": "MILL_1"})
        logger.warning("Measurement unavailable for equipment %s", "MILL_1")
        # Driver exception chains and raw connection strings are deliberately omitted.
        logger.info("Collector stopped")
    finally:
        shutdown_logging()


if __name__ == "__main__":
    run(main)
