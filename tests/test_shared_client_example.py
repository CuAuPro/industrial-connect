from types import SimpleNamespace

import pytest

from examples.shared_client.http_handlers import get_messages, get_reports
from examples.shared_client.mqtt_callbacks import on_message
from industrial_connect.db import (
    SqliteClient,
    SqliteConfig,
    close_all_databases,
    close_database,
    get_database,
    init_database,
)


def test_imported_accessor_shares_client_with_callback_and_resets_on_close():
    close_all_databases()
    with pytest.raises(KeyError, match="init_database"):
        get_database("production")
    database = init_database("production", SqliteClient(SqliteConfig(database=":memory:")))
    try:
        assert get_database("production") is database
        database.execute("CREATE TABLE mqtt_messages(topic TEXT, payload BLOB)")
        reporting = init_database("reporting", SqliteClient(SqliteConfig(database=":memory:")))
        reporting.execute("CREATE TABLE reports(value REAL)")
        reporting.execute("INSERT INTO reports VALUES (?)", (7.5,))
        on_message(None, None, SimpleNamespace(topic="plant/value", payload=b"12.5"))
        assert get_messages() == [
            {"topic": "plant/value", "payload": b"12.5"},
        ]
        assert get_reports() == [{"value": 7.5}]
    finally:
        close_all_databases()
    close_database("production")
    with pytest.raises(KeyError, match="production"):
        get_database("production")
    replacement = init_database("production", SqliteClient(SqliteConfig(database=":memory:")))
    try:
        assert replacement is not database
    finally:
        close_database("production")
