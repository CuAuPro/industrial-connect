"""Consume validated equipment telemetry; optionally publish one example reading."""

import json
import math
import time
from datetime import UTC, datetime
from queue import Empty

from industrial_connect import OperationError
from industrial_connect.logging import get_logger, setup_logging
from industrial_connect.mqtt import MqttClient, MqttConfig

from ._common import parser, positive_seconds, run

logger = get_logger(__name__)


def decode_reading(payload: bytes) -> dict:
    reading = json.loads(payload)
    if not isinstance(reading, dict):
        raise ValueError("Telemetry must be a JSON object")
    equipment = reading.get("equipment_id")
    value = reading.get("temperature_c")
    observed = reading.get("observed_at")
    if not isinstance(equipment, str) or not equipment.strip():
        raise ValueError("Missing equipment_id")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("temperature_c must be a finite number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError("temperature_c must be a finite number")
    if not isinstance(observed, str):
        raise ValueError("Missing observed_at")
    timestamp = datetime.fromisoformat(observed.replace("Z", "+00:00"))
    if timestamp.utcoffset() is None:
        raise ValueError("observed_at needs a UTC offset")
    return {
        "equipment_id": equipment,
        "temperature_c": value,
        "observed_at": timestamp.astimezone(UTC).isoformat(),
    }


def main() -> None:
    cli = parser(__doc__)
    cli.add_argument("--topic", default="industrial-connect/examples/temperature")
    cli.add_argument("--duration", type=positive_seconds, default=30)
    cli.add_argument("--publish-demo", action="store_true")
    args = cli.parse_args()
    setup_logging("examples.mqtt")
    client = MqttClient(MqttConfig.from_env(env_file=args.env_file))
    client.subscribe(args.topic, qos=1)
    with client:
        if args.publish_demo:
            # Subscribe and publish share one connection, preserving request order.
            client.publish(
                args.topic,
                json.dumps(
                    {
                        "equipment_id": "MILL_1",
                        "temperature_c": 72.5,
                        "observed_at": datetime.now(UTC).isoformat(),
                    }
                ),
                qos=1,
                retain=False,
                wait=True,
            )
        deadline = time.monotonic() + args.duration
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                message = client.get_notification(timeout=min(remaining, 1))
            except Empty:
                if not client.connected:
                    logger.warning("Broker disconnected; automatic reconnect is active")
                continue
            try:
                print(decode_reading(message.payload))
            except (ValueError, UnicodeError):
                logger.warning("Rejected malformed telemetry; payload omitted")
        if client.dropped_count:
            raise OperationError("MQTT notification queue overflowed; telemetry was lost")


if __name__ == "__main__":
    run(main)
