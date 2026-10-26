"""Read and monitor PLC measurements with quality and timestamps preserved."""

import time
from queue import Empty

from industrial_connect import OperationError
from industrial_connect.logging import get_logger, setup_logging
from industrial_connect.opcua import OpcUaClient, OpcUaConfig, OpcUaWrite

from ._common import finite_number, parser, positive_seconds, run

logger = get_logger(__name__)


def main() -> None:
    cli = parser(__doc__)
    cli.add_argument("--node", action="append", required=True, help="Repeat for several node IDs")
    cli.add_argument("--duration", type=positive_seconds, default=30)
    cli.add_argument("--sampling-ms", type=finite_number, default=500)
    cli.add_argument(
        "--write-double", type=finite_number, help="Explicitly write to the single node"
    )
    args = cli.parse_args()
    if args.write_double is not None and len(args.node) != 1:
        cli.error("--write-double requires exactly one --node")
    setup_logging("examples.opcua")
    with OpcUaClient(OpcUaConfig.from_env(env_file=args.env_file)) as client:
        for value in client.read_many(args.node):
            print(value)
        subscription = client.subscribe(args.node, sampling_interval_ms=args.sampling_ms)
        if args.write_double is not None:
            results = client.write_many([OpcUaWrite(args.node[0], args.write_double, "Double")])
            if any(not result.status_code.is_good() for result in results):
                raise OperationError("PLC rejected the requested write")
        deadline = time.monotonic() + args.duration
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                event = client.get_notification(timeout=min(remaining, 1))
            except Empty:
                continue
            if event.kind != "data":
                logger.warning("OPC UA event: %s; values are unavailable", event.kind)
            for value in event.values:
                print(value)
        if client.dropped_count:
            raise OperationError("OPC UA notification queue overflowed; measurements were lost")
        client.unsubscribe(subscription)


if __name__ == "__main__":
    run(main)
