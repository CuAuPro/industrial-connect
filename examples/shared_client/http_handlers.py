"""Framework-independent functions usable from an HTTP route."""

from industrial_connect.db import Row, get_database


def get_messages() -> list[Row]:
    return get_database("production").query("SELECT topic, payload FROM mqtt_messages")


def get_reports() -> list[Row]:
    return get_database("reporting").query("SELECT value FROM reports")
