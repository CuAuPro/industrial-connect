"""Consume MQTT telemetry using an on_message worker instead of polling a queue."""

import time

from industrial_connect import OperationError
from industrial_connect.logging import get_logger, setup_logging
from industrial_connect.mqtt import MqttClient, MqttConfig, MqttMessage

from ._common import parser, positive_seconds, run
from .mqtt import decode_reading

logger = get_logger(__name__)


def main() -> None:
    cli = parser(__doc__)
    cli.add_argument("--topic", default="industrial-connect/examples/temperature")
    cli.add_argument("--duration", type=positive_seconds, default=30)
    args = cli.parse_args()
    setup_logging("examples.mqtt_callback")

    def on_message(message: MqttMessage) -> None:
        try:
            print(decode_reading(message.payload))
        except (ValueError, UnicodeError):
            logger.warning("Rejected malformed telemetry; payload omitted")

    client = MqttClient(MqttConfig.from_env(env_file=args.env_file), on_message=on_message)
    client.subscribe(args.topic, qos=1)
    with client:
        # Keep the application alive while its worker invokes on_message.
        time.sleep(args.duration)
    if client.callback_error_count:
        raise OperationError("A message callback failed; inspect last_callback_error")
    if client.dropped_count:
        raise OperationError("MQTT callback queue overflowed; telemetry was lost")


if __name__ == "__main__":
    run(main)
