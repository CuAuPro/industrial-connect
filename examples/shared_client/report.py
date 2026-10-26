from industrial_connect.db import SqliteClient


def readings(database: SqliteClient):
    return database.query("select value from readings")
