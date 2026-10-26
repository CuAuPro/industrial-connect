from industrial_connect.db import SqliteClient


def save_reading(database: SqliteClient, value: float) -> None:
    database.execute("insert into readings(value) values (?)", (value,))
