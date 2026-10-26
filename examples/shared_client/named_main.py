"""python -m examples.shared_client.named_main (no services or extras required)."""

from types import SimpleNamespace

from industrial_connect.db import SqliteClient, SqliteConfig, close_all_databases, init_database

from .http_handlers import get_messages, get_reports
from .mqtt_callbacks import on_message


def main() -> None:
    try:
        production = init_database("production", SqliteClient(SqliteConfig(database=":memory:")))
        reporting = init_database("reporting", SqliteClient(SqliteConfig(database=":memory:")))
        production.execute("CREATE TABLE mqtt_messages(topic TEXT, payload BLOB)")
        reporting.execute("CREATE TABLE reports(value REAL)")
        reporting.execute("INSERT INTO reports VALUES (?)", (12.5,))
        # Simulate message delivery; the callback imports the named database itself.
        on_message(None, None, SimpleNamespace(topic="plant/value", payload=b"12.5"))
        print(get_messages())
        print(get_reports())
    finally:
        close_all_databases()


if __name__ == "__main__":
    main()
