"""Send structured collector logs to an explicitly configured GELF/UDP input."""

import argparse
import os

from industrial_connect.logging import get_logger, setup_logging, shutdown_logging

from ._common import run

logger = get_logger(__name__)


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--host", default=os.getenv("GRAYLOG_HOST"), help="GELF/UDP host")
    cli.add_argument("--port", type=int, default=int(os.getenv("GRAYLOG_PORT", "12201")))
    args = cli.parse_args()
    if not args.host:
        cli.error("Set GRAYLOG_HOST or pass --host")
    setup_logging("equipment-collector", graylog_host=args.host, graylog_port=args.port)
    try:
        logger.info(
            "Measurement collected", extra={"equipment_id": "MILL_1", "temperature_c": 72.5}
        )
    finally:
        shutdown_logging()


if __name__ == "__main__":
    run(main)
