"""Consume MQTT on the application thread and share a named database across modules."""

import time
from queue import Empty

from examples._common import parser, positive_seconds, run
from industrial_connect import OperationError
from industrial_connect.db import SqliteClient, SqliteConfig, close_database, init_database
from industrial_connect.mqtt import MqttClient, MqttConfig

from .mqtt_callbacks import on_message


def main() -> None:
    cli = parser(__doc__)
    cli.add_argument("--topic", default="industrial-connect/examples/temperature")
    cli.add_argument("--duration", type=positive_seconds, default=30)
    args = cli.parse_args()
    database = init_database(
        "production", SqliteClient(SqliteConfig.from_env(env_file=args.env_file))
    )
    try:
        database.execute("CREATE TABLE IF NOT EXISTS mqtt_messages(topic TEXT, payload BLOB)")
        broker = MqttClient(MqttConfig.from_env(env_file=args.env_file))
        broker.subscribe(args.topic, qos=1)
        with broker:
            deadline = time.monotonic() + args.duration
            while (remaining := deadline - time.monotonic()) > 0:
                try:
                    message = broker.get_notification(timeout=min(remaining, 1))
                except Empty:
                    continue
                # The imported handler uses get_database("production"). No DB I/O on Paho's thread.
                on_message(None, None, message)
            if broker.dropped_count:
                raise OperationError("MQTT notification queue overflowed; messages were lost")
    finally:
        close_database("production")


if __name__ == "__main__":
    run(main)
