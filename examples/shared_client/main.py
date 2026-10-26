"""python -m examples.shared_client.main"""

from industrial_connect.db import SqliteClient, SqliteConfig

from .report import readings
from .repository import save_reading


def main():
    with SqliteClient(SqliteConfig(database=":memory:")) as database:
        database.execute("create table readings(value real)")
        save_reading(database, 1.5)
        print(readings(database))


if __name__ == "__main__":
    main()
