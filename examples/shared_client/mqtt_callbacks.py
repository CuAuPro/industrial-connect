import logging

from industrial_connect.db import get_database
from industrial_connect.errors import IndustrialConnectError

log = logging.getLogger(__name__)


def on_message(mqtt_client, userdata, message):
    """Get the application-owned database without passing it through the callback."""
    try:
        get_database("production").execute(
            "INSERT INTO mqtt_messages(topic, payload) VALUES (?, ?)",
            (message.topic, bytes(message.payload)),
        )
    except IndustrialConnectError:
        log.error("Could not store MQTT message")
